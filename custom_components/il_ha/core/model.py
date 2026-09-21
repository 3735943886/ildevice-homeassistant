"""The IL consumer without Home Assistant: follow the descriptors, values, presence and rejections that arrive on a
transport, and tell a `Sink` what changed. A host (the Home Assistant integration, a test, another program) supplies
the transport, a timer and the sink; nothing here imports Home Assistant.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .descriptor import Descriptor, DescriptorError, parse_descriptor
from .plan import EntitySpec, availability_prop, plan_entities
from .topics import Topics, resolve_topics, state_topic
from .transport import CallLater, Message, Transport, Unsubscribe, text
from .values import decode_value

_LOGGER = logging.getLogger(__name__)


@dataclass
class Device:
    desc: Descriptor
    doc: dict
    topics: Topics
    specs: list[EntitySpec]
    values: dict[str, object] = field(default_factory=dict)
    online: bool = True
    entities: list = field(default_factory=list)
    """Whatever the sink built for the device's specs."""
    unsubs: list[Unsubscribe] = field(default_factory=list)
    offline_timer: Unsubscribe | None = None

    @property
    def availability(self) -> str | None:
        return availability_prop(self.desc)


class Sink(Protocol):
    """What a host does with the model's findings."""

    def discovered(self, desc: Descriptor) -> None:
        """A device the user has not added has appeared (offer it)."""

    def withdrawn(self, device_id: str) -> None:
        """A device's descriptor was removed: forget any offer for it."""

    async def replacing(self, known: Device) -> None:
        """A new descriptor for a known device: drop what was built for the old one."""

    async def ready(self, dev: Device, known: Device | None) -> None:
        """A device was set up (`known` is the version it replaced): build its entities."""

    async def removed(self, dev: Device) -> None:
        """A known device was removed by its producer."""

    def values_changed(self, dev: Device) -> None:
        """A value, the availability or the producer's presence changed."""

    def event(self, dev: Device, prop: str, kind: str) -> None:
        """One occurrence of an `event` property."""

    def rejected(self, dev: Device, data: dict) -> None:
        """The producer refused a command."""


