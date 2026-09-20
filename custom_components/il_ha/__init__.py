"""IL devices in Home Assistant: any producer that publishes IL descriptors over MQTT
(rusthinq, rustuya, ...) gets its devices and entities, with no code per model."""

from __future__ import annotations

import json
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ALIASES,
    CONF_AUTO_ADD,
    CONF_DEVICES,
    CONF_IL_PREFIX,
    CONF_OFFLINE_GRACE,
    DEFAULT_IL_PREFIX,
    DEFAULT_OFFLINE_GRACE,
    DOMAIN,
    PLATFORMS,
)
from .hub import IlHub

_LOGGER = logging.getLogger(__name__)


def _aliases(text: str) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        _LOGGER.error("the legacy entity id option is not valid JSON; ignoring it")
        return {}
    return data if isinstance(data, dict) else {}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if CONF_DEVICES not in entry.options:
        # An entry from before devices were asked about: the devices it already has stay.
        registry = dr.async_get(hass)
        known = sorted(
            ident[1]
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
            for ident in device.identifiers
            if ident[0] == DOMAIN
        )
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DEVICES: known})
    options = entry.options
    hub = IlHub(
        hass,
        il_prefix=options.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX),
        offline_grace=options.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE),
        aliases=_aliases(options.get(CONF_ALIASES, "")),
        auto_add=options.get(CONF_AUTO_ADD, False),
        allowed=set(options.get(CONF_DEVICES, [])),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await hub.async_start()
    return True


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device) -> bool:
    """Deleting a device in the UI forgets that the user added it; it is offered again when it next appears."""
    ids = {ident[1] for ident in device.identifiers if ident[0] == DOMAIN}
    devices = [d for d in entry.options.get(CONF_DEVICES, []) if d not in ids]
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DEVICES: devices})
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await hass.data[DOMAIN].pop(entry.entry_id).async_stop()
    return unloaded
