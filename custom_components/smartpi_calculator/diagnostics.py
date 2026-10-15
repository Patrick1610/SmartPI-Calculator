"""Download model and configuration diagnostics without source identifiers."""

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_CLOSING, CONF_OPENING, SOURCE_KEYS, UPSTREAM_COMMIT


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "upstream_commit": UPSTREAM_COMMIT,
        "configuration": async_redact_data(
            coordinator.settings, {*SOURCE_KEYS, CONF_OPENING, CONF_CLOSING}
        ),
        "available": coordinator.last_update_success,
        "demand_percent": coordinator.data if coordinator.last_update_success else None,
        "model": coordinator.model_diagnostics(),
        "control_mode": coordinator.mode,
        "valve_status": coordinator.valves.status
        if coordinator.valves
        else "not_configured",
    }
