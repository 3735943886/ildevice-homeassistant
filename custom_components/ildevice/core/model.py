"""The IL consumer without Home Assistant: follow the descriptors, values, presence and rejections that arrive on a
transport, and tell a `Sink` what changed. A host (the Home Assistant integration, a test, another program) supplies
the transport, a timer and the sink; nothing here imports Home Assistant.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .descriptor import Descriptor, Prop, parse_descriptor
from .plan import EntitySpec, availability_prop, plan_entities
from .topics import (
    DEFAULT_IL_PREFIX,
    Topics,
    descriptor_filter,
    last_level,
    presence_filter,
    resolve_topics,
    state_topic,
)
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
    entities: list = field(default_factory=list)
    """Whatever the sink built for the device's specs."""
    unsubs: list[Unsubscribe] = field(default_factory=list)
    offline_timer: Unsubscribe | None = None
    availability: str | None = field(init=False)
    """The property with the `available` role, if the device has one."""
    online: bool = field(init=False)
    """False from the start when the device reports its availability, until it says it is up."""

    def __post_init__(self) -> None:
        self.availability = availability_prop(self.desc)
        self.online = self.availability is None

    def cancel_offline_timer(self) -> None:
        if self.offline_timer:
            self.offline_timer()
            self.offline_timer = None

    def release(self) -> None:
        """Drop the device's subscriptions and its pending offline timer."""
        for unsub in self.unsubs:
            unsub()
        self.unsubs.clear()
        self.cancel_offline_timer()


Aliases = Mapping[str, Mapping[str, str]]
"""Model (or `*` for any) -> entity key -> the key an earlier integration used."""


def parse_aliases(text: str) -> dict[str, dict[str, str]] | None:
    """Aliases from their JSON text (empty for none), or None when it is not `{"<model>": {"<key>": "<key>"}}`."""
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return None
    valid = isinstance(data, dict) and all(
        isinstance(keys, dict) and all(isinstance(a, str) and isinstance(b, str) for a, b in keys.items())
        for keys in data.values()
    )
    return data if valid else None


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
        il_prefix: str = DEFAULT_IL_PREFIX,
        offline_grace: float = 0,
        aliases: Aliases | None = None,
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
        self._offered: dict[str, tuple[Descriptor, dict]] = {}
        """The descriptors of devices not added (yet), so adding one needs no new replay."""
        self._unsubs: list[Unsubscribe] = []
        self._lock = asyncio.Lock()

    # ---- lifecycle ---------------------------------------------------------------------

    async def start(self) -> None:
        self._unsubs = [
            await self.transport.subscribe(descriptor_filter(self.il_prefix), self._on_descriptor),
            await self.transport.subscribe(presence_filter(self.il_prefix), self._on_presence),
        ]

    async def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        for dev in self.devices.values():
            dev.release()
        self.devices.clear()

    # ---- presence ----------------------------------------------------------------------

    def source_up(self, dev: Device) -> bool:
        """False while the producer of the device says it is offline (il-messages.md W-6)."""
        return dev.desc.source not in self.offline_sources

    def _on_presence(self, msg: Message) -> None:
        source = last_level(msg.topic)
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
        device_id = last_level(msg.topic)
        async with self._lock:
            payload = text(msg.payload).strip()
            if not payload:
                self._offered.pop(device_id, None)
                await self._remove_device(device_id)
                return
            try:
                doc = json.loads(payload)
                desc = parse_descriptor(doc)
            except ValueError as err:  # not JSON, or a DescriptorError
                _LOGGER.warning("ignoring the descriptor on %s: %s", msg.topic, err)
                return
            if desc.id != device_id:
                _LOGGER.warning("the descriptor on %s carries the id %s", msg.topic, desc.id)
            if not self.auto_add and desc.id not in self.allowed:
                self._offered[desc.id] = (desc, doc)
                if desc.id not in self._asked:
                    self._asked.add(desc.id)
                    self.sink.discovered(desc)
                return
            known = self.devices.get(desc.id)
            if known is not None and known.doc == doc:
                return
            await self._setup_device(desc, doc, known)

    async def set_allowed(self, allowed: set[str]) -> None:
        """The user added or deleted devices: set up the added ones and take down the deleted ones (offered again),
        leaving every other device as it is. Reloading the whole consumer instead takes every entity away and back,
        which a state-following automation reads as a change of each (an event entity coming back looks pressed)."""
        async with self._lock:
            added, dropped = allowed - self.allowed, self.allowed - allowed
            self.allowed = set(allowed)
            if self.auto_add:
                return
            for device_id in dropped:
                dev = self.devices.pop(device_id, None)
                if dev is None:
                    continue
                await self.sink.removed(dev)
                dev.entities = []
                dev.release()
                self._offered[device_id] = (dev.desc, dev.doc)
                self._asked.add(device_id)
                self.sink.discovered(dev.desc)
            for device_id in added:
                offered = self._offered.pop(device_id, None)
                if offered is not None and device_id not in self.devices:
                    await self._setup_device(*offered, None)

    def _aliases_for(self, desc: Descriptor) -> dict[str, str]:
        """The aliases for any model (`*`), overridden by the device's model's own."""
        return {**self.aliases.get("*", {}), **(self.aliases.get(desc.model, {}) if desc.model else {})}

    async def _setup_device(self, desc: Descriptor, doc: dict, known: Device | None) -> None:
        if known is not None:
            await self.sink.replacing(known)
            known.entities = []
            known.release()
        try:
            specs = plan_entities(desc, self._aliases_for(desc))
        except ValueError as err:
            _LOGGER.error("cannot plan %s: %s", desc.id, err)
            return
        dev = Device(desc=desc, doc=doc, topics=resolve_topics(desc, self.il_prefix), specs=specs)
        self.devices[desc.id] = dev
        for prop in desc.props.values():
            if prop.type != "trigger":  # a trigger has no state
                topic = state_topic(desc, dev.topics, prop.name)
                dev.unsubs.append(await self.transport.subscribe(topic, self._state_callback(dev, prop)))
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
        dev.release()

    # ---- values ------------------------------------------------------------------------

    def _state_callback(self, dev: Device, prop: Prop) -> Callable[[Message], None]:
        name = prop.name

        def on_state(msg: Message) -> None:
            if prop.type == "event":
                # every message is one occurrence; a retained one is an old occurrence the broker
                # replays on (re)subscribe, not a new one
                kind = text(msg.payload).strip()
                if kind and not msg.retain:
                    self.sink.event(dev, name, kind)
                return
            value = decode_value(prop, msg.payload)
            if value is None:
                dev.values.pop(name, None)
            else:
                dev.values[name] = value
            if name == dev.availability:
                self._availability(dev, value is True)
            self.sink.values_changed(dev)

        return on_state

    def _availability(self, dev: Device, online: bool) -> None:
        dev.cancel_offline_timer()
        if online or self.offline_grace <= 0:
            dev.online = online
            return

        def go_offline() -> None:
            dev.offline_timer = None
            dev.online = False
            self.sink.values_changed(dev)

        dev.offline_timer = self.call_later(self.offline_grace, go_offline)

    def _reject_callback(self, dev: Device) -> Callable[[Message], None]:
        def on_reject(msg: Message) -> None:
            try:
                body = json.loads(msg.payload)
            except ValueError:
                body = {"reason": text(msg.payload)}
            if not isinstance(body, dict):
                body = {"reason": str(body)}
            data = {"device_id": dev.desc.id} | {k: body.get(k) for k in ("prop", "code", "reason")}
            _LOGGER.warning("%s refused a command: %s", dev.desc.id, data)
            self.sink.rejected(dev, data)

        return on_reject
