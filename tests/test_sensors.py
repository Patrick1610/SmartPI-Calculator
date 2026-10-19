"""Small historical diagnostics without changing the controller."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.smartpi_calculator.sensor import (
    CONTRIBUTION_NAMES,
    ContributionSensor,
    DemandSensor,
    LastLearningResultSensor,
    LearningSampleSensor,
    LearningStatusSensor,
    ValveStatusSensor,
)


def entry(coordinator):
    return SimpleNamespace(entry_id="test", title="Test", runtime_data=coordinator)


def test_entity_backed_values_are_not_duplicated_as_attributes():
    diagnostics = {
        "phase": "Hysteresis",
        "learning_enabled": True,
        "feedback": "confirmed_number_values",
        "kp": 0.55,
        "ki": 0.01,
        "thermal_gain": 0.0005,
        "thermal_loss": 0.001,
        "learned_heating_samples": 3,
        "learned_cooling_samples": 8,
        "last_learning_result": "learned b",
        "cycle_minutes": 10,
        "pump_overrun_protection": False,
        **dict.fromkeys(CONTRIBUTION_NAMES, -2.5),
    }
    original = dict(diagnostics)
    coordinator = Mock(data=25.0, mode="direct_valve_control")
    coordinator.model_diagnostics.return_value = diagnostics
    demand = DemandSensor(entry(coordinator))
    assert demand.native_value == 25
    assert demand.extra_state_attributes == {
        **{
            key: diagnostics[key]
            for key in (
                "feedback",
                "kp",
                "ki",
                "thermal_gain",
                "thermal_loss",
                "cycle_minutes",
                "pump_overrun_protection",
            )
        },
        "upstream_commit": "6a131df56cf3cfe2974f78f55f33ce8a095076b3",
        "control_mode": "direct_valve_control",
    }
    assert LearningStatusSensor(entry(coordinator)).extra_state_attributes == {
        "feedback": "confirmed_number_values"
    }
    assert diagnostics == original
    assert LearningSampleSensor(entry(coordinator), "heating").native_value == 3
    assert LearningSampleSensor(entry(coordinator), "cooling").native_value == 8
    assert LastLearningResultSensor(entry(coordinator)).native_value == "learned b"


@pytest.mark.parametrize("key", CONTRIBUTION_NAMES)
def test_signed_contribution_sensors_are_measurements_and_unknown_without_pi(key):
    coordinator = Mock(last_update_success=True)
    data = {key: -8.25}
    coordinator.model_diagnostics.side_effect = lambda: dict(data)
    sensor = ContributionSensor(entry(coordinator), key)
    assert sensor.unique_id == f"test_{key}"
    assert sensor.native_value == -8.25
    assert sensor.native_unit_of_measurement == "%"
    assert sensor.state_class is SensorStateClass.MEASUREMENT
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    data[key] = None
    assert sensor.native_value is None
    coordinator.last_update_success = False
    assert not sensor.available


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
