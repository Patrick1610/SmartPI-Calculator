"""Small historical diagnostics without changing the controller."""

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.smartpi_calculator.sensor import (
    LastLearningResultSensor,
    LearningSampleSensor,
    ValveStatusSensor,
)


def entry(coordinator):
    return SimpleNamespace(entry_id="test", title="Test", runtime_data=coordinator)


def test_valve_failure_stays_visible_during_calculation_failure():
    coordinator = Mock(last_update_success=False)
    coordinator.valves.status = "failed"
    sensor = ValveStatusSensor(entry(coordinator))
    assert sensor.available
    assert sensor.native_value == "failed"
    coordinator.valves.status = "unavailable"
    assert sensor.available
    assert sensor.native_value == "unavailable"
    coordinator.valves = None
    assert sensor.native_value == "not_configured"


def test_sample_counts_are_current_core_counts_not_invented_lifetime_totals():
    coordinator = Mock(last_update_success=True)
    data = {"learned_heating_samples": 3, "learned_cooling_samples": 8}
    coordinator.model_diagnostics.side_effect = lambda: dict(data)
    heating = LearningSampleSensor(entry(coordinator), "heating")
    cooling = LearningSampleSensor(entry(coordinator), "cooling")
    assert heating.native_value == 3
    assert cooling.native_value == 8
    assert heating.state_class is None
    data["learned_heating_samples"] = 4
    assert heating.native_value == 4
    coordinator.last_update_success = False
    assert not heating.available
    assert not cooling.available


def test_last_result_is_not_reinterpreted_as_current_learning_permission():
    coordinator = Mock(last_update_success=True)
    coordinator.model_diagnostics.return_value = {
        "last_learning_result": "skip: delta too small",
        "learning_enabled": False,
    }
    sensor = LastLearningResultSensor(entry(coordinator))
    assert sensor.native_value == "skip: delta too small"
    coordinator.model_diagnostics.return_value["last_learning_result"] = None
    assert sensor.native_value is None
    coordinator.model_diagnostics.return_value["last_learning_result"] = "x" * 300
    assert len(sensor.native_value) == 255
