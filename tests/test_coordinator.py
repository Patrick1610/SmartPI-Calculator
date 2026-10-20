"""Exercise real Home Assistant State objects and source conversion."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.smartpi_calculator.const import (
    CONF_OUTDOOR,
    CONF_TARGET,
    CONF_TEMPERATURE,
    DOMAIN,
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


class StorageClock:
    """Advance only Store's timer; keep HA tasks and actual disk writes real."""

    def __init__(self, hass):
        self.hass = hass
        self.now = 0.0
        self.handles = []

    def time(self):
        return self.now

    def call_at(self, when, callback):
        handle = SimpleNamespace(cancelled=False, callback=callback)
        handle.when = lambda: when
        handle.cancel = lambda: setattr(handle, "cancelled", True)
        self.handles.append(handle)
        return handle

    async def advance(self, until):
        while pending := [
            h for h in self.handles if not h.cancelled and h.when() <= until
        ]:
            handle = min(pending, key=lambda h: h.when())
            handle.cancelled = True
            self.now = handle.when()
            handle.callback()
            await self.hass.async_block_till_done()
        self.now = until


class StorageHost:
    def __init__(self, hass, clock):
        self.hass = hass
        self.loop = clock

    def __getattr__(self, name):
        return getattr(self.hass, name)


def real_store_with_clock(hass, coordinator, monkeypatch):
    clock = StorageClock(hass)
    store = Store(hass, 1, f"{DOMAIN}.{coordinator.entry.entry_id}")
    store.hass = StorageHost(hass, clock)
    monkeypatch.setattr(
        "custom_components.smartpi_calculator.coordinator.monotonic", clock.time
    )
    coordinator.store = store
    return store, clock


async def test_real_store_writes_latest_samples_during_continuous_updates(
    host, monkeypatch
):
    hass, coordinator = host
    store, clock = real_store_with_clock(hass, coordinator, monkeypatch)
    est = coordinator.engine.algo.est
    est.a_meas_hist.extend([0.02] * 5)
    est.b_meas_hist.extend([0.001] * 24)
    storage_path = hass.config.path(".storage", store.key)

    # Ten minutes of ongoing 30-second calculations, not a quiet debounce window.
    # Capturing at write time must include a sample added after scheduling.
    for block in range(10):
        await coordinator._async_update_data()
        est.b_meas_hist.append(0.001 + block * 0.00001)
        assert len([h for h in clock.handles if not h.cancelled]) == 1
        await clock.advance(block * 60)
        payload = json.loads(Path(storage_path).read_text())["data"]
        assert payload["algorithm"]["est_state"]["a_meas_hist"] == [0.02] * 5
        assert payload["algorithm"]["est_state"]["b_meas_hist"] == list(est.b_meas_hist)
        assert payload["sources"] == ["sensor.room", "sensor.outside"]
        assert payload["application"] == coordinator._application_binding()

        await clock.advance(block * 60 + 30)
        await coordinator._async_update_data()
        assert not [h for h in clock.handles if not h.cancelled]
        await clock.advance(block * 60 + 60)

    # Read the written file through a fresh Store/coordinator, not HA's pending
    # in-memory Store data, and retain this existing entry's actual sample arrays.
    fresh = CalculatorCoordinator(hass, coordinator.entry)
    await fresh.initialize()
    assert fresh.entry.entry_id == coordinator.entry.entry_id
    assert list(fresh.engine.algo.est.a_meas_hist) == list(est.a_meas_hist)
    assert list(fresh.engine.algo.est.b_meas_hist) == list(est.b_meas_hist)


async def test_explicit_save_cancels_pending_and_periodic_saving_resumes(
    host, monkeypatch
):
    hass, coordinator = host
    store, clock = real_store_with_clock(hass, coordinator, monkeypatch)
    await coordinator._async_update_data()
    await coordinator.save()
    coordinator.engine.algo.est.a_meas_hist.append(0.03)
    await clock.advance(60)
    await coordinator._async_update_data()
    await clock.advance(60)
    data = json.loads(Path(hass.config.path(".storage", store.key)).read_text())["data"]
    assert data["algorithm"]["est_state"]["a_meas_hist"] == [0.03]
    assert not [h for h in clock.handles if not h.cancelled]


async def test_failed_real_store_snapshot_keeps_disk_and_next_request_retries(
    host, monkeypatch
):
    hass, coordinator = host
    store, clock = real_store_with_clock(hass, coordinator, monkeypatch)
    await coordinator.save()
    path = Path(hass.config.path(".storage", store.key))
    before = path.read_bytes()
    await coordinator._async_update_data()
    original = coordinator.engine.snapshot
    coordinator.engine.snapshot = Mock(side_effect=RuntimeError("snapshot failed"))
    with pytest.raises(RuntimeError, match="snapshot failed"):
        await store._async_callback_delayed_write()
    assert path.read_bytes() == before
    assert store._data is None
    coordinator.engine.snapshot = original
    coordinator.engine.algo.est.a_meas_hist.append(0.03)
    await clock.advance(60)
    await coordinator._async_update_data()
    await clock.advance(60)
    assert json.loads(path.read_text())["data"]["algorithm"]["est_state"][
        "a_meas_hist"
    ] == [0.03]


