"""Constants for the standalone host, not the upstream controller."""

DOMAIN = "smartpi_calculator"
UPSTREAM_COMMIT = "6a131df56cf3cfe2974f78f55f33ce8a095076b3"
CONF_TEMPERATURE = "temperature_entity"
CONF_TARGET = "target_entity"
CONF_OUTDOOR = "outdoor_entity"
CONF_OUTPUT_APPLIED = "output_applied"
CONF_MODE = "control_mode"
CONF_OPENING = "opening_entities"
CONF_CLOSING = "closing_entities"
MODE_DIRECT = "direct_valve_control"
MODE_EXTERNAL = "manual_external_control"
MODE_DISABLED = "learning_disabled"
SAMPLE_SECONDS = 30
CYCLE_MINUTES = 10
SOURCE_KEYS = (CONF_TEMPERATURE, CONF_TARGET, CONF_OUTDOOR)


def control_mode(settings):
    """Old installations never acquire actuator permission through migration."""
    return settings.get(CONF_MODE) or (
        MODE_EXTERNAL if settings.get(CONF_OUTPUT_APPLIED, False) else MODE_DISABLED
    )
