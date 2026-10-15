"""Configuration schemas use real HA selectors and the installed flow API."""

from unittest.mock import Mock

from homeassistant.core import HomeAssistant

from custom_components.smartpi_calculator.config_flow import (
    ConfigFlow,
    OptionsFlow,
    schema,
)
from custom_components.smartpi_calculator.const import (
    CONF_OUTDOOR,
    CONF_OUTPUT_APPLIED,
    CONF_TARGET,
    CONF_TEMPERATURE,
)

SOURCES = {
    CONF_TEMPERATURE: "sensor.room",
    CONF_TARGET: "sensor.target",
    CONF_OUTDOOR: "sensor.outside",
}


def test_schema_has_safe_observer_default():
    assert schema({}, options=True)(SOURCES)[CONF_OUTPUT_APPLIED] is False
    assert (
        schema({}, options=True)({**SOURCES, CONF_OUTPUT_APPLIED: True})[
            CONF_OUTPUT_APPLIED
        ]
        is True
    )


async def test_user_form_and_create(tmp_path):
    flow = ConfigFlow()
    flow.context = {"source": "user"}
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    flow.hass.config_entries.async_entries.return_value = []
    flow.hass.config_entries.flow.async_progress_by_handler.return_value = []
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
    form = await flow.async_step_user()
    assert form["step_id"] == "user"
    created = await flow.async_step_user({"name": "Living Room", **SOURCES})
    assert created["title"] == "Living Room"
    assert created["data"] == {"name": "Living Room", **SOURCES}


async def test_options_form_and_explicit_learning_optin(tmp_path):
    flow = OptionsFlow()
    flow.handler = "entry"
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    flow.hass.config_entries.async_entries.return_value = []
    entry = Mock(data=SOURCES, options={})
    flow.hass.config_entries.async_get_known_entry.return_value = entry
    form = await flow.async_step_init()
    assert form["data_schema"](SOURCES)[CONF_OUTPUT_APPLIED] is False
    saved = await flow.async_step_init({**SOURCES, CONF_OUTPUT_APPLIED: True})
    assert saved["data"][CONF_OUTPUT_APPLIED] is True
    flow.hass.config_entries.async_update_entry.assert_called_once_with(
        entry, unique_id="sensor.room"
    )


async def test_options_reject_duplicate_room(tmp_path):
    flow = OptionsFlow()
    flow.handler = "entry"
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    current = Mock(entry_id="current", data=SOURCES, options={})
    other = Mock(entry_id="other", data=SOURCES, options={})
    flow.hass.config_entries.async_get_known_entry.return_value = current
    flow.hass.config_entries.async_entries.return_value = [current, other]
    result = await flow.async_step_init(SOURCES)
    assert result["reason"] == "already_configured"
    flow.hass.config_entries.async_update_entry.assert_not_called()
