"""The hub: follows the descriptors on MQTT, keeps every device's values and creates its entities."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components import mqtt
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, EVENT_COMMAND_REJECTED
from .core import Descriptor, DescriptorError, EntitySpec, Topics, decode_value, parse_descriptor, plan_entities, resolve_topics
from .core.plan import availability_prop
from .core.topics import state_topic

_LOGGER = logging.getLogger(__name__)


def signal(device_id: str) -> str:
    return f"{DOMAIN}_update_{device_id}"


def event_signal(device_id: str, prop: str) -> str:
    """One dispatch per occurrence of an `event` property; the argument is its kind."""
    return f"{DOMAIN}_event_{device_id}_{prop}"


@dataclass
class Device:
    desc: Descriptor
    doc: dict
    topics: Topics
    specs: list[EntitySpec]
    values: dict[str, object] = field(default_factory=dict)
    online: bool = True
    entities: list = field(default_factory=list)
    unsubs: list[CALLBACK_TYPE] = field(default_factory=list)
    offline_timer: CALLBACK_TYPE | None = None

    @property
    def availability(self) -> str | None:
        return availability_prop(self.desc)


class IlHub:
    def __init__(
        self,
        hass: HomeAssistant,
        il_prefix: str,
        offline_grace: int,
        aliases: dict[str, dict[str, str]],
        auto_add: bool = True,
        allowed: set[str] | None = None,
    ) -> None:
        self.hass = hass
        self.il_prefix = il_prefix
        self.offline_grace = offline_grace
        self.aliases = aliases
        self.auto_add = auto_add
        self.allowed = allowed or set()
        self._asked: set[str] = set()
        self.devices: dict[str, Device] = {}
        self._adders: dict[str, Callable] = {}
        self._pending: dict[str, list] = {}
        self._unsub: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()

    # ---- lifecycle ---------------------------------------------------------------------

    async def async_start(self) -> None:
        self._unsub = await mqtt.async_subscribe(self.hass, f"{self.il_prefix}/+", self._on_descriptor)

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        for dev in list(self.devices.values()):
            self._release(dev)
        self.devices.clear()

    def _release(self, dev: Device) -> None:
        for unsub in dev.unsubs:
            unsub()
        dev.unsubs.clear()
        if dev.offline_timer:
            dev.offline_timer()
            dev.offline_timer = None

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

    # ---- descriptors -------------------------------------------------------------------

    async def _on_descriptor(self, msg) -> None:
        device_id = msg.topic.rsplit("/", 1)[-1]
        async with self._lock:
            payload = msg.payload.strip() if isinstance(msg.payload, str) else msg.payload.decode().strip()
            if not payload:
                await self._remove_device(device_id)
                return
            try:
                doc = json.loads(payload)
                desc = parse_descriptor(doc)
            except (ValueError, DescriptorError) as err:
                _LOGGER.warning("ignoring the descriptor on %s: %s", msg.topic, err)
                return
            if desc.id != device_id:
                _LOGGER.warning("the descriptor on %s carries the id %s", msg.topic, desc.id)
            if not self.auto_add and desc.id not in self.allowed:
                self._discover(desc)
                return
            known = self.devices.get(desc.id)
            if known is not None and known.doc == doc:
                return
            await self._setup_device(desc, doc, known)

    def _discover(self, desc: Descriptor) -> None:
        """A device the user has not added: offer it (add / ignore) instead of creating it."""
        if desc.id in self._asked:
            return
        self._asked.add(desc.id)
        discovery_flow.async_create_flow(
            self.hass,
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={
                "device_id": desc.id,
                "label": desc.label or desc.model or desc.id,
                "model": desc.model or "",
            },
        )

    def _aliases_for(self, desc: Descriptor) -> dict[str, str]:
        merged = dict(self.aliases.get("*", {}))
        if desc.model:
            merged.update(self.aliases.get(desc.model, {}))
        return merged

    async def _setup_device(self, desc: Descriptor, doc: dict, known: Device | None) -> None:
        from .entity import ENTITY_CLASSES

        if known is not None:
            await self._drop_entities(known, keep=None)
            self._release(known)
        try:
            specs = plan_entities(desc, self._aliases_for(desc))
        except ValueError as err:
            _LOGGER.error("cannot plan %s: %s", desc.id, err)
            return
        dev = Device(desc=desc, doc=doc, topics=resolve_topics(desc, self.il_prefix), specs=specs)
        dev.online = dev.availability is None
        self.devices[desc.id] = dev

        # a spec that is gone from the new plan leaves the registry too
        if known is not None:
            gone = {s.unique_id for s in known.specs} - {s.unique_id for s in specs}
            self._forget_unique_ids(gone)

        for prop in desc.props:
            if desc.props[prop].type == "trigger":
                continue
            topic = state_topic(desc, dev.topics, prop)
            dev.unsubs.append(
                await mqtt.async_subscribe(self.hass, topic, self._state_callback(dev, prop))
            )
        dev.unsubs.append(
            await mqtt.async_subscribe(self.hass, dev.topics.reject, self._reject_callback(dev))
        )
        for spec in specs:
            entity = ENTITY_CLASSES[spec.platform](self, dev, spec)
            dev.entities.append(entity)
            self._add(spec.platform, entity)

    async def _drop_entities(self, dev: Device, keep) -> None:
        entities, dev.entities = dev.entities, []
        for entity in entities:
            if entity.hass is not None:
                await entity.async_remove()

    def _forget_unique_ids(self, unique_ids: set[str]) -> None:
        registry = er.async_get(self.hass)
        for entry in list(registry.entities.values()):
            if entry.platform == DOMAIN and entry.unique_id in unique_ids:
                registry.async_remove(entry.entity_id)

    async def _remove_device(self, device_id: str) -> None:
        self._asked.discard(device_id)
        for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            if flow["context"].get("unique_id") == device_id:
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
        dev = self.devices.pop(device_id, None)
        if dev is None:
            return
        await self._drop_entities(dev, keep=None)
        self._release(dev)
        self._forget_unique_ids({s.unique_id for s in dev.specs})

    # ---- values ------------------------------------------------------------------------

    def _state_callback(self, dev: Device, prop: str):
        @callback
        def on_state(msg) -> None:
            if dev.desc.props[prop].type == "event":
                # every message is one occurrence; a retained one is an old occurrence the broker
                # replays on (re)subscribe, not a new one
                kind = msg.payload.decode("utf-8", "replace") if isinstance(msg.payload, (bytes, bytearray)) else msg.payload
                kind = kind.strip()
                if kind and not msg.retain:
                    async_dispatcher_send(self.hass, event_signal(dev.desc.id, prop), kind)
                return
            value = decode_value(dev.desc.props[prop], msg.payload)
            if value is None:
                dev.values.pop(prop, None)
            else:
                dev.values[prop] = value
            if prop == dev.availability:
                self._availability(dev, value is True)
            async_dispatcher_send(self.hass, signal(dev.desc.id))

        return on_state

    def _availability(self, dev: Device, online: bool) -> None:
        if dev.offline_timer:
            dev.offline_timer()
            dev.offline_timer = None
        if online or self.offline_grace <= 0:
            dev.online = online
            return

        @callback
        def go_offline(_now) -> None:
            dev.offline_timer = None
            dev.online = False
            async_dispatcher_send(self.hass, signal(dev.desc.id))

        dev.offline_timer = async_call_later(self.hass, self.offline_grace, go_offline)

    def _reject_callback(self, dev: Device):
        @callback
        def on_reject(msg) -> None:
            try:
                body = json.loads(msg.payload)
            except ValueError:
                body = {"reason": str(msg.payload)}
            data = {"device_id": dev.desc.id, "prop": body.get("prop"), "reason": body.get("reason")}
            _LOGGER.warning("%s refused a command: %s", dev.desc.id, data)
            self.hass.bus.async_fire(EVENT_COMMAND_REJECTED, data)

        return on_reject
