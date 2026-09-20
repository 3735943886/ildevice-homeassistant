"""Config flow: one instance, the IL prefix and how long to wait before calling a device offline."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_ALIASES,
    CONF_IL_PREFIX,
    CONF_OFFLINE_GRACE,
    DEFAULT_IL_PREFIX,
    DEFAULT_OFFLINE_GRACE,
    DOMAIN,
)


def _schema(prefix: str, grace: int, aliases: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_IL_PREFIX, default=prefix): str,
            vol.Required(CONF_OFFLINE_GRACE, default=grace): vol.All(int, vol.Range(min=0, max=3600)),
            vol.Optional(CONF_ALIASES, default=aliases): str,
        }
    )


def _check_aliases(text: str) -> bool:
    """Aliases are JSON: `{"<model or *>": {"<key>": "<legacy key>"}}`."""
    if not text.strip():
        return True
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and all(
        isinstance(v, dict) and all(isinstance(a, str) and isinstance(b, str) for a, b in v.items())
        for v in data.values()
    )


class IlConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            if _check_aliases(user_input.get(CONF_ALIASES, "")):
                return self.async_create_entry(title="Intermediate Layer", data={}, options=user_input)
            errors[CONF_ALIASES] = "bad_aliases"
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(DEFAULT_IL_PREFIX, DEFAULT_OFFLINE_GRACE, ""),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IlOptionsFlow()


class IlOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if _check_aliases(user_input.get(CONF_ALIASES, "")):
                return self.async_create_entry(data=user_input)
            errors[CONF_ALIASES] = "bad_aliases"
        o = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                o.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX),
                o.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE),
                o.get(CONF_ALIASES, ""),
            ),
            errors=errors,
        )
