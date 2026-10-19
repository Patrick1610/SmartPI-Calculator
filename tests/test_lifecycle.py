"""Load/unload the real integration in an isolated, temporary HA instance."""

import asyncio
from pathlib import Path

import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry, frame

from custom_components.smartpi_calculator.const import (
    CONF_CLOSING,
    CONF_MIN_OPENING,
    CONF_MODE,
    CONF_OPENING,
    DOMAIN,
    MODE_DIRECT,
    MODE_DISABLED,
    MODE_EXTERNAL,
)


async def test_real_ha_setup_sensor_unavailable_recovery_and_unload(tmp_path):
    (tmp_path / "custom_components").symlink_to(
        Path(__file__).resolve().parents[1] / "custom_components"
    )
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    frame.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    device_registry.async_setup(hass)
    await device_registry.async_load(hass, load_empty=True)
    await entity_registry.async_load(hass, load_empty=True)
    for name, value in (("room", "19"), ("target", "20"), ("outside", "10")):
        hass.states.async_set(f"sensor.{name}", value, {"unit_of_measurement": "°C"})
    entry = ConfigEntry(
        domain=DOMAIN,
        title="Isolated test",
        version=1,
        minor_version=1,
        source="user",
        unique_id="sensor.room",
        options={},
        discovery_keys={},
        subentries_data=[],
        data={
            "temperature_entity": "sensor.room",
            "target_entity": "sensor.target",
            "outdoor_entity": "sensor.outside",
        },
    )
    try:
        async with asyncio.timeout(15):
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            states = [
                s
                for s in hass.states.async_all("sensor")
                if s.entity_id not in ("sensor.room", "sensor.target", "sensor.outside")
            ]
            assert len(states) == 14
            registry = entity_registry.async_get(hass)

            def state(key):
                return hass.states.get(
                    registry.async_get_entity_id(
                        "sensor", DOMAIN, f"{entry.entry_id}_{key}"
                    )
                )

            entity_id = state("demand").entity_id
            assert float(state("demand").state) == 100
            assert float(state("used_room_temperature").state) == 19
            assert float(state("used_target_temperature").state) == 20
            assert float(state("used_outdoor_temperature").state) == 10
            assert state("control_phase").state == "Hysteresis"
            assert state("learning_status").state == "observing"
            for key in (
                "base_contribution",
                "proportional_contribution",
                "integral_contribution",
                "predictive_contribution",
            ):
                assert state(key).state == "unknown"
                assert state(key).attributes["unit_of_measurement"] == "%"
                assert key not in state("demand").attributes
            assert state("valve_control_status").state == "not_configured"
            assert state("learned_heating_samples").state == "0"
            assert state("learned_cooling_samples").state == "0"
            assert not {
                "phase",
                "learning_enabled",
                "learned_heating_samples",
                "learned_cooling_samples",
                "last_learning_result",
                "valve_status",
                "input_temperatures_c",
            }.intersection(state("demand").attributes)
            assert not {
                "learned_heating_samples",
                "learned_cooling_samples",
            }.intersection(state("learning_status").attributes)
            assert (
                state("last_learning_result").state
                == entry.runtime_data.engine.algo.learn_last_reason
            )
            assert (
                state("learning_status").attributes["feedback"] == "virtual_not_applied"
            )
            hass.states.async_set("sensor.room", "unavailable")
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert hass.states.get(entity_id).state == "unavailable"
            for previous in states:
                expected = (
                    "not_configured"
                    if previous.entity_id == state("valve_control_status").entity_id
                    else "unavailable"
                )
                assert hass.states.get(previous.entity_id).state == expected
            hass.states.async_set("sensor.room", "19", {"unit_of_measurement": "°C"})
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert float(hass.states.get(entity_id).state) == 100
            assert state("learning_status").state == "observing"
            # Input entities reflect the same latest successful calculation.
            hass.states.async_set("sensor.target", "21", {"unit_of_measurement": "°C"})
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert float(state("used_target_temperature").state) == 21
            entry.runtime_data.mode = MODE_EXTERNAL
            entry.runtime_data.async_set_updated_data(entry.runtime_data.data)
            await hass.async_block_till_done()
            assert state("learning_status").state == "enabled"
            entry.runtime_data.mode = MODE_DISABLED
            entry.runtime_data.engine.algo.ctl.integral = 1.25
            devices = device_registry.async_get(hass)
            device = devices.async_get_device_by_identifier(
                (DOMAIN, entry.entry_id), entry.entry_id
            )
            devices.async_update_device(
                device.id,
                manufacturer="Unofficial derivative of KipK / gael1980 SmartPI",
            )
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.runtime_data.engine.algo.ctl.integral == 1.25
            assert not entry.runtime_data.engine.algo.learning_enabled
            assert devices.async_get(device.id).manufacturer is None
            assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await hass.async_stop()


