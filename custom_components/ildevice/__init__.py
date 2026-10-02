"""IL devices in Home Assistant: any producer that publishes IL descriptors over MQTT
(rusthinq, rustuya, ...) gets its devices and entities, with no code per model.

Importing this package needs no Home Assistant (`ildevice.core` is usable on its own); Home Assistant is imported
when the integration is set up. A host that embeds the consumer calls `attach.build_hub`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_DEVICES, DOMAIN, PLATFORMS, with_defaults

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .attach import build_hub

    hub = build_hub(hass, entry.options, entry_id=entry.entry_id)
    hub.options = dict(entry.options)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await hub.async_start()
    return True


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry) -> bool:
    """Deleting a device in the UI forgets that the user added it; it is offered again when it next appears."""
    ids = {ident for domain, ident in device.identifiers if domain == DOMAIN}
    devices = [d for d in entry.options.get(CONF_DEVICES, []) if d not in ids]
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DEVICES: devices})
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A change of the added devices alone is applied in place (`IlHub.async_set_allowed`), and a title change needs
    nothing; any other option reloads the entry."""
    hub = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if hub is not None and _without_devices(entry.options) == _without_devices(hub.options):
        hub.options = dict(entry.options)
        await hub.async_set_allowed(set(with_defaults(entry.options)[CONF_DEVICES]))
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _without_devices(options) -> dict:
    return {k: v for k, v in with_defaults(options).items() if k != CONF_DEVICES}


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await hass.data[DOMAIN].pop(entry.entry_id).async_stop()
    return unloaded
