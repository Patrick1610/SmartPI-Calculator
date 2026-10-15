"""Exercise real Home Assistant State objects and source conversion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.smartpi_calculator.const import (
    CONF_OUTDOOR,
    CONF_TARGET,
    CONF_TEMPERATURE,
)
from custom_components.smartpi_calculator.coordinator import CalculatorCoordinator


@pytest.fixture
async def host(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        title="Test",
        entry_id="test-entry",
        options={},
        async_on_unload=Mock(),
        data={
            CONF_TEMPERATURE: "sensor.room",
            CONF_TARGET: "sensor.target",
            CONF_OUTDOOR: "sensor.outside",
        },
    )
    coordinator = CalculatorCoordinator(hass, entry)
    coordinator.store = Mock(
        async_load=AsyncMock(return_value=None), async_save=AsyncMock()
    )
    await coordinator.initialize()
    hass.states.async_set("sensor.room", "19", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.target", "20", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.outside", "10", {"unit_of_measurement": "°C"})
    yield hass, coordinator
    await hass.async_stop()


async def test_three_sources_compute(host):
    _, coordinator = host
    assert await coordinator._async_update_data() == 100
    assert coordinator.inputs.temperature == 19
    assert coordinator.inputs.target == 20


@pytest.mark.parametrize("value", ["unknown", "unavailable", "nan", "inf", "bad"])
async def test_invalid_source_is_not_a_zero_temperature(host, value):
    hass, coordinator = host
    await coordinator._async_update_data()
    hass.states.async_set("sensor.room", value)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.inputs is None
    assert coordinator.engine._cycle_start is None


async def test_fahrenheit_source(host):
    hass, coordinator = host
    hass.states.async_set("sensor.room", "68", {"unit_of_measurement": "°F"})
    await coordinator._async_update_data()
    assert coordinator.inputs.temperature == pytest.approx(20)


async def test_wrong_unit_fails_closed(host):
    hass, coordinator = host
    hass.states.async_set("sensor.room", "19", {"unit_of_measurement": "%"})
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_source_change_does_not_restore_wrong_room_model(host):
    _, coordinator = host
    coordinator.store.async_load.return_value = {
        "sources": ["sensor.other", "sensor.outside"],
        "algorithm": {"ctl": {"integral": 99}},
    }
    await coordinator.initialize()
    assert coordinator.engine.algo.ctl.integral != 99


async def test_store_and_sensor_diagnostics(host):
    _, coordinator = host
    await coordinator._async_update_data()
    await coordinator.save()
    payload = coordinator.store.async_save.call_args.args[0]
    assert payload["sources"] == ["sensor.room", "sensor.outside"]
    assert payload["algorithm"]["version"] == 2


async def test_unexpected_calculation_failure_invalidates_partial_cycle(host):
    _, coordinator = host
    await coordinator._async_update_data()
    coordinator.engine.step = AsyncMock(
        side_effect=RuntimeError("synthetic core failure")
    )
    with pytest.raises(UpdateFailed, match="synthetic core failure"):
        await coordinator._async_update_data()
    assert coordinator.inputs is None
    assert coordinator.engine._cycle_start is None


async def test_diagnostics_redact_source_ids(host):
    from custom_components.smartpi_calculator.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    hass, coordinator = host
    coordinator.entry.runtime_data = coordinator
    await coordinator._async_update_data()
    result = await async_get_config_entry_diagnostics(hass, coordinator.entry)
    assert "sensor.room" not in str(result)
    assert "sensor.target" not in str(result)
    assert "sensor.outside" not in str(result)


async def test_coordinator_uses_options_margins_and_retains_model_binding(host):
    from custom_components.smartpi_calculator.const import (
        CONF_HYSTERESIS_OFF,
        CONF_HYSTERESIS_ON,
    )

    _, coordinator = host
    coordinator.engine.algo.ctl.integral = 1.25
    saved = coordinator._storage_data()
    coordinator.store.async_load.return_value = saved
    coordinator.settings.update({CONF_HYSTERESIS_ON: 0.2, CONF_HYSTERESIS_OFF: 0})
    await coordinator.initialize()
    assert coordinator.engine.algo._hyst_on == 0.2
    assert coordinator.engine.algo._hyst_off == 0
    assert coordinator.engine.algo.ctl.integral == 1.25


async def test_coordinator_rejects_invalid_stored_margins(host):
    from homeassistant.exceptions import ConfigEntryError

    _, coordinator = host
    coordinator.settings.update(hysteresis_on=0, hysteresis_off=0)
    with pytest.raises(ConfigEntryError, match="hysteresis"):
        await coordinator.initialize()
