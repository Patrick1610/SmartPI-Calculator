"""Configuration schemas use real HA selectors and the installed flow API."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import probatio
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from custom_components.smartpi_calculator.config_flow import (
    ConfigFlow,
    OptionsFlow,
    schema,
)
from custom_components.smartpi_calculator.const import (
    CONF_CLOSING,
    CONF_HYSTERESIS_OFF,
    CONF_HYSTERESIS_ON,
    CONF_MIN_OPENING,
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
        CONF_HYSTERESIS_ON: 0.3,
        CONF_HYSTERESIS_OFF: 0.5,
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
    assert result["step_id"] == "valve_minima"
    result = await flow.async_step_valve_minima({CONF_MIN_OPENING: {}})
    assert result["data"][CONF_MODE] == MODE_DIRECT
    assert result["data"][CONF_OPENING] == ["number.opening"]
    assert result["data"][CONF_MIN_OPENING] == {"number.opening": 0}
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
            CONF_MIN_OPENING: {"number.opening": 30},
        },
        options={},
    )
    flow.hass.config_entries.async_get_known_entry.return_value = current
    flow.hass.config_entries.async_entries.return_value = [current]
    result = await flow.async_step_init({**SOURCES, CONF_MODE: MODE_DISABLED})
    assert result["data"][CONF_OPENING] == []
    assert result["data"][CONF_CLOSING] == []
    assert result["data"][CONF_MIN_OPENING] == {}
    await flow.hass.async_stop()


def test_hysteresis_form_defaults_and_zero_off():
    defaults = schema({}, options=True)(SOURCES)
    assert defaults[CONF_HYSTERESIS_ON] == 0.3
    assert defaults[CONF_HYSTERESIS_OFF] == 0.5
    selected = schema({CONF_HYSTERESIS_ON: 0.2, CONF_HYSTERESIS_OFF: 0}, options=True)(
        SOURCES
    )
    assert selected[CONF_HYSTERESIS_ON] == 0.2
    assert selected[CONF_HYSTERESIS_OFF] == 0


@pytest.mark.parametrize(
    "on,off",
    [
        (0, 0),
        (-0.2, 0),
        (0.2, -0.1),
        (float("nan"), 0),
        (0.2, float("inf")),
        (3, 0),
        (True, 0),
        (None, 0),
    ],
)
@pytest.mark.parametrize("options", [False, True])
async def test_invalid_hysteresis_never_creates_or_saves(tmp_path, on, off, options):
    flow = OptionsFlow() if options else ConfigFlow()
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    method = flow.async_step_init if options else flow.async_step_user
    result = await method(
        {"name": "Test", **SOURCES, CONF_HYSTERESIS_ON: on, CONF_HYSTERESIS_OFF: off}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_hysteresis"}
    serialized = probatio.to_field_list(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )
    json.loads(json.dumps(serialized, allow_nan=False))
    flow.hass.config_entries.async_update_entry.assert_not_called()
    await flow.hass.async_stop()


async def test_direct_options_keep_custom_hysteresis_through_valve_step(tmp_path):
    flow = OptionsFlow()
    flow.handler = "entry"
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    current = Mock(entry_id="entry", data=SOURCES, options={})
    flow.hass.config_entries.async_get_known_entry.return_value = current
    flow.hass.config_entries.async_entries.return_value = [current]
    selected = {
        **SOURCES,
        CONF_MODE: MODE_DIRECT,
        CONF_HYSTERESIS_ON: 0.2,
        CONF_HYSTERESIS_OFF: 0,
    }
    result = await flow.async_step_init(selected)
    assert result["step_id"] == "valves"
    for key in ("opening", "closing"):
        flow.hass.states.async_set(
            "number." + key, "100", {"min": 0, "max": 100, "step": 1}
        )
    minima = await flow.async_step_valves(
        {CONF_OPENING: ["number.opening"], CONF_CLOSING: ["number.closing"]}
    )
    assert minima["step_id"] == "valve_minima"
    saved = await flow.async_step_valve_minima(
        {CONF_MIN_OPENING: {"number.opening": 30}}
    )
    assert saved["data"][CONF_HYSTERESIS_ON] == 0.2
    assert saved["data"][CONF_HYSTERESIS_OFF] == 0
    assert saved["data"][CONF_MIN_OPENING] == {"number.opening": 30}
    await flow.hass.async_stop()


@pytest.mark.parametrize(
    "invalid", [True, None, -1, 100, float("nan"), float("inf"), "bad"]
)
@pytest.mark.parametrize("options", [False, True])
async def test_invalid_minima_redisplay_serializes_safely(tmp_path, invalid, options):
    flow = OptionsFlow() if options else ConfigFlow()
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    flow.hass.config_entries.async_entries.return_value = []
    if options:
        flow.handler = "entry"
        flow.hass.config_entries.async_get_known_entry.return_value = Mock(
            entry_id="entry", data=SOURCES, options={}
        )
    flow._pending = {
        **SOURCES,
        CONF_MODE: MODE_DIRECT,
        CONF_OPENING: ["number.opening"],
        CONF_CLOSING: ["number.closing"],
    }
    result = await flow.async_step_valve_minima(
        {CONF_MIN_OPENING: {"number.opening": invalid}}
    )
    assert result["errors"] == {"base": "invalid_minimum_opening"}
    serialized = probatio.to_field_list(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )
    json.loads(json.dumps(serialized, allow_nan=False))
    flow.hass.config_entries.async_update_entry.assert_not_called()
    await flow.hass.async_stop()


async def test_minima_options_bind_to_entity_not_selection_order(tmp_path):
    from custom_components.smartpi_calculator.config_flow import minima_schema

    flow = OptionsFlow()
    flow.handler = "entry"
    flow.hass = HomeAssistant(str(tmp_path))
    flow.hass.config_entries = Mock()
    old = {
        **SOURCES,
        CONF_MODE: MODE_DIRECT,
        CONF_OPENING: ["number.old", "number.keep"],
        CONF_CLOSING: ["number.cold", "number.ckeep"],
        CONF_MIN_OPENING: {"number.old": 40, "number.keep": 30},
    }
    current = Mock(entry_id="entry", data=old, options={})
    flow.hass.config_entries.async_get_known_entry.return_value = current
    flow.hass.config_entries.async_entries.return_value = [current]
    await flow.async_step_init({**SOURCES, CONF_MODE: MODE_DIRECT})
    for entity in ("number.new", "number.keep", "number.cnew", "number.ckeep"):
        flow.hass.states.async_set(
            entity,
            "100",
            {"min": 0, "max": 100, "step": 1, "friendly_name": entity + " label"},
        )
    result = await flow.async_step_valves(
        {
            CONF_OPENING: ["number.keep", "number.new"],
            CONF_CLOSING: ["number.ckeep", "number.cnew"],
        }
    )
    assert result["step_id"] == "valve_minima"
    validated = result["data_schema"]({})
    assert validated[CONF_MIN_OPENING] == {"number.keep": 30, "number.new": 0}
    selector = next(iter(minima_schema(flow.hass, flow._pending).schema.values()))
    fields = selector.config["fields"]
    assert fields["number.keep"]["label"] == "number.keep label"
    assert fields["number.new"]["default"] == 0
    # Binding is rechecked even if another instance claims it while the form is open.
    other = Mock(
        entry_id="other", data={**old, CONF_OPENING: ["number.new"]}, options={}
    )
    flow.hass.config_entries.async_entries.return_value = [current, other]
    rejected = await flow.async_step_valve_minima(validated)
    assert rejected["errors"]["base"] == "valve_in_use"
    flow.hass.config_entries.async_entries.return_value = [current]
    saved = await flow.async_step_valve_minima(validated)
    assert saved["data"][CONF_MIN_OPENING] == {"number.keep": 30, "number.new": 0}
    await flow.hass.async_stop()


async def test_minima_error_preserves_other_valid_fields(tmp_path):
    from custom_components.smartpi_calculator.config_flow import minima_schema

    hass = HomeAssistant(str(tmp_path))
    settings = {
        CONF_OPENING: ["number.bad", "number.good"],
        CONF_MIN_OPENING: {"number.bad": float("nan"), "number.good": 31.7},
    }
    form = minima_schema(hass, settings)
    assert form({})[CONF_MIN_OPENING] == {"number.bad": 0, "number.good": 31.7}
    serialized = probatio.to_field_list(form, custom_serializer=cv.custom_serializer)
    json.loads(json.dumps(serialized, allow_nan=False))
    await hass.async_stop()
