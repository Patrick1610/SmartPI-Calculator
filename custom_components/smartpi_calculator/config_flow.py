"""Three exclusive modes; actuator selection is a separate explicit step."""

import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CLOSING,
    CONF_MODE,
    CONF_OPENING,
    CONF_OUTPUT_APPLIED,
    DOMAIN,
    MODE_DIRECT,
    MODE_DISABLED,
    MODE_EXTERNAL,
    SOURCE_KEYS,
    control_mode,
)
from .valve import valve_bindings


def schema(defaults, *, options=False):
    fields = {}
    if not options:
        fields[vol.Required(CONF_NAME, default="SmartPI")] = str
    for key in SOURCE_KEYS:
        fields[vol.Required(key, default=defaults.get(key, vol.UNDEFINED))] = (
            EntitySelector(
                EntitySelectorConfig(domain=["sensor", "number", "input_number"])
            )
        )
    fields[vol.Required(CONF_MODE, default=control_mode(defaults))] = SelectSelector(
        SelectSelectorConfig(
            mode=SelectSelectorMode.LIST,
            options=[
                SelectOptionDict(value=MODE_DISABLED, label="Learning disabled"),
                SelectOptionDict(value=MODE_DIRECT, label="Direct valve control"),
                SelectOptionDict(value=MODE_EXTERNAL, label="Manual external control"),
            ],
        )
    )
    return vol.Schema(fields)


def valve_schema(defaults):
    return vol.Schema(
        {
            vol.Required(key, default=defaults.get(key, [])): EntitySelector(
                EntitySelectorConfig(domain="number", multiple=True)
            )
            for key in (CONF_OPENING, CONF_CLOSING)
        }
    )


class ModeFlow:
    """Shared validation and pending state for creation and options."""

    _pending = None

    def _clean(self, values):
        values = dict(values)
        values[CONF_MODE] = control_mode(values)
        values.pop(CONF_OUTPUT_APPLIED, None)
        if values[CONF_MODE] != MODE_DIRECT:
            values.pop(CONF_OPENING, None)
            values.pop(CONF_CLOSING, None)
        return values

    async def async_step_valves(self, user_input=None):
        errors = {}
        if user_input is not None:
            values = {**self._pending, **user_input}
            try:
                valve_bindings(self.hass, values, exclude_entry=self._entry_id())
            except ValueError as err:
                errors["base"] = str(err)
            else:
                return self._finish(values)
        return self.async_show_form(
            step_id="valves",
            data_schema=valve_schema(
                user_input if user_input is not None else self._defaults()
            ),
            errors=errors,
        )


class ConfigFlow(ModeFlow, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def _entry_id(self):
        return None

    def _defaults(self):
        return {}

    def _finish(self, values):
        return self.async_create_entry(title=values[CONF_NAME], data=values)

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(user_input[SOURCE_KEYS[0]])
            self._abort_if_unique_id_configured()
            self._pending = self._clean(user_input)
            if self._pending[CONF_MODE] == MODE_DIRECT:
                return await self.async_step_valves()
            return self._finish(self._pending)
        return self.async_show_form(step_id="user", data_schema=schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(ModeFlow, config_entries.OptionsFlow):
    def _entry_id(self):
        return self.config_entry.entry_id

    def _defaults(self):
        return {**self.config_entry.data, **self.config_entry.options}

    def _finish(self, values):
        self.hass.config_entries.async_update_entry(
            self.config_entry, unique_id=values[SOURCE_KEYS[0]]
        )
        return self.async_create_entry(title="", data=values)

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            room_source = user_input[SOURCE_KEYS[0]]
            for other in self.hass.config_entries.async_entries(DOMAIN):
                if (
                    other.entry_id != self.config_entry.entry_id
                    and {**other.data, **other.options}[SOURCE_KEYS[0]] == room_source
                ):
                    return self.async_abort(reason="already_configured")
            self._pending = self._clean(user_input)
            if self._pending[CONF_MODE] == MODE_DIRECT:
                return await self.async_step_valves()
            return self._finish({**self._pending, CONF_OPENING: [], CONF_CLOSING: []})
        return self.async_show_form(
            step_id="init", data_schema=schema(self._defaults(), options=True)
        )