@pytest.mark.parametrize("before,after", [(0, 30), (30, 45), (30, 0)])
async def test_real_ha_minimum_options_reload_preserves_learned_state(
    tmp_path, before, after
):
    """Exercise the real Options listener, unload/save, setup/load and sensors."""
    (tmp_path / "custom_components").symlink_to(
        Path(__file__).resolve().parents[1] / "custom_components"
    )
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    frame.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    device_registry.async_setup(hass)
    await device_registry.async_load(hass, load_empty=True)
    await entity_registry.async_load(hass, load_empty=True)
    for name, value in (("room", "20"), ("target", "20"), ("outside", "10")):
        hass.states.async_set(f"sensor.{name}", value, {"unit_of_measurement": "°C"})
    opening, closing = "number.opening", "number.closing"
    attrs = {"min": 0, "max": 100, "step": 1}
    hass.states.async_set(opening, 0, attrs)
    hass.states.async_set(closing, 100, attrs)

    async def echo(call):
        hass.states.async_set(call.data["entity_id"], call.data["value"], attrs)

    hass.services.async_register("number", "set_value", echo)
    entry = ConfigEntry(
        domain=DOMAIN,
        title="Isolated direct test",
        version=2,
        minor_version=1,
        source="user",
        unique_id="sensor.room",
        options={CONF_MIN_OPENING: {opening: before}},
        discovery_keys={},
        subentries_data=[],
        data={
            "temperature_entity": "sensor.room",
            "target_entity": "sensor.target",
            "outdoor_entity": "sensor.outside",
            CONF_MODE: MODE_DIRECT,
            CONF_OPENING: [opening],
            CONF_CLOSING: [closing],
        },
    )
    try:
        async with asyncio.timeout(15):
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            previous = entry.runtime_data
            est = previous.engine.algo.est
            est.a_meas_hist.extend([0.02] * 8)
            est.b_meas_hist.extend([0.001] * 31)
            est.a, est.b = 0.02, 0.001
            previous.engine.algo.ctl.integral = 1.25
            hass.config_entries.async_update_entry(
                entry, options={CONF_MIN_OPENING: {opening: after}}
            )
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            current = entry.runtime_data
            assert current is not previous
            assert current.minima == {opening: after}
            assert current.engine.algo.meas_count_a == 8
            assert current.engine.algo.meas_count_b == 31
            assert current.engine.algo.a == 0.02
            assert current.engine.algo.b == 0.001
            assert current.engine.algo.ctl.integral == 1.25
            registry = entity_registry.async_get(hass)
            for key, expected in (
                ("learned_heating_samples", "8"),
                ("learned_cooling_samples", "31"),
                ("control_phase", "Stable"),
            ):
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{entry.entry_id}_{key}"
                )
                assert hass.states.get(entity_id).state == expected
            # Shutdown saves the new range with the same learned sample history.
            await hass.config_entries.async_unload(entry.entry_id)
            saved = await current.store.async_load()
            assert saved["application"] == current._application_binding()
            assert saved["algorithm"]["est_state"]["a_meas_hist"] == [0.02] * 8
            assert saved["algorithm"]["est_state"]["b_meas_hist"] == [0.001] * 31
    finally:
        await hass.async_stop()
