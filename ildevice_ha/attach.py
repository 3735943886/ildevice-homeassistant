"""Build the IL consumer for a host: the Home Assistant integration itself, or another integration that embeds it.

An embedding integration passes its own `transport` (for example `core.memory.InProcessTransport` shared with an
in-process producer, so no broker is needed) and its own domain as `platform`, forwards the IL platforms to its
config entry, and hands each platform's `async_add_entities` to `hub.register_platform`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from homeassistant.core import HomeAssistant

from .const import (
    CONF_ALIASES,
    CONF_AUTO_ADD,
    CONF_DEVICES,
    CONF_IL_PREFIX,
    CONF_OFFLINE_GRACE,
    DOMAIN,
    with_defaults,
)
from .core import parse_aliases
from .core.transport import Transport
from .hub import IlHub

_LOGGER = logging.getLogger(__name__)


def build_hub(
    hass: HomeAssistant, options: Mapping, transport: Transport | None = None, platform: str = DOMAIN,
    entry_id: str | None = None,
) -> IlHub:
    """`options` are the integration's options (`const.CONF_*`); `transport` defaults to Home Assistant's `mqtt`."""
    options = with_defaults(options)
    aliases = parse_aliases(options[CONF_ALIASES])
    if aliases is None:
        _LOGGER.error("the legacy entity id option is not valid JSON aliases; ignoring it")
    return IlHub(
        hass,
        il_prefix=options[CONF_IL_PREFIX],
        offline_grace=options[CONF_OFFLINE_GRACE],
        aliases=aliases or {},
        auto_add=options[CONF_AUTO_ADD],
        allowed=set(options[CONF_DEVICES]),
        transport=transport,
        platform=platform,
        entry_id=entry_id,
    )
