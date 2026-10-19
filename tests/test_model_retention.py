"""Range tuning retains real model state, not a fabricated or loosened learner."""

import json
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.smartpi_calculator.const import (
    CONF_CLOSING,
    CONF_MIN_OPENING,
    CONF_MODE,
    CONF_OPENING,
    CONF_OUTDOOR,
    CONF_TARGET,
    CONF_TEMPERATURE,
    MODE_DIRECT,
    MODE_DISABLED,
)
from custom_components.smartpi_calculator.coordinator import CalculatorCoordinator
from custom_components.smartpi_calculator.engine import Calculator

OPENING = ["number.left_opening", "number.right_opening"]
CLOSING = ["number.left_closing", "number.right_closing"]
SETTINGS = {
    CONF_MODE: MODE_DIRECT,
    CONF_TEMPERATURE: "sensor.room",
    CONF_TARGET: "sensor.target",
    CONF_OUTDOOR: "sensor.outside",
    CONF_OPENING: OPENING,
    CONF_CLOSING: CLOSING,
}


def json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


@pytest.fixture
async def model(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = Mock()
    hass.config_entries.async_entries.return_value = []
    for entity, value in zip(OPENING + CLOSING, [0, 0, 100, 100], strict=True):
        hass.states.async_set(entity, value, {"min": 0, "max": 100, "step": 1})

    async def load(settings, saved=None):
        coordinator = CalculatorCoordinator(
            hass, Mock(data=SETTINGS, options=settings, title="Test", entry_id="test")
        )
        coordinator.store = Mock(
            async_load=AsyncMock(return_value=json_copy(saved)),
            async_save=AsyncMock(),
        )
        await coordinator.initialize()
        return coordinator

    coordinator = await load({})
    est = coordinator.engine.algo.est
    est.a = 0.02
    est.b = 0.001
    est.model_hvac_mode = "heat"
    est.a_meas_hist.extend([0.02] * 8)
    est.b_meas_hist.extend([0.001] * 31)
    est._a_hat_hist.extend([0.02] * 8)
    est._b_hat_hist.extend([0.001] * 5)
    est.learn_ok_count_a = 3
    est.learn_ok_count_b = 3  # Deliberately not converged yet.
    est.learn_ok_count = 6
    coordinator.engine.algo.ctl.integral = 1.25
    yield coordinator, load
    await hass.async_stop()


@pytest.mark.parametrize(
    "previous,current",
    [
        ({}, {OPENING[0]: 30}),
        ({OPENING[0]: 0}, {OPENING[0]: 30, OPENING[1]: 40}),
        ({OPENING[0]: 30}, {OPENING[0]: 45}),
        ({OPENING[0]: 30, OPENING[1]: 45}, {OPENING[1]: 45}),
        ({OPENING[0]: 30}, {OPENING[0]: 0}),
        ({OPENING[0]: 30}, {}),
    ],
)
async def test_range_tuning_matches_an_ordinary_restore(model, previous, current):
    coordinator, load = model
    # Build both the legacy three-part and 0.2.2 four-part storage envelopes.
    coordinator.minima = previous
    saved = json_copy(coordinator._storage_data())
    baseline = Calculator(Mock(), "Test", saved_state=json_copy(saved["algorithm"]))
    restored = await load({CONF_MIN_OPENING: current}, saved)
    for component in ("est", "ctl", "dt_est", "gain_scheduler", "_ff_trim"):
        actual = getattr(restored.engine.algo, component).save_state()
        expected = getattr(baseline.algo, component).save_state()
        assert actual == expected
    assert restored.engine.algo.phase.value == "Stable"
    assert restored.engine.algo.meas_count_a == 8
    assert restored.engine.algo.meas_count_b == 31
    assert restored.engine.algo.ctl.integral == 1.25
    # Current range is persisted and a second fresh coordinator restores it too.
    await restored.save()
    persisted = restored.store.async_save.call_args.args[0]
    assert persisted["application"] == restored._application_binding()
    again = await load({CONF_MIN_OPENING: current}, persisted)
    assert again.engine.algo.est.save_state() == restored.engine.algo.est.save_state()
    assert again.engine.algo.ctl.integral == 1.25


@pytest.mark.parametrize(
    "change",
    [
        {CONF_TEMPERATURE: "sensor.another_room"},
        {CONF_OUTDOOR: "sensor.another_outside"},
        {CONF_MODE: MODE_DISABLED},
        {CONF_OPENING: OPENING[::-1]},
        {CONF_CLOSING: CLOSING[::-1]},
    ],
)
async def test_minimum_tolerance_never_admits_a_different_identity(model, change):
    coordinator, load = model
    saved = coordinator._storage_data()
    changed = await load({CONF_MIN_OPENING: {OPENING[0]: 30}, **change}, saved)
    assert changed.engine.algo.meas_count_a == 0
    assert changed.engine.algo.meas_count_b == 0
    assert changed.engine.algo.ctl.integral != 1.25


async def test_target_and_hysteresis_changes_do_not_erase_the_model(model):
    coordinator, load = model
    changed = await load(
        {
            CONF_MIN_OPENING: {OPENING[0]: 30},
            CONF_TARGET: "sensor.new_target",
            "hysteresis_on": 0,
            "hysteresis_off": 0.1,
        },
        coordinator._storage_data(),
    )
    assert changed.engine.algo.meas_count_a == 8
    assert changed.engine.algo.meas_count_b == 31
    assert changed.engine.algo.ctl.integral == 1.25


async def test_deliberate_mode_round_trip_is_a_fresh_start(model):
    coordinator, load = model
    disabled = await load({CONF_MODE: MODE_DISABLED}, coordinator._storage_data())
    assert disabled.engine.algo.meas_count_a == 0
    assert disabled.engine.algo.meas_count_b == 0
    await disabled.save()
    persisted = disabled.store.async_save.call_args.args[0]
    direct = await load({CONF_MODE: MODE_DIRECT}, persisted)
    assert direct.engine.algo.meas_count_a == 0
    assert direct.engine.algo.meas_count_b == 0
    assert direct.engine.algo.ctl.integral != 1.25


async def test_retention_preserves_quality_gates_and_native_drift_learning(model):
    coordinator, load = model
    restored = await load(
        {CONF_MIN_OPENING: {OPENING[0]: 30}}, coordinator._storage_data()
    )
    est = restored.engine.algo.est
    assert not est.b_converged_for_a()
    # A changed physical response is not silently accepted as a good sample.
    est.learn(0.022, 0.8, 20, 10)  # Implies a=0.04 instead of 0.02.
    assert est.diag_a_last_reason == "REJECT_NOT_ELIGIBLE_FOR_DRIFT"
    assert list(est.a_meas_hist) == [0.02] * 8
    assert est.a == 0.02
    # Ordinary accepted cooling observations satisfy the original convergence gate.
    for _ in range(8):
        est.learn(-0.01, 0, 20, 10)
    assert est.b_converged_for_a()
    # The unmodified core can then recenter using repeated, real observations.
    for _ in range(400):
        est.learn(0.022, 0.8, 20, 10)
        if est.learn_ok_count_a > 3:
            break
    assert est.learn_ok_count_a > 3
    assert len(est.a_meas_hist) == 9
    assert list(est.a_meas_hist)[:8] == [0.02] * 8
    assert est.a_meas_hist[-1] == pytest.approx(0.04)
    assert list(est.b_meas_hist) == [0.001] * 31
