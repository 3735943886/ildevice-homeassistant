"""IL devices in Home Assistant: any producer that publishes IL descriptors over MQTT
(rusthinq, rustuya, ...) gets its devices and entities, with no code per model."""

from __future__ import annotations

import json
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ALIASES,
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
    options = entry.options
    hub = IlHub(
        hass,
        il_prefix=options.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX),
        offline_grace=options.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE),
        aliases=_aliases(options.get(CONF_ALIASES, "")),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await hub.async_start()
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await hass.data[DOMAIN].pop(entry.entry_id).async_stop()
    return unloaded
