"""Standalone SmartPI calculator. Publishes sensors; never controls devices."""

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_TARGET
from .coordinator import CalculatorCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass, entry):
    coordinator = CalculatorCoordinator(hass, entry)
    await coordinator.initialize()
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def source_changed(event):
        old, new = event.data.get("old_state"), event.data.get("new_state")
        invalid = new is None or new.state in ("unknown", "unavailable")
        recovered = old is None or old.state in ("unknown", "unavailable")
        if (
            invalid
            or recovered
            or event.data["entity_id"] == coordinator.settings[CONF_TARGET]
        ):
            entry.async_create_background_task(
                hass,
                coordinator.async_request_refresh(),
                "SmartPI input refresh",
            )

    entry.async_on_unload(
        async_track_state_change_event(
            hass, coordinator.source_entities, source_changed
        )
    )
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.save()
    return unloaded