class IlModel:
    def __init__(
        self,
        transport: Transport,
        sink: Sink,
        call_later: CallLater,
        il_prefix: str = "il",
        offline_grace: float = 0,
        aliases: Mapping[str, Mapping[str, str]] | None = None,
        auto_add: bool = True,
        allowed: set[str] | None = None,
    ) -> None:
        self.transport = transport
        self.sink = sink
        self.call_later = call_later
        self.il_prefix = il_prefix
        self.offline_grace = offline_grace
        self.aliases = aliases or {}
        self.auto_add = auto_add
        self.allowed = allowed if allowed is not None else set()
        self.devices: dict[str, Device] = {}
        self.offline_sources: set[str] = set()
        self._asked: set[str] = set()
        self._unsubs: list[Unsubscribe] = []
        self._lock = asyncio.Lock()

    # ---- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        self._unsubs = [
            await self.transport.subscribe(f"{self.il_prefix}/+", self._on_descriptor),
            await self.transport.subscribe(f"{self.il_prefix}/_producer/+", self._on_presence),
        ]

    async def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        for dev in list(self.devices.values()):
            self.release(dev)
        self.devices.clear()

    def release(self, dev: Device) -> None:
        for unsub in dev.unsubs:
            unsub()
        dev.unsubs.clear()
        if dev.offline_timer:
            dev.offline_timer()
            dev.offline_timer = None

    # ---- presence ----------------------------------------------------------------------

    def source_up(self, dev: Device) -> bool:
        """False while the producer of the device says it is offline (il-messages.md W-6)."""
        return dev.desc.source not in self.offline_sources

    def _on_presence(self, msg: Message) -> None:
        source = msg.topic.rsplit("/", 1)[-1]
        payload = text(msg.payload).strip().lower()
        if payload == "offline":
            self.offline_sources.add(source)
        elif payload == "online" or not payload:
            self.offline_sources.discard(source)
        else:
            return
        for dev in self.devices.values():
            if dev.desc.source == source:
                self.sink.values_changed(dev)

    # ---- descriptors -------------------------------------------------------------------

    async def _on_descriptor(self, msg: Message) -> None:
        device_id = msg.topic.rsplit("/", 1)[-1]
        async with self._lock:
            payload = text(msg.payload).strip()
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
                if desc.id not in self._asked:
                    self._asked.add(desc.id)
                    self.sink.discovered(desc)
                return
            known = self.devices.get(desc.id)
            if known is not None and known.doc == doc:
                return
            await self._setup_device(desc, doc, known)

    def _aliases_for(self, desc: Descriptor) -> dict[str, str]:
        merged = dict(self.aliases.get("*", {}))
        if desc.model:
            merged.update(self.aliases.get(desc.model, {}))
        return merged

    async def _setup_device(self, desc: Descriptor, doc: dict, known: Device | None) -> None:
        if known is not None:
            await self.sink.replacing(known)
            known.entities = []
            self.release(known)
        try:
            specs = plan_entities(desc, self._aliases_for(desc))
        except ValueError as err:
            _LOGGER.error("cannot plan %s: %s", desc.id, err)
            return
        dev = Device(desc=desc, doc=doc, topics=resolve_topics(desc, self.il_prefix), specs=specs)
        dev.online = dev.availability is None
        self.devices[desc.id] = dev
        for prop in desc.props:
            if desc.props[prop].type == "trigger":
                continue
            dev.unsubs.append(
                await self.transport.subscribe(state_topic(desc, dev.topics, prop), self._state_callback(dev, prop))
            )
        dev.unsubs.append(await self.transport.subscribe(dev.topics.reject, self._reject_callback(dev)))
        await self.sink.ready(dev, known)

    async def _remove_device(self, device_id: str) -> None:
        self._asked.discard(device_id)
        self.sink.withdrawn(device_id)
        dev = self.devices.pop(device_id, None)
        if dev is None:
            return
        await self.sink.removed(dev)
        dev.entities = []
        self.release(dev)

    # ---- values ------------------------------------------------------------------------

    def _state_callback(self, dev: Device, prop: str):
        def on_state(msg: Message) -> None:
            if dev.desc.props[prop].type == "event":
                # every message is one occurrence; a retained one is an old occurrence the broker
                # replays on (re)subscribe, not a new one
                kind = text(msg.payload).strip()
                if kind and not msg.retain:
                    self.sink.event(dev, prop, kind)
                return
            value = decode_value(dev.desc.props[prop], msg.payload)
            if value is None:
                dev.values.pop(prop, None)
            else:
                dev.values[prop] = value
            if prop == dev.availability:
                self._availability(dev, value is True)
            self.sink.values_changed(dev)

        return on_state

    def _availability(self, dev: Device, online: bool) -> None:
        if dev.offline_timer:
            dev.offline_timer()
            dev.offline_timer = None
        if online or self.offline_grace <= 0:
            dev.online = online
            return

        def go_offline() -> None:
            dev.offline_timer = None
            dev.online = False
            self.sink.values_changed(dev)

        dev.offline_timer = self.call_later(self.offline_grace, go_offline)

    def _reject_callback(self, dev: Device):
        def on_reject(msg: Message) -> None:
            try:
                body = json.loads(msg.payload)
            except ValueError:
                body = {"reason": text(msg.payload)}
            if not isinstance(body, dict):
                body = {"reason": str(body)}
            data = {"device_id": dev.desc.id, "prop": body.get("prop"), "code": body.get("code"), "reason": body.get("reason")}
            _LOGGER.warning("%s refused a command: %s", dev.desc.id, data)
            self.sink.rejected(dev, data)

        return on_reject
