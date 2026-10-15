"""Read-only source adapter, periodic calculation, and persistence."""

from __future__ import annotations

import logging
import math
from datetime import timedelta

from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_OUTDOOR,
    CONF_OUTPUT_APPLIED,
    CONF_TARGET,
    CONF_TEMPERATURE,
    DOMAIN,
    SAMPLE_SECONDS,
    SOURCE_KEYS,
    UPSTREAM_COMMIT,
)
from .engine import Calculator, Inputs

_LOGGER = logging.getLogger(__name__)


class CalculatorCoordinator(DataUpdateCoordinator[float]):
    def __init__(self, hass, entry):
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            config_entry=entry,
            update_interval=timedelta(seconds=SAMPLE_SECONDS),
        )
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.engine: Calculator | None = None
        self.inputs: Inputs | None = None

    async def initialize(self):
        saved = await self.store.async_load()
        binding = [self.settings[k] for k in (CONF_TEMPERATURE, CONF_OUTDOOR)]
        state = (
            saved.get("algorithm")
            if isinstance(saved, dict) and saved.get("sources") == binding
            else None
        )
        self.engine = Calculator(
            self.hass,
            self.entry.title,
            output_applied=self.settings.get(CONF_OUTPUT_APPLIED, False),
            saved_state=state,
        )

    def _read_temperature(self, key):
        state = self.hass.states.get(self.settings[key])
        if state is None or state.state in ("unknown", "unavailable"):
            raise ValueError(f"Missing input: {key}")
        value = float(state.state)
        unit = state.attributes.get(
            "unit_of_measurement", self.hass.config.units.temperature_unit
        )
        if unit not in (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT):
            raise ValueError(f"Unsupported temperature unit: {key}")
        value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        if not math.isfinite(value):
            raise ValueError(f"Non-finite input: {key}")
        return value, state.last_updated

    async def _async_update_data(self):
        try:
            room, measure_id = self._read_temperature(CONF_TEMPERATURE)
            target, _ = self._read_temperature(CONF_TARGET)
            outdoor, _ = self._read_temperature(CONF_OUTDOOR)
            self.inputs = Inputs(room, target, outdoor, measure_id)
            value = await self.engine.step(self.inputs)
        except Exception as err:
            # The HA coordinator exposes failures as unavailable. Also invalidate
            # our partial cycle after unexpected core errors, not just bad inputs.
            self.inputs = None
            self.engine.interrupt()
            raise UpdateFailed(str(err)) from err
        self.store.async_delay_save(self._storage_data, 60)
        return value

    def _storage_data(self):
        return {
            "sources": [self.settings[k] for k in (CONF_TEMPERATURE, CONF_OUTDOOR)],
            "upstream_commit": UPSTREAM_COMMIT,
            "algorithm": self.engine.snapshot(),
        }

    async def save(self):
        if self.engine is not None:
            await self.store.async_save(self._storage_data())

    @property
    def source_entities(self):
        return [self.settings[k] for k in SOURCE_KEYS]
