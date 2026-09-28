"""Config flow: one entry per IL prefix, how long to wait before calling a device offline, and the discovery offers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_ALIASES,
    CONF_AUTO_ADD,
    CONF_DEVICES,
    CONF_IL_PREFIX,
    CONF_OFFLINE_GRACE,
    DEFAULT_OPTIONS,
    DOMAIN,
    MAX_OFFLINE_GRACE,
    with_defaults,
)
from .core import parse_aliases

# Shown in the aliases field description. Passed as a placeholder because hassfest rejects
# literal braces and angle brackets in strings.json.
_ALIASES_EXAMPLE = '{"<model or *>": {"<entity key>": "<key an earlier integration used>"}}'


def _schema(options: Mapping[str, Any]) -> vol.Schema:
    """The form, filled in with `options` (an entry's, or the defaults)."""
    o = with_defaults(options)
    return vol.Schema(
        {
            vol.Required(CONF_IL_PREFIX, default=o[CONF_IL_PREFIX]): str,
            vol.Required(CONF_OFFLINE_GRACE, default=o[CONF_OFFLINE_GRACE]): vol.All(
                int, vol.Range(min=0, max=MAX_OFFLINE_GRACE)
            ),
            vol.Optional(CONF_ALIASES, default=o[CONF_ALIASES]): str,
            vol.Required(CONF_AUTO_ADD, default=o[CONF_AUTO_ADD]): bool,
        }
    )


def _validate(
    hass: HomeAssistant, user_input: dict[str, Any], entry_id: str | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """The input with its prefix cleaned, and the errors in it. `entry_id` is the entry being edited."""
    prefix = user_input[CONF_IL_PREFIX].strip().strip("/")
    errors: dict[str, str] = {}
    if not prefix:
        errors[CONF_IL_PREFIX] = "bad_prefix"
    elif any(
        e.entry_id != entry_id and with_defaults(e.options)[CONF_IL_PREFIX] == prefix
        for e in hass.config_entries.async_entries(DOMAIN)
    ):
        errors[CONF_IL_PREFIX] = "duplicate_prefix"
    if parse_aliases(user_input.get(CONF_ALIASES, "")) is None:
        errors[CONF_ALIASES] = "bad_aliases"
    return {**user_input, CONF_IL_PREFIX: prefix}, errors


class IlConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _device: dict[str, Any]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Each entry has its own topic prefix (`il/tuya`, `il/thinq`, ...); add the integration again for another."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input, errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_IL_PREFIX], data={}, options={**user_input, CONF_DEVICES: []}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(DEFAULT_OPTIONS),
            errors=errors,
            description_placeholders={"aliases_example": _ALIASES_EXAMPLE},
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
        entry = self.config_entry
        if user_input is not None:
            user_input, errors = _validate(self.hass, user_input, entry.entry_id)
            if not errors:
                devices = set(entry.options.get(CONF_DEVICES, []))
                hub = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
                if hub is not None and not user_input[CONF_AUTO_ADD]:
                    # Turning "add automatically" off keeps the devices it added.
                    devices |= set(hub.devices)
                self.hass.config_entries.async_update_entry(entry, title=user_input[CONF_IL_PREFIX])
                return self.async_create_entry(data={**user_input, CONF_DEVICES: sorted(devices)})
        return self.async_show_form(step_id="init", data_schema=_schema(entry.options), errors=errors)
