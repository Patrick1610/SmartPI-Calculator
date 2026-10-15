"""Download model and configuration diagnostics without source identifiers."""

from homeassistant.components.diagnostics import async_redact_data

from .const import SOURCE_KEYS, UPSTREAM_COMMIT


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "upstream_commit": UPSTREAM_COMMIT,
        "configuration": async_redact_data(coordinator.settings, set(SOURCE_KEYS)),
        "available": coordinator.last_update_success,
        "demand_percent": coordinator.data if coordinator.last_update_success else None,
        "model": coordinator.engine.diagnostics(),
    }
