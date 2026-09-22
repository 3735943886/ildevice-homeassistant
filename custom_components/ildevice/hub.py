"""The Home Assistant side of the IL consumer: entities, the registry, discovery offers and dispatcher signals.
What the messages mean is `core.model.IlModel`'s business; this class is its `Sink`."""

from __future__ import annotations

import logging
from typing import Callable

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, EVENT_COMMAND_REJECTED
from .core import Descriptor, Device, IlModel
from .core.transport import Transport
from .mqtt_transport import HaMqttTransport

_LOGGER = logging.getLogger(__name__)

__all__ = ["Device", "IlHub", "event_signal", "signal"]


def signal(device_id: str) -> str:
    return f"{DOMAIN}_update_{device_id}"


def event_signal(device_id: str, prop: str) -> str:
    """One dispatch per occurrence of an `event` property; the argument is its kind."""
    return f"{DOMAIN}_event_{device_id}_{prop}"


class IlHub:
    def __init__(
        self,
        hass: HomeAssistant,
        il_prefix: str,
        offline_grace: int,
        aliases: dict[str, dict[str, str]],
        auto_add: bool = True,
        allowed: set[str] | None = None,
        transport: Transport | None = None,
        platform: str = DOMAIN,
        entry_id: str | None = None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.transport: Transport = transport or HaMqttTransport(hass)
        self.platform = platform
        """The integration domain the entities are registered under (`ildevice`, or a host that embeds this)."""
        self.model = IlModel(
            self.transport,
            self,
            self._call_later,
            il_prefix=il_prefix,
            offline_grace=offline_grace,
            aliases=aliases,
            auto_add=auto_add,
            allowed=allowed,
        )
        self._adders: dict[str, Callable] = {}
        self._pending: dict[str, list] = {}

    def _call_later(self, delay: float, fn: Callable[[], None]) -> CALLBACK_TYPE:
        @callback
        def fire(_now) -> None:
            fn()

        return async_call_later(self.hass, delay, fire)

    # ---- the model's view --------------------------------------------------------------

    @property
    def devices(self) -> dict[str, Device]:
        return self.model.devices

    @property
    def il_prefix(self) -> str:
        return self.model.il_prefix

    def source_up(self, dev: Device) -> bool:
        return self.model.source_up(dev)

    async def async_start(self) -> None:
        await self.model.start()

    async def async_stop(self) -> None:
        await self.model.stop()

    # ---- platforms ---------------------------------------------------------------------

    def register_platform(self, platform: str, add_entities: Callable) -> None:
        """A platform module is ready: hand it the entities that were waiting for it."""
        self._adders[platform] = add_entities
        waiting = self._pending.pop(platform, [])
        if waiting:
            add_entities(waiting)

    def _add(self, platform: str, entity) -> None:
        adder = self._adders.get(platform)
        if adder is None:
            self._pending.setdefault(platform, []).append(entity)
        else:
            adder([entity])

    # ---- Sink --------------------------------------------------------------------------

    def discovered(self, desc: Descriptor) -> None:
        """A device the user has not added: offer it (add / ignore) instead of creating it."""
        discovery_flow.async_create_flow(
            self.hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={"entry_id": self.entry_id, "device_id": desc.id, "label": desc.label or desc.model or desc.id, "model": desc.model or ""},
        )

    def withdrawn(self, device_id: str) -> None:
        for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            if flow["context"].get("unique_id") == device_id:
                self.hass.config_entries.flow.async_abort(flow["flow_id"])

    async def replacing(self, known: Device) -> None:
        await self._drop_entities(known)

    async def ready(self, dev: Device, known: Device | None) -> None:
        from .entity import ENTITY_CLASSES

        # a spec that is gone from the new plan leaves the registry too
        if known is not None:
            self._forget_unique_ids({s.unique_id for s in known.specs} - {s.unique_id for s in dev.specs})
        for spec in dev.specs:
            entity = ENTITY_CLASSES[spec.platform](self, dev, spec)
            dev.entities.append(entity)
            self._add(spec.platform, entity)

    async def removed(self, dev: Device) -> None:
        await self._drop_entities(dev)
        self._forget_unique_ids({s.unique_id for s in dev.specs})
        self._forget_device(dev.desc.id)

    def values_changed(self, dev: Device) -> None:
        async_dispatcher_send(self.hass, signal(dev.desc.id))

    def event(self, dev: Device, prop: str, kind: str) -> None:
        async_dispatcher_send(self.hass, event_signal(dev.desc.id, prop), kind)

    def rejected(self, dev: Device, data: dict) -> None:
        self.hass.bus.async_fire(EVENT_COMMAND_REJECTED, data)

    async def _drop_entities(self, dev: Device) -> None:
        entities, dev.entities = dev.entities, []
        for entity in entities:
            if entity.hass is not None:
                await entity.async_remove()

    def _forget_device(self, device_id: str) -> None:
        """The producer withdrew the device: its registry entry goes too, not only its entities."""
        if self.entry_id is None:
            return
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(registry, self.entry_id):
            if (self.platform, device_id) in device.identifiers:
                registry.async_remove_device(device.id)

    def _forget_unique_ids(self, unique_ids: set[str]) -> None:
        registry = er.async_get(self.hass)
        for entry in list(registry.entities.values()):
            if entry.platform == self.platform and entry.unique_id in unique_ids:
                registry.async_remove(entry.entity_id)
