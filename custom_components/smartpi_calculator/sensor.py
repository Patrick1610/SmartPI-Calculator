"""A numerical demand sensor, not a climate or actuator entity."""

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, UPSTREAM_COMMIT


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DemandSensor(entry)])


class DemandSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Heating demand"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    def __init__(self, entry):
        super().__init__(entry.runtime_data)
        self._attr_unique_id = f"{entry.entry_id}_demand"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Unofficial derivative of KipK / gael1980 SmartPI",
            model="Standalone calculator (no actuator control)",
        )

    @property
    def native_value(self):
        return self.coordinator.data

    @property
    def extra_state_attributes(self):
        return {
            **self.coordinator.engine.diagnostics(),
            "upstream_commit": UPSTREAM_COMMIT,
            "input_temperatures_c": None
            if self.coordinator.inputs is None
            else {
                "room": self.coordinator.inputs.temperature,
                "target": self.coordinator.inputs.target,
                "outdoor": self.coordinator.inputs.outdoor,
            },
        }
