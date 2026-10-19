"""Constants for the standalone host, not the upstream controller."""

import math

from .vendor.smartpi.const import HYST_LOWER_C, HYST_UPPER_C

DOMAIN = "smartpi_calculator"
UPSTREAM_COMMIT = "6a131df56cf3cfe2974f78f55f33ce8a095076b3"
CONF_TEMPERATURE = "temperature_entity"
CONF_TARGET = "target_entity"
CONF_OUTDOOR = "outdoor_entity"
CONF_OUTPUT_APPLIED = "output_applied"
CONF_MODE = "control_mode"
CONF_OPENING = "opening_entities"
CONF_CLOSING = "closing_entities"
CONF_MIN_OPENING = "minimum_openings"
CONF_HYSTERESIS_ON = "hysteresis_on"
CONF_HYSTERESIS_OFF = "hysteresis_off"
DEFAULT_HYSTERESIS_ON = HYST_LOWER_C
DEFAULT_HYSTERESIS_OFF = HYST_UPPER_C
MODE_DIRECT = "direct_valve_control"
MODE_EXTERNAL = "manual_external_control"
MODE_DISABLED = "learning_disabled"
SAMPLE_SECONDS = 30
CYCLE_MINUTES = 10
SOURCE_KEYS = (CONF_TEMPERATURE, CONF_TARGET, CONF_OUTDOOR)


def valve_minima(settings):
    """Minimum offsets the opening command, not the normalized PI demand."""
    openings = settings.get(CONF_OPENING, [])
    raw = settings.get(CONF_MIN_OPENING, {})
    if not isinstance(raw, dict) or set(raw).difference(openings):
        raise ValueError("invalid_minimum_opening")
    values = {}
    for entity in openings:
        value = raw.get(entity, 0)
        if isinstance(value, bool):
            raise ValueError("invalid_minimum_opening")
        try:
            value = float(value)
        except (ValueError, TypeError) as err:
            raise ValueError("invalid_minimum_opening") from err
        if not math.isfinite(value) or not 0 <= value < 100:
            raise ValueError("invalid_minimum_opening")
        values[entity] = value
    return values


def hysteresis_margins(settings):
    """Nonnegative distances below/above target; preserve upstream defaults."""
    values = []
    for key, default in (
        (CONF_HYSTERESIS_ON, DEFAULT_HYSTERESIS_ON),
        (CONF_HYSTERESIS_OFF, DEFAULT_HYSTERESIS_OFF),
    ):
        raw = settings.get(key, default)
        try:
            value = float(raw)
        except (ValueError, TypeError) as err:
            raise ValueError("invalid_hysteresis") from err
        if isinstance(raw, bool) or not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError("invalid_hysteresis")
        values.append(value)
    if sum(values) <= 0:
        raise ValueError("invalid_hysteresis")
    return tuple(values)


def control_mode(settings):
    """Old installations never acquire actuator permission through migration."""
    return settings.get(CONF_MODE) or (
        MODE_EXTERNAL if settings.get(CONF_OUTPUT_APPLIED, False) else MODE_DISABLED
    )
