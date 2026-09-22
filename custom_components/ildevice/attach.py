"""Build the IL consumer for a host: the Home Assistant integration itself, or another integration that embeds it.

An embedding integration passes its own `transport` (for example `core.memory.InProcessTransport` shared with an
in-process producer, so no broker is needed) and its own domain as `platform`, forwards the IL platforms to its
config entry, and hands each platform's `async_add_entities` to `hub.register_platform`.
"""

from __future__ import annotations

import json
import logging
from typing import Mapping

from homeassistant.core import HomeAssistant

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
from .core.transport import Transport
from .hub import IlHub

_LOGGER = logging.getLogger(__name__)


def parse_aliases(text: str) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        _LOGGER.error("the legacy entity id option is not valid JSON; ignoring it")
        return {}
    return data if isinstance(data, dict) else {}


def build_hub(
    hass: HomeAssistant, options: Mapping, transport: Transport | None = None, platform: str = DOMAIN,
    entry_id: str | None = None,
) -> IlHub:
    """`options` are the integration's options (`const.CONF_*`); `transport` defaults to Home Assistant's `mqtt`."""
    return IlHub(
        hass,
        il_prefix=options.get(CONF_IL_PREFIX, DEFAULT_IL_PREFIX),
        offline_grace=options.get(CONF_OFFLINE_GRACE, DEFAULT_OFFLINE_GRACE),
        aliases=parse_aliases(options.get(CONF_ALIASES, "")),
        auto_add=options.get(CONF_AUTO_ADD, False),
        allowed=set(options.get(CONF_DEVICES, [])),
        transport=transport,
        platform=platform,
        entry_id=entry_id,
    )