async def test_delayed_snapshot_does_not_change_pi_or_learning_state(host):
    _, coordinator = host
    await coordinator._async_update_data()
    coordinator.engine.algo.est.a_meas_hist.extend([0.02] * 8)
    coordinator.engine.algo.est.b_meas_hist.extend([0.001] * 31)
    coordinator.engine.algo.ctl.integral = 1.25
    before = {
        part: getattr(coordinator.engine.algo, part).save_state()
        for part in ("est", "ctl", "dt_est", "gain_scheduler", "_ff_trim")
    }
    callback = coordinator.store.async_delay_save.call_args.args[0]
    payload = callback()
    assert payload["algorithm"]["est_state"]["a_meas_hist"] == [0.02] * 8
    assert payload["algorithm"]["est_state"]["b_meas_hist"] == [0.001] * 31
    for part, expected in before.items():
        assert getattr(coordinator.engine.algo, part).save_state() == expected
    assert coordinator.engine.algo.ctl.integral == 1.25


async def test_scheduling_failure_does_not_disable_retry(host):
    _, coordinator = host
    coordinator.store.async_delay_save.side_effect = RuntimeError("schedule failed")
    with pytest.raises(RuntimeError, match="schedule failed"):
        await coordinator._async_update_data()
    assert coordinator._next_storage_save_request == 0
    coordinator.store.async_delay_save.side_effect = None
    await coordinator._async_update_data()
    assert coordinator.store.async_delay_save.call_count == 2


async def test_pending_write_survives_invalid_source_stretch(host, monkeypatch):
    hass, coordinator = host
    store, clock = real_store_with_clock(hass, coordinator, monkeypatch)
    coordinator.engine.algo.est.a_meas_hist.extend([0.02] * 8)
    await coordinator._async_update_data()
    hass.states.async_set("sensor.room", "unavailable")
    for _ in range(3):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
    await clock.advance(90)
    data = json.loads(Path(hass.config.path(".storage", store.key)).read_text())["data"]
    assert data["algorithm"]["est_state"]["a_meas_hist"] == [0.02] * 8
    hass.states.async_set("sensor.room", "19", {"unit_of_measurement": "°C"})
    await coordinator._async_update_data()
    await clock.advance(90)
    assert not [h for h in clock.handles if not h.cancelled]


async def test_real_store_final_write_captures_latest_when_ha_stops(host, monkeypatch):
    hass, coordinator = host
    store, _ = real_store_with_clock(hass, coordinator, monkeypatch)
    hass.state = CoreState.stopping
    try:
        await coordinator._async_update_data()
        coordinator.engine.algo.est.a_meas_hist.extend([0.02] * 8)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()
        data = json.loads(Path(hass.config.path(".storage", store.key)).read_text())[
            "data"
        ]
        assert data["algorithm"]["est_state"]["a_meas_hist"] == [0.02] * 8
    finally:
        hass.state = CoreState.not_running


async def test_shutdown_saves_latest_buffers_before_reload(host, monkeypatch):
    hass, coordinator = host
    store, clock = real_store_with_clock(hass, coordinator, monkeypatch)
    await coordinator._async_update_data()
    coordinator.engine.algo.est.a_meas_hist.extend([0.02] * 8)
    coordinator.engine.algo.est.b_meas_hist.extend([0.001] * 31)
    coordinator.engine.algo.ctl.integral = 1.25
    coordinator.valves = Mock(close=AsyncMock(return_value=True))
    await coordinator.shutdown()
    coordinator.valves.close.assert_awaited_once_with(terminal=True)
    await clock.advance(120)
    data = json.loads(Path(hass.config.path(".storage", store.key)).read_text())["data"]
    assert data["algorithm"]["est_state"]["a_meas_hist"] == [0.02] * 8
    assert data["algorithm"]["est_state"]["b_meas_hist"] == [0.001] * 31
    assert data["algorithm"]["ctl_state"]["integral"] == 1.25


async def test_v024_storage_envelope_round_trip_keeps_model(host, monkeypatch):
    hass, coordinator = host
    store, clock = real_store_with_clock(hass, coordinator, monkeypatch)
    coordinator.engine.algo.est.a_meas_hist.extend([0.02] * 8)
    coordinator.engine.algo.est.b_meas_hist.extend([0.001] * 31)
    coordinator.engine.algo.ctl.integral = 1.25
    # Literal unchanged 0.2.4 Store envelope and model binding; no schema migration.
    legacy = {
        "version": 1,
        "minor_version": 1,
        "key": store.key,
        "data": {
            "sources": ["sensor.room", "sensor.outside"],
            "upstream_commit": "6a131df56cf3cfe2974f78f55f33ce8a095076b3",
            "application": ["learning_disabled", [], []],
            "algorithm": coordinator.engine.snapshot(),
        },
    }
    path = Path(hass.config.path(".storage", store.key))
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(legacy))
    fresh = CalculatorCoordinator(hass, coordinator.entry)
    await fresh.initialize()
    fresh.store.hass = StorageHost(hass, clock)
    fresh.engine.step = AsyncMock(return_value=0)
    await fresh._async_update_data()
    await clock.advance(0)
    data = json.loads(path.read_text())["data"]
    assert data["sources"] == legacy["data"]["sources"]
    assert data["application"] == legacy["data"]["application"]
    assert data["algorithm"]["est_state"] == legacy["data"]["algorithm"]["est_state"]
    assert data["algorithm"]["ctl_state"]["integral"] == 1.25
