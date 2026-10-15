"""Standalone SmartPI calculator with explicit optional number-only outputs."""

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_MODE, CONF_OUTPUT_APPLIED, CONF_TARGET, DOMAIN, control_mode
from .coordinator import CalculatorCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_migrate_entry(hass, entry):
    if entry.version == 1:
        data, options = dict(entry.data), dict(entry.options)
        data[CONF_MODE] = control_mode({**data, **options})
        data.pop(CONF_OUTPUT_APPLIED, None)
        options.pop(CONF_OUTPUT_APPLIED, None)
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=2
        )
    return entry.version == 2


async def async_setup_entry(hass, entry):
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    if (
        device
        and device.manufacturer == "Unofficial derivative of KipK / gael1980 SmartPI"
    ):
        registry.async_update_device(
            device.id, manufacturer=None, model="SmartPI Calculator"
        )
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

    async def stopping(event):
        await coordinator.shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stopping)
    )
    return True


async def _reload(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.shutdown()
    return unloaded
