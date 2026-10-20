"""Read-only source adapter, periodic calculation, and persistence."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta
from time import monotonic

from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    CONF_CLOSING,
    CONF_MIN_OPENING,
    CONF_OPENING,
    CONF_OUTDOOR,
    CONF_TARGET,
    CONF_TEMPERATURE,
    DOMAIN,
    MODE_DIRECT,
    MODE_EXTERNAL,
    SAMPLE_SECONDS,
    SOURCE_KEYS,
    UPSTREAM_COMMIT,
    control_mode,
    hysteresis_margins,
    valve_minima,
)
from .engine import Calculator, Inputs
from .valve import ValveController, valve_bindings

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
        self.mode = control_mode(self.settings)
        self.valves = None
        self.minima = {}
        self._control_lock = asyncio.Lock()
        self._stopping = False
        self._next_storage_save_request = 0.0

    async def initialize(self):
        if self.mode == MODE_DIRECT:
            try:
                self.minima = valve_minima(self.settings)
            except ValueError as err:
                raise ConfigEntryError("Invalid minimum valve openings") from err
        else:
            self.minima = {}
        saved = await self.store.async_load()
        binding = [self.settings[k] for k in (CONF_TEMPERATURE, CONF_OUTDOOR)]
        state = (
            saved.get("algorithm")
            if isinstance(saved, dict)
            and saved.get("sources") == binding
            and self._compatible_application(saved)
            else None
        )
        if (
            state is not None
            and self.mode == MODE_DIRECT
            and saved.get("application") != self._application_binding()
        ):
            _LOGGER.warning(
                "%s: valve minimums changed; retaining learned samples and PI "
                "state unchanged. Existing learning quality gates still apply; "
                "heating outliers cannot recenter until cooling converges. "
                "Monitor the changed heating response and Last learning result",
                self.entry.title,
            )
        try:
            hysteresis_on, hysteresis_off = hysteresis_margins(self.settings)
        except ValueError as err:
            raise ConfigEntryError("Invalid bootstrap hysteresis margins") from err
        self.engine = Calculator(
            self.hass,
            self.entry.title,
            output_applied=self.mode == MODE_EXTERNAL,
            saved_state=state,
            hysteresis_on=hysteresis_on,
            hysteresis_off=hysteresis_off,
        )
        if self.mode == MODE_DIRECT:
            try:
                pairs, step = valve_bindings(
                    self.hass, self.settings, exclude_entry=self.entry.entry_id
                )
            except ValueError as err:
                if str(err) in ("invalid_valve", "invalid_valve_range") and any(
                    self.hass.states.get(entity) is None
                    or self.hass.states.get(entity).state in ("unavailable", "unknown")
                    for key in (CONF_OPENING, CONF_CLOSING)
                    for entity in self.settings.get(key, [])
                ):
                    raise ConfigEntryNotReady("Valve entities are not ready") from err
                raise ConfigEntryError(str(err)) from err
            self.valves = ValveController(self.hass, pairs, step, self.minima)

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
        async with self._control_lock:
            if self._stopping:
                raise UpdateFailed("SmartPI is stopping")
            return await self._calculate()

    async def _calculate(self):
        try:
            room, measure_id = self._read_temperature(CONF_TEMPERATURE)
            target, _ = self._read_temperature(CONF_TARGET)
            outdoor, _ = self._read_temperature(CONF_OUTDOOR)
            self.inputs = Inputs(room, target, outdoor, measure_id)
            if self.valves is None:
                value = await self.engine.step(self.inputs)
            else:
                self.engine.set_feedback(self.valves.confirmed())
                value = await self.engine.step(
                    self.inputs, command_transform=self.valves.prepare
                )
                await self.valves.apply(value)
                # A service may return before its state echo. No learning step
                # runs after this write; check the echo before the next step.
                if self.valves.status in ("failed", "unavailable"):
                    self.engine.set_feedback(False)
        except Exception as err:
            # The HA coordinator exposes failures as unavailable. Also invalidate
            # our partial cycle after unexpected core errors, not just bad inputs.
            self.inputs = None
            self.engine.interrupt()
            if self.valves is not None:
                self.engine.set_feedback(False)
                await self.valves.close()
            raise UpdateFailed(str(err)) from err
        # A 60-second Store debounce is starved by 30-second updates. Rate-limit
        # requests ourselves and queue a nonblocking write without that delay.
        now = monotonic()
        if now >= self._next_storage_save_request:
            self.store.async_delay_save(self._storage_data, 0)
            self._next_storage_save_request = now + 60
        return value

    def _storage_data(self):
        return {
            "sources": [self.settings[k] for k in (CONF_TEMPERATURE, CONF_OUTDOOR)],
            "upstream_commit": UPSTREAM_COMMIT,
            "application": self._application_binding(),
            "algorithm": self.engine.snapshot(),
        }

    def _application_binding(self):
        binding = [
            self.mode,
            self.settings.get(CONF_OPENING, []),
            self.settings.get(CONF_CLOSING, []),
        ]
        nonzero = [[e, v] for e, v in sorted(self.minima.items()) if v != 0]
        # Keep the applied curve as provenance, not a reason to erase learning.
        return binding + [nonzero] if nonzero else binding

    def _compatible_application(self, saved):
        """Retain learning across range tuning, never across rooms/actuators/modes."""
        current = self._application_binding()
        previous = saved.get(
            "application", current if self.mode != MODE_DIRECT else None
        )
        if not isinstance(previous, list) or len(previous) not in (3, 4):
            return False
        if previous[:3] != current[:3]:
            return False
        if len(previous) == 4:
            minima = previous[3]
            if not isinstance(minima, list) or any(
                not isinstance(item, list) or len(item) != 2 for item in minima
            ):
                return False
            try:
                values = dict(minima)
                if len(values) != len(minima):
                    return False
                valve_minima({CONF_OPENING: previous[1], CONF_MIN_OPENING: values})
            except ValueError, TypeError:
                return False
        return True

    def model_diagnostics(self):
        data = self.engine.diagnostics()
        if self.valves is not None and not self.valves.confirmed():
            data.update(learning_enabled=False, feedback="awaiting_number_values")
        return data

    async def save(self):
        if self.engine is not None:
            await self.store.async_save(self._storage_data())

    async def shutdown(self):
        self._stopping = True
        await self.async_shutdown()
        async with self._control_lock:
            if self.valves is not None:
                self.engine.set_feedback(False)
                if not await self.valves.close(terminal=True):
                    _LOGGER.warning(
                        "%s: final valve close is not confirmed", self.entry.title
                    )
            await self.save()

    @property
    def source_entities(self):
        return [self.settings[k] for k in SOURCE_KEYS]
