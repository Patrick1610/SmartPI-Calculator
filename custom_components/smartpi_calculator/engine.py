"""Small sensor-only runtime around the unchanged SmartPI control law.

Logical cycles only maintain the core's timing/ownership bookkeeping. They never
start a PWM device or call a Home Assistant service.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .const import CYCLE_MINUTES, SAMPLE_SECONDS
from .vendor.algo import SmartPI
from .vendor.hvac_mode import VThermHvacMode_HEAT, VThermHvacMode_OFF
from .vendor.smartpi.command_ownership import project_cycle_command


@dataclass(frozen=True)
class Inputs:
    temperature: float
    target: float
    outdoor: float
    measurement_id: Any = None

    def __post_init__(self):
        if not all(
            math.isfinite(v) for v in (self.temperature, self.target, self.outdoor)
        ):
            raise ValueError("Inputs must be finite")


class Calculator:
    """Hold persistent model state and time-weighted command history."""

    def __init__(self, hass, name: str, *, output_applied=False, saved_state=None):
        self.output_applied = output_applied
        self.algo = SmartPI(
            hass,
            CYCLE_MINUTES,
            0,
            0,
            name,
            saved_state=saved_state,
            use_ff3=True,
        )
        # Continuous numeric output; no valve curve or extra actuator rounding.
        self.algo.configure_valve_linearization(False, None, valve_mode=True)
        self.algo.set_learning_enabled(output_applied)
        self._cycle_start: float | None = None
        self._last_tick: float | None = None
        self._command = 0.0
        self._command_integral = 0.0
        self._last_temperature: float | None = None
        self._interrupted = False

    def interrupt(self):
        """Missing inputs are unknown, never fictitious temperatures or zero output."""
        if not self._interrupted:
            self.algo.calculate(None, None, hvac_mode=VThermHvacMode_OFF)
            self.algo.on_applied_power_updated(
                on_percent=0.0,
                hvac_mode=VThermHvacMode_OFF,
                projection=project_cycle_command(0.0, CYCLE_MINUTES, 0, 0),
            )
        self.algo.reset_cycle_state()
        self.algo.reset_command_ownership("missing_inputs")
        self._cycle_start = self._last_tick = None
        self._last_temperature = None
        self._command_integral = 0.0
        self._command = 0.0
        self._interrupted = True

    async def step(self, inputs: Inputs) -> float:
        now = time.monotonic()
        if self._last_tick is not None and now - self._last_tick > 3 * SAMPLE_SECONDS:
            self.interrupt()
        if self._interrupted:
            self.algo.notify_resume_after_interruption()
            self._interrupted = False

        elapsed = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._command_integral += self._command * elapsed
        boundary = (
            self._cycle_start is None or now - self._cycle_start >= CYCLE_MINUTES * 60
        )
        if boundary and self._cycle_start is not None:
            duration = now - self._cycle_start
            await self.algo.on_cycle_completed(
                e_eff=self._command_integral / duration,
                elapsed_ratio=duration / (CYCLE_MINUTES * 60),
                cycle_duration_min=CYCLE_MINUTES,
            )

        slope = None
        if elapsed > 0 and self._last_temperature is not None:
            slope = (inputs.temperature - self._last_temperature) * 3600 / elapsed
        self.algo.calculate(
            target_temp=inputs.target,
            current_temp=inputs.temperature,
            ext_current_temp=inputs.outdoor,
            hvac_mode=VThermHvacMode_HEAT,
            slope=slope,
            cycle_boundary=boundary,
            temperature_measure_id=inputs.measurement_id,
        )
        command = self.algo.on_percent
        if not math.isfinite(command) or not 0 <= command <= 1:
            self.interrupt()
            raise ValueError("SmartPI produced invalid demand")

        projection = project_cycle_command(command, CYCLE_MINUTES, 0, 0)
        self.algo.stage_valve_command(
            projection,
            VThermHvacMode_HEAT,
            expected_actuator_power=command,
        )
        if boundary:
            await self.algo.on_cycle_started(
                projection.on_time_sec,
                projection.off_time_sec,
                projection.projected_power,
                VThermHvacMode_HEAT,
                physical_on_percent=command,
            )
            self._cycle_start = now
            self._command_integral = 0.0
        else:
            self.algo.on_applied_power_updated(
                on_percent=command,
                hvac_mode=VThermHvacMode_HEAT,
                projection=projection,
            )
        self._command = command
        self._last_tick = now
        self._last_temperature = inputs.temperature
        return command * 100

    def snapshot(self) -> dict:
        return self.algo.save_state()

    def diagnostics(self) -> dict:
        return {
            "phase": self.algo.phase.value,
            "learning_enabled": self.output_applied,
            "feedback": "assumed_1_to_1"
            if self.output_applied
            else "virtual_not_applied",
            "kp": self.algo.kp,
            "ki": self.algo.ki,
            "thermal_gain": self.algo.a,
            "thermal_loss": self.algo.b,
            "learned_heating_samples": self.algo.meas_count_a,
            "learned_cooling_samples": self.algo.meas_count_b,
            "cycle_minutes": CYCLE_MINUTES,
            "pump_overrun_protection": False,
        }
