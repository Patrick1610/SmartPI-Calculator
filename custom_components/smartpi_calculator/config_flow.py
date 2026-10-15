"""Select three source entities, with explicit opt-in to assumed application."""

import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import CONF_OUTPUT_APPLIED, DOMAIN, SOURCE_KEYS


def schema(defaults, *, options=False):
    fields = {}
    if not options:
        fields[vol.Required(CONF_NAME, default="Living Room SmartPI")] = str
    for key in SOURCE_KEYS:
        fields[vol.Required(key, default=defaults.get(key, vol.UNDEFINED))] = (
            EntitySelector(
                EntitySelectorConfig(domain=["sensor", "number", "input_number"])
            )
        )
    if options:
        fields[
            vol.Required(
                CONF_OUTPUT_APPLIED, default=defaults.get(CONF_OUTPUT_APPLIED, False)
            )
        ] = bool
    return vol.Schema(fields)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(user_input[SOURCE_KEYS[0]])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            room_source = user_input[SOURCE_KEYS[0]]
            for other in self.hass.config_entries.async_entries(DOMAIN):
                if (
                    other.entry_id != self.config_entry.entry_id
                    and {**other.data, **other.options}[SOURCE_KEYS[0]] == room_source
                ):
                    return self.async_abort(reason="already_configured")
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                unique_id=room_source,
            )
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=schema(defaults, options=True)
        )
