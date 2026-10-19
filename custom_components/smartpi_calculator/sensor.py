"""A numerical demand sensor, not a climate or actuator entity."""

import math

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_DIRECT, MODE_EXTERNAL, UPSTREAM_COMMIT

CONTRIBUTION_NAMES = {
    "base_contribution": "Base contribution",
    "proportional_contribution": "Proportional contribution",
    "integral_contribution": "Integral contribution",
    "predictive_contribution": "Predictive contribution",
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            DemandSensor(entry),
            *[
                InputTemperatureSensor(entry, key)
                for key in ("room", "target", "outdoor")
            ],
            PhaseSensor(entry),
            LearningStatusSensor(entry),
            ValveStatusSensor(entry),
            LearningSampleSensor(entry, "heating"),
            LearningSampleSensor(entry, "cooling"),
            LastLearningResultSensor(entry),
            *[ContributionSensor(entry, key) for key in CONTRIBUTION_NAMES],
            UnboundedPIDemandSensor(entry),
        ]
    )


class CalculatorSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, key):
        super().__init__(entry.runtime_data)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            model="SmartPI Calculator",
        )


class DemandSensor(CalculatorSensor):
    _attr_name = "Heating demand"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    def __init__(self, entry):
        super().__init__(entry, "demand")

    @property
    def native_value(self):
        return self.coordinator.data

    @property
    def extra_state_attributes(self):
        return {
            **{
                key: value
                for key, value in self.coordinator.model_diagnostics().items()
                if key
                not in {
                    "phase",
                    "learning_enabled",
                    "learned_heating_samples",
                    "learned_cooling_samples",
                    "last_learning_result",
                    *CONTRIBUTION_NAMES,
                }
            },
            "upstream_commit": UPSTREAM_COMMIT,
            "control_mode": self.coordinator.mode,
        }


class UnboundedPIDemandSensor(CalculatorSensor):
    """Sum one cached PI snapshot before final output/actuator constraints."""

    _attr_translation_key = "unbounded_pi_demand"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:sigma"

    def __init__(self, entry):
        super().__init__(entry, "unbounded_pi_demand")

    @property
    def native_value(self):
        diagnostics = self.coordinator.model_diagnostics()
        values = [diagnostics.get(key) for key in CONTRIBUTION_NAMES]
        if any(value is None for value in values):
            return None
        try:
            if not all(math.isfinite(value) for value in values):
                return None
            total = math.fsum(values)
        except TypeError, ValueError, OverflowError:
            return None
        return total if math.isfinite(total) else None


class ContributionSensor(CalculatorSensor):
    """Signed percentage-point terms before command/output constraints."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calculator-variant-outline"

    def __init__(self, entry, key):
        super().__init__(entry, key)
        self._key = key
        self._attr_name = CONTRIBUTION_NAMES[key]

    @property
    def native_value(self):
        return self.coordinator.model_diagnostics()[self._key]


class InputTemperatureSensor(CalculatorSensor):
    """The successful calculation's input, not an independently reread source."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, key):
        super().__init__(entry, f"used_{key}_temperature")
        self._key = {"room": "temperature", "target": "target", "outdoor": "outdoor"}[
            key
        ]
        self._attr_name = f"Used {key} temperature"

    @property
    def native_value(self):
        inputs = self.coordinator.inputs
        return None if inputs is None else getattr(inputs, self._key)


class PhaseSensor(CalculatorSensor):
    _attr_name = "Control phase"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["Hysteresis", "Stable", "Calibration"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:state-machine"

    def __init__(self, entry):
        super().__init__(entry, "control_phase")

    @property
    def native_value(self):
        return self.coordinator.engine.diagnostics()["phase"]


class LearningStatusSensor(CalculatorSensor):
    """Enabled means permission to learn, not proof of accepted samples."""

    _attr_name = "Learning status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["observing", "enabled", "paused"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:school"

    def __init__(self, entry):
        super().__init__(entry, "learning_status")

    @property
    def native_value(self):
        if self.coordinator.mode == MODE_EXTERNAL:
            return "enabled"
        if self.coordinator.mode == MODE_DIRECT:
            return (
                "enabled"
                if self.coordinator.engine.algo.learning_enabled
                and self.coordinator.valves.confirmed()
                else "paused"
            )
        return "observing"

    @property
    def extra_state_attributes(self):
        diagnostics = self.coordinator.model_diagnostics()
        return {"feedback": diagnostics["feedback"]}


class ValveStatusSensor(CalculatorSensor):
    """Keep endpoint failures visible even when calculation is unavailable."""

    _attr_name = "Valve control status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "not_configured",
        "starting",
        "pending",
        "failed",
        "unavailable",
        "confirmed",
    ]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:valve"

    def __init__(self, entry):
        super().__init__(entry, "valve_control_status")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        valves = self.coordinator.valves
        return valves.status if valves else "not_configured"


class LearningSampleSensor(CalculatorSensor):
    """Accepted model samples, not every temperature report or control cycle."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:counter"
    _attr_suggested_display_precision = 0

    def __init__(self, entry, kind):
        super().__init__(entry, f"learned_{kind}_samples")
        self._key = f"learned_{kind}_samples"
        self._attr_name = f"Accepted {kind} samples"

    @property
    def native_value(self):
        return self.coordinator.model_diagnostics()[self._key]


class LastLearningResultSensor(CalculatorSensor):
    """Last core learning result, not a claim about the current blocking cause."""

    _attr_name = "Last learning result"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:school-outline"

    def __init__(self, entry):
        super().__init__(entry, "last_learning_result")

    @property
    def native_value(self):
        result = self.coordinator.model_diagnostics()["last_learning_result"]
        return str(result)[:255] if result is not None else None
