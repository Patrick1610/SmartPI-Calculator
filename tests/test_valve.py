"""Number-only writes, complementary endpoints, bounded recovery and learning gates."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.smartpi_calculator.const import (
    CONF_CLOSING,
    CONF_MIN_OPENING,
    CONF_MODE,
    CONF_OPENING,
    MODE_DIRECT,
    MODE_DISABLED,
    MODE_EXTERNAL,
    valve_minima,
)
from custom_components.smartpi_calculator.coordinator import CalculatorCoordinator
from custom_components.smartpi_calculator.valve import ValveController, valve_bindings

PAIR = ("number.opening", "number.closing")
SETTINGS = {CONF_MODE: MODE_DIRECT, CONF_OPENING: [PAIR[0]], CONF_CLOSING: [PAIR[1]]}
ATTRS = {"min": 0, "max": 100, "step": 1}


@pytest.fixture
async def rig(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = Mock()
    hass.config_entries.async_entries.return_value = []
    hass.states.async_set(PAIR[0], "0", ATTRS)
    hass.states.async_set(PAIR[1], "100", ATTRS)
    calls = []

    async def echo(call):
        calls.append((call.data["entity_id"], call.data["value"]))
        hass.states.async_set(call.data["entity_id"], call.data["value"], ATTRS)

    hass.services.async_register("number", "set_value", echo)
    yield hass, calls
    await hass.async_stop()


async def test_complement_rounding_and_safe_write_order(rig):
    hass, calls = rig
    pairs, grid = valve_bindings(hass, SETTINGS)
    controller = ValveController(hass, pairs, grid)
    assert await controller.apply(37.6)
    assert calls == [(PAIR[0], 38), (PAIR[1], 62)]
    assert controller.confirmed()
    # Normal writes are rate limited, not sent every calculation tick.
    calls.clear()
    assert await controller.apply(60)
    assert controller.prepare(60) == 38
    assert calls == []
    # Closing bypasses the normal interval and expands closing before opening.
    assert await controller.close()
    assert calls == [(PAIR[1], 100), (PAIR[0], 0)]


async def test_retry_requires_interval_and_echo(rig, monkeypatch):
    hass, _ = rig
    now = [100.0]
    monkeypatch.setattr(
        "custom_components.smartpi_calculator.valve.time.monotonic", lambda: now[0]
    )
    calls = []

    async def no_echo(call):
        calls.append(dict(call.data))

    hass.services.async_register("number", "set_value", no_echo)
    controller = ValveController(hass, [PAIR], 1)
    assert not await controller.apply(50)
    assert not controller.confirmed()
    assert len(calls) == 2
    assert not await controller.apply(50)
    assert len(calls) == 2
    now[0] += 60
    assert not await controller.apply(50)
    assert len(calls) == 4
    for entity, value in ((PAIR[0], 50), (PAIR[1], 50)):
        hass.states.async_set(entity, value, ATTRS)
    assert await controller.apply(50)
    assert len(calls) == 4


async def test_failed_endpoint_does_not_continue_normal_opening(rig):
    hass, _ = rig
    calls = []

    async def failure(call):
        calls.append(call.data["entity_id"])
        raise RuntimeError("Simulated transport failure")

    hass.services.async_register("number", "set_value", failure)
    controller = ValveController(hass, [PAIR], 1)
    assert not await controller.apply(50)
    assert calls == [PAIR[0]]
    assert controller.status == "failed"


async def test_hung_service_is_bounded_and_leaves_command_unconfirmed(rig, monkeypatch):
    hass, _ = rig
    monkeypatch.setattr("custom_components.smartpi_calculator.valve.CALL_TIMEOUT", 0.01)

    async def hang(call):
        await asyncio.Event().wait()

    hass.services.async_register("number", "set_value", hang)
    controller = ValveController(hass, [PAIR], 1)
    async with asyncio.timeout(1):
        assert not await controller.apply(50)
    assert not controller.confirmed()


async def test_new_binding_does_not_restore_a_different_actuator_model(rig):
    hass, _ = rig
    entry = Mock(
        data={
            **SETTINGS,
            "temperature_entity": "sensor.room",
            "target_entity": "sensor.target",
            "outdoor_entity": "sensor.outside",
        },
        options={},
        title="Test",
        entry_id="test",
    )
    coordinator = CalculatorCoordinator(hass, entry)
    coordinator.store = Mock(async_load=AsyncMock(return_value=None))
    await coordinator.initialize()
    coordinator.engine.algo.ctl.integral = 1.25
    stored = coordinator._storage_data()
    coordinator.store.async_load.return_value = stored
    await coordinator.initialize()
    assert coordinator.engine.algo.ctl.integral == 1.25
    stored["application"] = [MODE_EXTERNAL, [], []]
    await coordinator.initialize()
    assert coordinator.engine.algo.ctl.integral != 1.25


async def test_unavailable_endpoint_closes_reachable_counterpart(rig):
    hass, calls = rig
    hass.states.async_set(PAIR[0], "unavailable", ATTRS)
    hass.states.async_set(PAIR[1], "30", ATTRS)
    controller = ValveController(hass, [PAIR], 1)
    assert not await controller.apply(70)
    assert calls == [(PAIR[1], 100)]
    assert controller.status == "unavailable"
    assert not controller.confirmed()


async def test_pair_validation_and_shared_grid(rig):
    hass, _ = rig
    hass.states.async_set(PAIR[0], "0", {**ATTRS, "step": 0.5})
    hass.states.async_set(PAIR[1], "100", {**ATTRS, "step": 2})
    assert valve_bindings(hass, SETTINGS) == ([PAIR], 2)
    with pytest.raises(ValueError, match="valve_is_source"):
        valve_bindings(hass, {**SETTINGS, "target_entity": PAIR[0]})
    with pytest.raises(ValueError, match="invalid_pairs"):
        valve_bindings(hass, {**SETTINGS, CONF_CLOSING: []})
    with pytest.raises(ValueError, match="duplicate_valves"):
        valve_bindings(hass, {**SETTINGS, CONF_CLOSING: [PAIR[0]]})
    hass.states.async_set(PAIR[1], "100", {**ATTRS, "max": 255})
    with pytest.raises(ValueError, match="invalid_valve_range"):
        valve_bindings(hass, SETTINGS)
    other = Mock(entry_id="other", data=SETTINGS, options={})
    hass.config_entries.async_entries.return_value = [other]
    with pytest.raises(ValueError, match="valve_in_use"):
        valve_bindings(hass, SETTINGS)


async def test_multiple_pairs_receive_same_supported_command(rig):
    hass, calls = rig
    pairs = [PAIR, ("number.opening2", "number.closing2")]
    for entity, value in zip(pairs[1], (0, 100)):
        hass.states.async_set(entity, value, ATTRS)
    controller = ValveController(hass, pairs, 1)
    assert await controller.apply(41)
    assert set(calls) == {(pair[0], 41) for pair in pairs} | {
        (pair[1], 59) for pair in pairs
    }


@pytest.mark.parametrize(
    "demand,opening", [(0, 0), (5, 34), (17, 42), (24, 47), (100, 100)]
)
async def test_minimum_rescales_positive_demand_and_zero_still_closes(
    rig, demand, opening
):
    hass, _ = rig
    controller = ValveController(hass, [PAIR], 1, {PAIR[0]: 30})
    assert await controller.apply(demand)
    assert float(hass.states.get(PAIR[0]).state) == opening
    assert float(hass.states.get(PAIR[1]).state) == 100 - opening
    assert controller.prepare(demand) == demand
    assert await controller.close(terminal=True)
    assert float(hass.states.get(PAIR[0]).state) == 0
    assert float(hass.states.get(PAIR[1]).state) == 100


async def test_per_radiator_mapping_and_rate_hold_stay_in_normalized_space(
    rig, monkeypatch
):
    hass, calls = rig
    clock = [1000.0]
    monkeypatch.setattr(
        "custom_components.smartpi_calculator.valve.time.monotonic", lambda: clock[0]
    )
    pair2 = ("number.opening2", "number.closing2")
    for entity, value in zip(pair2, (0, 100), strict=True):
        hass.states.async_set(entity, value, ATTRS)
    controller = ValveController(hass, [PAIR, pair2], 1, {PAIR[0]: 30, pair2[0]: 50})
    assert await controller.apply(5)
    assert float(hass.states.get(PAIR[0]).state) == 34
    assert float(hass.states.get(pair2[0]).state) == 53
    assert controller.prepare(17) == 5  # never 34 or 53
    assert await controller.apply(17)
    assert len(calls) == 4
    clock[0] += 60
    assert controller.prepare(17) == 17
    assert await controller.apply(17)
    assert float(hass.states.get(PAIR[0]).state) == 42
    assert float(hass.states.get(pair2[0]).state) == 59
    # Never map 42 again: it is the physical command, not 17% PI demand.
    assert controller._demand == 17
    assert await controller.apply(0)
    assert controller.confirmed()


@pytest.mark.parametrize("step,minimum", [(1, 30.1), (2, 31), (0.5, 99.1)])
async def test_mapped_endpoints_are_supported_and_never_below_minimum(
    rig, step, minimum
):
    hass, _ = rig
    controller = ValveController(hass, [PAIR], step, {PAIR[0]: minimum})
    for demand in range(1, 101):
        opening = controller.opening_for(PAIR, demand)
        assert minimum <= opening <= 100
        assert opening / step == pytest.approx(round(opening / step))
        assert (100 - opening) / step == pytest.approx(round((100 - opening) / step))
    assert controller.opening_for(PAIR, 0) == 0


@pytest.mark.parametrize(
    "minimum", [None, True, -0.1, 100, float("inf"), float("nan"), "bad"]
)
def test_invalid_minimum_is_rejected(minimum):
    with pytest.raises(ValueError, match="invalid_minimum_opening"):
        valve_minima({**SETTINGS, CONF_MIN_OPENING: {PAIR[0]: minimum}})


@pytest.mark.parametrize("minima", [[], None, {"number.other": 30}])
def test_invalid_mapping_is_rejected(minima):
    with pytest.raises(ValueError, match="invalid_minimum_opening"):
        valve_minima({**SETTINGS, CONF_MIN_OPENING: minima})


@pytest.mark.parametrize("minimum", [0, 30])
async def test_minimum_is_part_of_persisted_model_binding(rig, minimum):
    hass, _ = rig
    settings = {
        **SETTINGS,
        CONF_MIN_OPENING: {PAIR[0]: minimum},
        "temperature_entity": "sensor.room",
        "target_entity": "sensor.target",
        "outdoor_entity": "sensor.outside",
    }
    coordinator = CalculatorCoordinator(
        hass, Mock(data=settings, options={}, title="Test", entry_id="test")
    )
    coordinator.store = Mock(async_load=AsyncMock(return_value=None))
    await coordinator.initialize()
    coordinator.engine.algo.ctl.integral = 1.25
    stored = json.loads(json.dumps(coordinator._storage_data()))
    if minimum == 0:
        assert stored["application"] == [MODE_DIRECT, [PAIR[0]], [PAIR[1]]]
        coordinator.settings.pop(CONF_MIN_OPENING)  # legacy identity model
    coordinator.store.async_load.return_value = stored
    await coordinator.initialize()
    assert coordinator.engine.algo.ctl.integral == 1.25
    coordinator.settings[CONF_MIN_OPENING] = {PAIR[0]: minimum + 10}
    await coordinator.initialize()
    assert coordinator.engine.algo.ctl.integral != 1.25


async def test_mapped_echoes_gate_learning_and_diagnostics_redact_entity_keys(rig):
    from custom_components.smartpi_calculator.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    hass, _ = rig
    settings = {
        **SETTINGS,
        CONF_MIN_OPENING: {PAIR[0]: 30},
        "temperature_entity": "sensor.room",
        "target_entity": "sensor.target",
        "outdoor_entity": "sensor.outside",
    }
    coordinator = CalculatorCoordinator(
        hass, Mock(data=settings, options={}, title="Test", entry_id="test")
    )
    coordinator.store = Mock(async_load=AsyncMock(return_value=None))
    await coordinator.initialize()
    controller = coordinator.valves
    assert await controller.apply(5)
    assert controller.confirmed()
    hass.states.async_set(PAIR[1], 95, ATTRS)  # normalized complement is NOT an echo
    assert not controller.confirmed()
    assert not coordinator.model_diagnostics()["learning_enabled"]
    hass.states.async_set(PAIR[1], 66, ATTRS)
    assert controller.confirmed()
    coordinator.entry.runtime_data = coordinator
    result = await async_get_config_entry_diagnostics(hass, coordinator.entry)
    for entity in (*PAIR, "sensor.room", "sensor.target", "sensor.outside"):
        assert entity not in str(result)
    assert result["output_space"] == "normalized_demand_percent"
    assert result["valve_commands"] == [
        {
            "minimum_opening_percent": 30,
            "opening_percent": 34,
            "closing_percent": 66,
        }
    ]
    # Loss of an endpoint still closes the other, ignoring the configured minimum.
    hass.states.async_set(PAIR[0], "unavailable", ATTRS)
    assert not await controller.apply(5)
    assert float(hass.states.get(PAIR[1]).state) == 100


async def test_real_pi_commits_normalized_demand_not_mapped_opening(rig, monkeypatch):
    from custom_components.smartpi_calculator.engine import Calculator, Inputs

    hass, _ = rig
    clock = [10000.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    controller = ValveController(hass, [PAIR], 1, {PAIR[0]: 30})
    engine = Calculator(hass, "Normalized PI")
    engine.algo.est.a_meas_hist.extend([0.05] * 6)
    engine.algo.est.b_meas_hist.extend([0.001] * 8)
    engine.algo.ctl.integral = 1.0
    output = await engine.step(Inputs(20, 20, 10), command_transform=controller.prepare)
    assert 0 < output < 100
    assert await controller.apply(output)
    opening = float(hass.states.get(PAIR[0]).state)
    assert opening > output
    assert engine._command == pytest.approx(output / 100)
    assert engine.algo.linear_committed_on_percent == pytest.approx(output / 100)
    clock[0] += 30
    engine.set_feedback(controller.confirmed())
    held = await engine.step(Inputs(20, 20, 10), command_transform=controller.prepare)
    assert held == output
    assert engine._command_integral == pytest.approx(30 * output / 100)
    assert await controller.apply(held)
    assert float(hass.states.get(PAIR[0]).state) == opening


async def test_explicit_zero_minimum_matches_legacy_commands(rig):
    hass, _ = rig
    legacy = ValveController(hass, [PAIR], 0.1)
    explicit = ValveController(hass, [PAIR], 0.1, {PAIR[0]: 0})
    for demand in range(101):
        assert explicit.opening_for(PAIR, demand) == legacy.opening_for(PAIR, demand)


@pytest.mark.parametrize(
    "divisions",
    [
        1,
        2,
        4,
        5,
        8,
        10,
        16,
        20,
        25,
        32,
        40,
        50,
        64,
        100,
        125,
        128,
        200,
        250,
        256,
        500,
        1000,
    ],
)
async def test_mapping_invariants_across_supported_grids(rig, divisions):
    hass, _ = rig
    for entity in PAIR:
        hass.states.async_set(entity, "100", {**ATTRS, "step": 100 / divisions})
    pairs, grid = valve_bindings(hass, SETTINGS)
    for minimum in (0, 0.1, 17.3, 30, 99.9):
        controller = ValveController(hass, pairs, grid, {PAIR[0]: minimum})
        previous = 0
        for demand in range(1, 101):
            opening = controller.opening_for(PAIR, demand)
            assert minimum <= opening <= 100
            assert opening >= previous
            assert opening / grid == pytest.approx(round(opening / grid))
            assert (100 - opening) / grid == pytest.approx(
                round((100 - opening) / grid)
            )
            previous = opening
        assert controller.opening_for(PAIR, 0) == 0
        assert controller.opening_for(PAIR, 100) == 100


@pytest.mark.parametrize("mode", [MODE_DISABLED, MODE_EXTERNAL, MODE_DIRECT])
async def test_coordinator_gate_bad_inputs_and_shutdown(rig, mode):
    hass, calls = rig
    settings = {
        **SETTINGS,
        CONF_MODE: mode,
        "temperature_entity": "sensor.room",
        "target_entity": "sensor.target",
        "outdoor_entity": "sensor.outside",
    }
    for key, value in (("room", 19), ("target", 20), ("outside", 10)):
        hass.states.async_set(f"sensor.{key}", value, {"unit_of_measurement": "°C"})
    entry = Mock(
        data=settings,
        options={},
        title="Test",
        entry_id="test",
        pref_disable_polling=False,
    )
    coordinator = CalculatorCoordinator(hass, entry)
    coordinator.store = Mock(
        async_load=AsyncMock(return_value=None), async_save=AsyncMock()
    )
    await coordinator.initialize()
    assert (
        not coordinator.engine.algo.learning_enabled
        if mode != MODE_EXTERNAL
        else coordinator.engine.algo.learning_enabled
    )
    assert await coordinator._async_update_data() == 100
    if mode == MODE_DIRECT:
        assert calls == [(PAIR[0], 100), (PAIR[1], 0)]
        # Initial application must be confirmed before the following learning interval.
        assert not coordinator.engine.algo.learning_enabled
        await coordinator._async_update_data()
        assert coordinator.engine.algo.learning_enabled
    else:
        assert calls == []
    hass.states.async_set("sensor.room", "unavailable")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    if mode == MODE_DIRECT:
        assert calls[-2:] == [(PAIR[1], 100), (PAIR[0], 0)]
        assert not coordinator.engine.algo.learning_enabled
    else:
        assert calls == []
    await coordinator.shutdown()
    coordinator.store.async_save.assert_awaited_once()


async def test_refresh_cannot_reopen_after_concurrent_shutdown(rig):
    hass, calls = rig
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_echo(call):
        calls.append((call.data["entity_id"], call.data["value"]))
        if call.data["value"] == 100 and call.data["entity_id"] == PAIR[0]:
            entered.set()
            await release.wait()
        hass.states.async_set(call.data["entity_id"], call.data["value"], ATTRS)

    hass.services.async_register("number", "set_value", delayed_echo)
    settings = {
        **SETTINGS,
        "temperature_entity": "sensor.room",
        "target_entity": "sensor.target",
        "outdoor_entity": "sensor.outside",
    }
    for key, value in (("room", 19), ("target", 20), ("outside", 10)):
        hass.states.async_set(f"sensor.{key}", value, {"unit_of_measurement": "°C"})
    entry = Mock(data=settings, options={}, title="Test", entry_id="test")
    coordinator = CalculatorCoordinator(hass, entry)
    coordinator.store = Mock(
        async_load=AsyncMock(return_value=None), async_save=AsyncMock()
    )
    await coordinator.initialize()
    refresh = asyncio.create_task(coordinator._async_update_data())
    await entered.wait()
    stopping = asyncio.create_task(coordinator.shutdown())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(refresh, stopping)
    assert calls[-2:] == [(PAIR[1], 100), (PAIR[0], 0)]
    assert not await coordinator.valves.apply(100)
    with pytest.raises(UpdateFailed, match="stopping"):
        await coordinator._async_update_data()
    assert coordinator.valves.confirmed()


async def test_terminal_close_retries_a_recent_unconfirmed_close(rig):
    hass, calls = rig
    hass.states.async_set(PAIR[0], "50", ATTRS)
    hass.states.async_set(PAIR[1], "50", ATTRS)

    async def no_echo(call):
        calls.append((call.data["entity_id"], call.data["value"]))

    hass.services.async_register("number", "set_value", no_echo)
    controller = ValveController(hass, [PAIR], 1)
    assert not await controller.close()
    count = len(calls)
    assert not await controller.close(terminal=True)
    assert len(calls) == count + 2


async def test_direct_learning_survives_delayed_echoes_and_changing_output(
    rig, monkeypatch
):
    """Synthetic plant, not measured radiator performance; echoes land after service returns."""
    from custom_components.smartpi_calculator.engine import Calculator, Inputs

    hass, calls = rig
    clock = [10000.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    pending = []

    async def delayed_echo(call):
        pending.append((call.data["entity_id"], call.data["value"]))
        calls.append((call.data["entity_id"], call.data["value"]))

    hass.services.async_register("number", "set_value", delayed_echo)
    controller = ValveController(hass, [PAIR], 1)
    engine = Calculator(hass, "Synthetic direct")
    room = 19.0
    outputs = set()
    for tick in range(24 * 120):
        engine.set_feedback(controller.confirmed())
        target = 20 if tick < 12 * 120 else 17
        output = await engine.step(
            Inputs(room, target, 10, tick), command_transform=controller.prepare
        )
        await controller.apply(output)
        if controller.status in ("failed", "unavailable"):
            engine.set_feedback(False)
        outputs.add(output)
        for entity, value in pending:
            hass.states.async_set(entity, value, ATTRS)
        pending.clear()
        room += 0.5 * (0.06 * output / 100 - 0.003 * (room - 10))
        clock[0] += 30
    assert engine.algo.cycles_since_reset > 100
    assert engine.algo.meas_count_a > 0
    assert engine.algo.meas_count_b > 0
    assert len(outputs) > 10
