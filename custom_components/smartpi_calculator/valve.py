"""Optional number-only actuator adapter; no climate or boiler writes."""

from __future__ import annotations

import asyncio
import math
import time
from fractions import Fraction

from .const import (
    CONF_CLOSING,
    CONF_MIN_OPENING,
    CONF_OPENING,
    DOMAIN,
    MODE_DIRECT,
    SOURCE_KEYS,
    control_mode,
    valve_minima,
)

SEND_INTERVAL = 60
CALL_TIMEOUT = 5


def valve_bindings(hass, settings, *, exclude_entry=None):
    """Validate explicit, unique, paired number entities and a common 0–100 grid."""
    openings, closings = settings.get(CONF_OPENING, []), settings.get(CONF_CLOSING, [])
    if not openings or len(openings) != len(closings):
        raise ValueError("invalid_pairs")
    targets = [*openings, *closings]
    if len(set(targets)) != len(targets):
        raise ValueError("duplicate_valves")
    if set(targets).intersection(settings.get(key) for key in SOURCE_KEYS):
        raise ValueError("valve_is_source")
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude_entry:
            continue
        other = {**entry.data, **entry.options}
        if control_mode(other) == MODE_DIRECT and set(targets).intersection(
            [*other.get(CONF_OPENING, []), *other.get(CONF_CLOSING, [])]
        ):
            raise ValueError("valve_in_use")
    steps = []
    for entity_id in targets:
        state = hass.states.get(entity_id)
        if not entity_id.startswith("number.") or state is None:
            raise ValueError("invalid_valve")
        try:
            low = float(state.attributes["min"])
            high = float(state.attributes["max"])
            step = float(state.attributes.get("step", 1))
            if not all(math.isfinite(v) for v in (low, high, step)):
                raise ValueError
            if low != 0 or high != 100 or not 0 < step <= 100:
                raise ValueError
            fraction = Fraction(str(step))
            if (100 / fraction).denominator != 1:
                raise ValueError
            steps.append(fraction)
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as err:
            raise ValueError("invalid_valve_range") from err
    grid = Fraction(
        math.lcm(*(s.numerator for s in steps)),
        math.gcd(*(s.denominator for s in steps)),
    )
    return list(zip(openings, closings, strict=True)), float(grid)


class ValveController:
    """Send complementary endpoints and confirm numeric echoes, not physical heat."""

    def __init__(self, hass, pairs, step, minima=None):
        self.hass, self.pairs, self.step = hass, pairs, step
        self.minima = valve_minima(
            {
                CONF_OPENING: [p[0] for p in pairs],
                CONF_MIN_OPENING: {} if minima is None else minima,
            }
        )
        self._last_send = {}
        self._expected = None
        self._demand = 0.0
        self._lock = asyncio.Lock()
        self._terminal = False
        self._failed = False
        self.status = "starting"

    @property
    def entity_ids(self):
        return [entity for pair in self.pairs for entity in pair]

    def quantize(self, percent):
        if not math.isfinite(percent):
            raise ValueError("Invalid valve demand")
        return min(100.0, max(0.0, math.floor(percent / self.step + 0.5) * self.step))

    def prepare(self, percent):
        """Return normalized demand; never feed a mapped valve position to PI."""
        value = self.quantize(percent)
        if value and self._expected and self._last_send:
            last = max(sent[0] for sent in self._last_send.values())
            if time.monotonic() - last < SEND_INTERVAL:
                return self._demand
        return value

    def opening_for(self, pair, demand):
        if demand == 0:
            return 0.0
        minimum = self.minima[pair[0]]
        opening = self.quantize(minimum + (100 - minimum) * demand / 100)
        minimum_supported = (
            math.ceil(Fraction(str(minimum)) / Fraction(str(self.step))) * self.step
        )
        return max(minimum_supported, opening)

    def command_diagnostics(self):
        """Existing number entities carry history; this is a redacted snapshot."""
        return [
            {
                "minimum_opening_percent": self.minima[pair[0]],
                "opening_percent": None
                if self._expected is None
                else self._expected[pair[0]],
                "closing_percent": None
                if self._expected is None
                else self._expected[pair[1]],
            }
            for pair in self.pairs
        ]

    def _value(self, entity_id):
        state = self.hass.states.get(entity_id)
        try:
            value = float(state.state) if state else math.nan
        except TypeError, ValueError:
            return None
        return value if math.isfinite(value) and 0 <= value <= 100 else None

    def confirmed(self):
        return self._expected is not None and all(
            self._value(entity) is not None and abs(self._value(entity) - value) < 0.001
            for entity, value in self._expected.items()
        )

    async def _send(self, entity_id, value, closing, bypass_interval=False):
        current = self._value(entity_id)
        if current is None:
            return False
        if abs(current - value) < 0.001:
            return True
        now = time.monotonic()
        previous = self._last_send.get(entity_id)
        if (
            not bypass_interval
            and previous is not None
            and now - previous[0] < SEND_INTERVAL
        ):
            if not closing or previous[1] == value:
                return False
        self._last_send[entity_id] = (now, value)
        try:
            async with asyncio.timeout(CALL_TIMEOUT):
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=True,
                )
        except Exception:
            self._failed = True
            return False
        return True

    async def _send_pair(self, pair, opening, closing, bypass_interval=False):
        targets = [(pair[0], opening), (pair[1], 100 - opening)]
        current_opening = self._value(pair[0])
        # Keep the intermediate endpoint range valid: expand the requested
        # endpoint first, then contract the other end. Never sum below 100.
        if current_opening is not None and opening < current_opening:
            targets.reverse()
        success = True
        for entity_id, value in targets:
            sent = await self._send(entity_id, value, closing, bypass_interval)
            success = success and sent
            if not sent and not closing:
                break
        return success

    async def apply(self, percent, *, force_close=False):
        async with self._lock:
            if self._terminal:
                return False
            return await self._apply(percent, force_close=force_close)

    async def _apply(self, percent, *, force_close=False, bypass_interval=False):
        self._failed = False
        demand = 0 if force_close else self.prepare(percent)
        unavailable = any(self._value(entity) is None for entity in self.entity_ids)
        if unavailable:
            demand = 0.0
        self._demand = demand
        openings = {pair[0]: self.opening_for(pair, demand) for pair in self.pairs}
        self._expected = {
            entity: value
            for pair in self.pairs
            for entity, value in zip(
                pair, (openings[pair[0]], 100 - openings[pair[0]]), strict=True
            )
        }
        results = await asyncio.gather(
            *(
                self._send_pair(
                    pair,
                    openings[pair[0]],
                    force_close or unavailable or demand == 0,
                    bypass_interval,
                )
                for pair in self.pairs
            )
        )
        confirmed = not unavailable and all(results) and self.confirmed()
        self.status = (
            "confirmed"
            if confirmed
            else (
                "unavailable"
                if unavailable
                else ("failed" if self._failed else "pending")
            )
        )
        return confirmed

    async def close(self, *, terminal=False):
        async with self._lock:
            self._terminal |= terminal
            return await self._apply(0, force_close=True, bypass_interval=terminal)
