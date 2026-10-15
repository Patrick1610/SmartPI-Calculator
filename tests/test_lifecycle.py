"""Load/unload the real integration in an isolated, temporary HA instance."""

import asyncio
from pathlib import Path

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry, frame

from custom_components.smartpi_calculator.const import DOMAIN


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
            assert len(states) == 1
            entity_id = states[0].entity_id
            assert float(states[0].state) == 100
            hass.states.async_set("sensor.room", "unavailable")
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert hass.states.get(entity_id).state == "unavailable"
            hass.states.async_set("sensor.room", "19", {"unit_of_measurement": "°C"})
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert float(hass.states.get(entity_id).state) == 100
            entry.runtime_data.engine.algo.ctl.integral = 1.25
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.runtime_data.engine.algo.ctl.integral == 1.25
            assert not entry.runtime_data.engine.algo.learning_enabled
            assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await hass.async_stop()
