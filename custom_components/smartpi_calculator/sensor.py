"""A numerical demand sensor, not a climate or actuator entity."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_DIRECT, MODE_EXTERNAL, UPSTREAM_COMMIT


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
            **self.coordinator.model_diagnostics(),
            "upstream_commit": UPSTREAM_COMMIT,
            "control_mode": self.coordinator.mode,
            "valve_status": self.coordinator.valves.status
            if self.coordinator.valves
            else "not_configured",
            "input_temperatures_c": None
            if self.coordinator.inputs is None
            else {
                "room": self.coordinator.inputs.temperature,
                "target": self.coordinator.inputs.target,
                "outdoor": self.coordinator.inputs.outdoor,
            },
        }


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
        return {
            key: diagnostics[key]
            for key in (
                "feedback",
                "learned_heating_samples",
                "learned_cooling_samples",
            )
        }
