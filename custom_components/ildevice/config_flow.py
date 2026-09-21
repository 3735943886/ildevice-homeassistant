"""Config flow: one instance, the IL prefix and how long to wait before calling a device offline."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_ALIASES,
    CONF_AUTO_ADD,
    CONF_DEVICES,
    CONF_IL_PREFIX,
    CONF_OFFLINE_GRACE,
    DEFAULT_IL_PREFIX,
    DEFAULT_OFFLINE_GRACE,
    DOMAIN,
)


def _schema(prefix: str, grace: int, aliases: str, auto_add: bool) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_IL_PREFIX, default=prefix): str,
            vol.Required(CONF_OFFLINE_GRACE, default=grace): vol.All(int, vol.Range(min=0, max=3600)),
            vol.Optional(CONF_ALIASES, default=aliases): str,
            vol.Required(CONF_AUTO_ADD, default=auto_add): bool,
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


def _prefix_in_use(hass, prefix: str, ignore_entry_id: str | None = None) -> bool:
    return any(
        e.entry_id != ignore_entry_id and e.options.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX) == prefix
        for e in hass.config_entries.async_entries(DOMAIN)
    )


def _clean_prefix(text: str) -> str:
    return text.strip().strip("/")


class IlConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Each entry is a hub: one topic prefix (`il/tuya`, `il/thinq`, ...). Add the integration again for another."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**user_input, CONF_IL_PREFIX: _clean_prefix(user_input[CONF_IL_PREFIX])}
            prefix = user_input[CONF_IL_PREFIX]
            if not prefix:
                errors[CONF_IL_PREFIX] = "bad_prefix"
            elif _prefix_in_use(self.hass, prefix):
                errors[CONF_IL_PREFIX] = "duplicate_prefix"
            if not _check_aliases(user_input.get(CONF_ALIASES, "")):
                errors[CONF_ALIASES] = "bad_aliases"
            if not errors:
                return self.async_create_entry(title=prefix, data={}, options={**user_input, CONF_DEVICES: []})
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(DEFAULT_IL_PREFIX, DEFAULT_OFFLINE_GRACE, "", False),
            errors=errors,
        )

    async def async_step_integration_discovery(self, discovery_info: dict[str, Any]) -> ConfigFlowResult:
        """A device published a descriptor and the user has not added it yet."""
        await self.async_set_unique_id(discovery_info["device_id"])
        self._abort_if_unique_id_configured()  # also true for a device the user chose to ignore
        self._device = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info["label"]}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        device = self._device
        if user_input is not None:
            hub = self.hass.config_entries.async_get_entry(device.get("entry_id") or "")
            if hub is None:
                return self.async_abort(reason="no_hub")
            devices = list(hub.options.get(CONF_DEVICES, []))
            if device["device_id"] not in devices:
                devices.append(device["device_id"])
            self.hass.config_entries.async_update_entry(hub, options={**hub.options, CONF_DEVICES: devices})
            return self.async_abort(reason="device_added")
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": device["label"], "model": device["model"] or "?"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IlOptionsFlow()


class IlOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**user_input, CONF_IL_PREFIX: _clean_prefix(user_input[CONF_IL_PREFIX])}
            prefix = user_input[CONF_IL_PREFIX]
            if not prefix:
                errors[CONF_IL_PREFIX] = "bad_prefix"
            elif _prefix_in_use(self.hass, prefix, self.config_entry.entry_id):
                errors[CONF_IL_PREFIX] = "duplicate_prefix"
            elif _check_aliases(user_input.get(CONF_ALIASES, "")):
                devices = set(self.config_entry.options.get(CONF_DEVICES, []))
                hub = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                if hub is not None and not user_input[CONF_AUTO_ADD]:
                    # Turning "add automatically" off keeps the devices it added.
                    devices |= set(hub.devices)
                self.hass.config_entries.async_update_entry(self.config_entry, title=prefix)
                return self.async_create_entry(data={**user_input, CONF_DEVICES: sorted(devices)})
            else:
                errors[CONF_ALIASES] = "bad_aliases"
        o = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                o.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX),
                o.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE),
                o.get(CONF_ALIASES, ""),
                o.get(CONF_AUTO_ADD, False),
            ),
            errors=errors,
        )
