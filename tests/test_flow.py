"""Configuration schemas use real HA selectors and the installed flow API."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.smartpi_calculator.config_flow import (
    ConfigFlow,
    OptionsFlow,
    schema,
)
from custom_components.smartpi_calculator.const import (
    CONF_CLOSING,
    CONF_MODE,
    CONF_OPENING,
    CONF_OUTDOOR,
    CONF_TARGET,
    CONF_TEMPERATURE,
    MODE_DIRECT,
    MODE_DISABLED,
    MODE_EXTERNAL,
)

SOURCES = {
    CONF_TEMPERATURE: "sensor.room",
    CONF_TARGET: "sensor.target",
    CONF_OUTDOOR: "sensor.outside",
}


def test_schema_has_safe_observer_default():
    assert schema({}, options=True)(SOURCES)[CONF_MODE] == MODE_DISABLED
    assert (
        schema({}, options=True)({**SOURCES, CONF_MODE: MODE_EXTERNAL})[CONF_MODE]
        == MODE_EXTERNAL
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
    assert created["data"] == {
        "name": "Living Room",
        **SOURCES,
        CONF_MODE: MODE_DISABLED,
    }


async def test_options_form_and_explicit_learning_optin(tmp_path):
    flow = OptionsFlow()
    flow.handler = "entry"
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    flow.hass.config_entries.async_entries.return_value = []
    entry = Mock(data=SOURCES, options={})
    flow.hass.config_entries.async_get_known_entry.return_value = entry
    form = await flow.async_step_init()
    assert form["data_schema"](SOURCES)[CONF_MODE] == MODE_DISABLED
    saved = await flow.async_step_init({**SOURCES, CONF_MODE: MODE_EXTERNAL})
    assert saved["data"][CONF_MODE] == MODE_EXTERNAL
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


async def test_direct_flow_requires_two_valid_lists_before_creating_entry(tmp_path):
    flow = ConfigFlow()
    flow.context = {"source": "user"}
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    flow.hass.config_entries.async_entries.return_value = []
    flow.hass.config_entries.flow.async_progress_by_handler.return_value = []
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
    step = await flow.async_step_user(
        {"name": "Bathroom", **SOURCES, CONF_MODE: MODE_DIRECT}
    )
    assert step["step_id"] == "valves"
    assert len(step["data_schema"].schema) == 2
    bad = await flow.async_step_valves({CONF_OPENING: [], CONF_CLOSING: []})
    assert bad["errors"]["base"] == "invalid_pairs"
    for key in ("opening", "closing"):
        flow.hass.states.async_set(
            "number." + key, "100", {"min": 0, "max": 100, "step": 1}
        )
    result = await flow.async_step_valves(
        {CONF_OPENING: ["number.opening"], CONF_CLOSING: ["number.closing"]}
    )
    assert result["data"][CONF_MODE] == MODE_DIRECT
    assert result["data"][CONF_OPENING] == ["number.opening"]
    await flow.hass.async_stop()


@pytest.mark.parametrize(
    "legacy,expected", [(False, MODE_DISABLED), (True, MODE_EXTERNAL)]
)
async def test_legacy_migration_never_enables_direct_control(legacy, expected):
    from custom_components.smartpi_calculator import async_migrate_entry

    entry = SimpleNamespace(version=1, data=SOURCES, options={"output_applied": legacy})
    hass = Mock()

    def update(target, **values):
        target.__dict__.update(values)

    hass.config_entries.async_update_entry.side_effect = update
    assert await async_migrate_entry(hass, entry)
    assert entry.data[CONF_MODE] == expected
    assert "output_applied" not in entry.options
    assert entry.version == 2


async def test_switching_out_of_direct_clears_saved_valve_binding(tmp_path):
    flow = OptionsFlow()
    flow.handler = "entry"
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    current = Mock(
        entry_id="entry",
        data={
            **SOURCES,
            CONF_MODE: MODE_DIRECT,
            CONF_OPENING: ["number.opening"],
            CONF_CLOSING: ["number.closing"],
        },
        options={},
    )
    flow.hass.config_entries.async_get_known_entry.return_value = current
    flow.hass.config_entries.async_entries.return_value = [current]
    result = await flow.async_step_init({**SOURCES, CONF_MODE: MODE_DISABLED})
    assert result["data"][CONF_OPENING] == []
    assert result["data"][CONF_CLOSING] == []
    await flow.hass.async_stop()
