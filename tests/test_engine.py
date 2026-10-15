"""Local contracts against the real vendored core, not a rewritten PID."""

import ast
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from custom_components.smartpi_calculator.engine import Calculator, Inputs
from custom_components.smartpi_calculator.vendor.hvac_mode import VThermHvacMode_HEAT
from custom_components.smartpi_calculator.vendor.smartpi.controller import (
    SmartPIController,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clock(monkeypatch):
    now = [10000.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    return now


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_rejected(bad):
    with pytest.raises(ValueError):
        Inputs(bad, 20, 10)


async def test_observer_does_not_learn(clock):
    engine = Calculator(Mock(), "Observe")
    for room in (19, 19.1, 19.2, 19.4, 20.1):
        demand = await engine.step(Inputs(room, 20, 10))
        assert 0 <= demand <= 100
        clock[0] += 30
    assert engine.algo.meas_count_a == 0
    assert engine.algo.meas_count_b == 0
    assert not engine.algo.learning_enabled
    assert engine.diagnostics()["feedback"] == "virtual_not_applied"


async def test_bootstrap_does_not_close_at_target(clock):
    engine = Calculator(Mock(), "Hysteresis")
    assert await engine.step(Inputs(19, 20, 10)) == 100
    clock[0] += 30
    assert await engine.step(Inputs(20, 20, 10)) == 100
    clock[0] += 30
    assert await engine.step(Inputs(20.6, 20, 10)) == 0


async def test_custom_bootstrap_closes_at_target_and_keeps_hysteresis(clock):
    engine = Calculator(Mock(), "Tight band", hysteresis_on=0.2, hysteresis_off=0)
    for room, expected in ((19, 100), (19.9, 100), (20, 0), (19.9, 0), (19.8, 100)):
        assert await engine.step(Inputs(room, 20, 10)) == expected
        clock[0] += 30


async def test_changed_margins_keep_saved_model_and_integral(clock):
    engine = Calculator(Mock(), "Saved")
    engine.algo.ctl.integral = 1.25
    saved = engine.snapshot()
    restored = Calculator(
        Mock(), "Saved", saved_state=saved, hysteresis_on=0.2, hysteresis_off=0
    )
    assert restored.algo.ctl.integral == 1.25
    assert restored.algo._hyst_on == 0.2
    assert restored.algo._hyst_off == 0


def test_learning_result_is_the_actual_core_reason():
    engine = Calculator(Mock(), "Reason")
    engine.algo.est.learn_last_reason = "skip: delta too small"
    assert engine.diagnostics()["last_learning_result"] == "skip: delta too small"


async def test_custom_bootstrap_thresholds_do_not_change_stable_pi(clock):
    engines = [
        Calculator(Mock(), "Default"),
        Calculator(Mock(), "Tight", hysteresis_on=0.2, hysteresis_off=0),
    ]
    for engine in engines:
        engine.algo.est.a_meas_hist.extend([0.05] * 6)
        engine.algo.est.b_meas_hist.extend([0.001] * 8)
        engine.algo.ctl.integral = 1.0
        assert engine.algo.phase.value == "Stable"
    for room in (19, 19.9, 20, 20.1):
        outputs = [await engine.step(Inputs(room, 20, 10)) for engine in engines]
        assert outputs[0] == pytest.approx(outputs[1])
        clock[0] += 30


def test_stable_controller_can_hold_heat_at_setpoint():
    ctl = SmartPIController("Holding")
    ctl.integral = 1.0
    output = ctl.compute_pwm(
        error=0,
        error_p=0,
        kp=0.5,
        ki=0.1,
        u_ff=0.2,
        dt_min=0.5,
        cycle_min=10,
        in_deadband=True,
        in_near_band=False,
        integrator_hold=False,
        u_db_nominal=0.2,
        hvac_mode=VThermHvacMode_HEAT,
        current_temp=20,
        target_temp=20,
        is_tau_reliable=True,
        learn_ok_count_a=10,
        deadband_c=0.05,
    )
    assert output == pytest.approx(0.3)
    assert ctl.integral == 1.0


async def test_previous_output_is_committed_not_new_sensor_delta(clock):
    engine = Calculator(Mock(), "Applied", output_applied=True)
    await engine.step(Inputs(19, 20, 10))
    assert engine.algo.linear_committed_on_percent == 1.0
    clock[0] += 30
    await engine.step(Inputs(19.1, 20, 10))
    assert engine._command_integral == 30
    assert engine.algo.linear_committed_on_percent == 1.0
    # Bootstrap has no feed-forward decomposition to bind yet. Preserve the
    # upstream rejection instead of inventing attribution during bootstrap.
    assert (
        engine.algo._command_ownership.last_binding.reason
        == "ownership_request_missing"
    )


async def test_logical_cycle_completion_and_restart(clock):
    engine = Calculator(Mock(), "Cycle")
    for _ in range(22):
        await engine.step(Inputs(19, 20, 10))
        clock[0] += 30
    assert engine.algo.cycles_since_reset == 1
    assert engine._command_integral == 30


async def test_missing_inputs_interrupt_and_recover(clock):
    engine = Calculator(Mock(), "Recovery", output_applied=True)
    await engine.step(Inputs(19, 20, 10))
    engine.interrupt()
    assert engine._cycle_start is None
    assert engine.algo.linear_committed_on_percent == 0
    clock[0] += 30
    demand = await engine.step(Inputs(19, 20, 10))
    assert 0 <= demand <= 100
    assert engine.algo.learning_resume_ts is not None


async def test_gap_does_not_integrate_an_unobserved_hour(clock):
    engine = Calculator(Mock(), "Gap", output_applied=True)
    await engine.step(Inputs(19, 20, 10))
    clock[0] += 3600
    await engine.step(Inputs(19, 20, 10))
    assert engine._command_integral == 0
    assert engine.algo.learning_resume_ts is not None


async def test_saved_model_and_integral_round_trip(clock):
    engine = Calculator(Mock(), "Saved")
    await engine.step(Inputs(19, 20, 10))
    engine.algo.ctl.integral = 1.25
    data = json.loads(json.dumps(engine.snapshot(), allow_nan=False))
    restored = Calculator(Mock(), "Saved", saved_state=data)
    assert restored.algo.ctl.integral == pytest.approx(1.25)
    assert not restored.algo.learning_enabled


async def test_learning_path_with_synthetic_heating_and_cooling(clock):
    """Adapter smoke test, not proof that a real radiator has these dynamics."""
    engine = Calculator(Mock(), "Synthetic", output_applied=True)
    room = 19.0
    stable_seen = bound_seen = False
    for tick in range(24 * 120):
        target = 20.0 if tick < 12 * 120 else 17.0
        demand = await engine.step(Inputs(room, target, 10, tick))
        stable_seen |= engine.algo.phase.value == "Stable"
        binding = engine.algo._command_ownership.last_binding
        bound_seen |= binding is not None and binding.status.value == "bound"
        # Simple synthetic first-order plant; command held for the next 30s.
        room += 0.5 * (0.06 * demand / 100 - 0.003 * (room - 10))
        clock[0] += 30
    assert engine.algo.meas_count_a > 0
    assert engine.algo.meas_count_b > 0
    assert engine.algo.cycles_since_reset > 100
    assert stable_seen
    assert bound_seen


def test_only_optional_adapter_can_write_number_entities():
    component = ROOT / "custom_components/smartpi_calculator"
    for path in component.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("vtherm_api", "custom_components.versatile_thermostat")
                ), path
            if isinstance(node, ast.Attribute):
                assert node.attr not in (
                    "async_set_native_value",
                    "async_set_temperature",
                ), path
                if node.attr == "async_call":
                    assert path.name == "valve.py", path
    manifest = json.loads((component / "manifest.json").read_text())
    assert not manifest.get("dependencies")
    assert not manifest["requirements"]
