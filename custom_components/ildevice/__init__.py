"""IL devices in Home Assistant: any producer that publishes IL descriptors over MQTT
(rusthinq, rustuya, ...) gets its devices and entities, with no code per model.

Importing this package needs no Home Assistant (`ildevice.core` is usable on its own); Home Assistant is imported
when the integration is set up. A host that embeds the consumer calls `attach.attach_hub`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import (
    CONF_DEVICES,
    DOMAIN,
    PLATFORMS,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.helpers import device_registry as dr

    from .attach import build_hub

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
    hub = build_hub(hass, entry.options)
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
