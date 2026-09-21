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
    CONF_IL_PREFIX,
    DEFAULT_IL_PREFIX,
    DOMAIN,
    HUB_PREFIX,
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
            if ident[0] == DOMAIN and not ident[1].startswith(HUB_PREFIX)
        )
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DEVICES: known})
    # every entry is a hub (one topic prefix): a device of its own that its devices are placed under
    prefix = entry.options.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX)
    hub_identifier = f"{HUB_PREFIX}{prefix}"
    registry = dr.async_get(hass)
    for stale in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if (DOMAIN, hub_identifier) not in stale.identifiers and any(
            i[0] == DOMAIN and i[1].startswith(HUB_PREFIX) for i in stale.identifiers
        ):
            registry.async_remove_device(stale.id)  # the prefix was changed
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_identifier)},
        name=prefix,
        manufacturer="ildevice",
        model="Hub",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    hub = build_hub(hass, entry.options, entry_id=entry.entry_id, hub_identifier=hub_identifier)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await hub.async_start()
    return True


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device) -> bool:
    """Deleting a device in the UI forgets that the user added it; it is offered again when it next appears."""
    ids = {ident[1] for ident in device.identifiers if ident[0] == DOMAIN}
    if any(i.startswith(HUB_PREFIX) for i in ids):
        return False  # a hub goes with its entry
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
