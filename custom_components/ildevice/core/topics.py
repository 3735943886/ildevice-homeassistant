"""Where a device's values live (il-mqtt.md, section 2)."""

from __future__ import annotations

from dataclasses import dataclass

from .descriptor import Descriptor

DEFAULT_IL_PREFIX = "il"


def descriptor_filter(il_prefix: str) -> str:
    """The retained descriptors, one per device: `<il_prefix>/<id>`."""
    return f"{il_prefix}/+"


def presence_filter(il_prefix: str) -> str:
    """Producer presence: `<il_prefix>/_producer/<source>`."""
    return f"{il_prefix}/_producer/+"


def last_level(topic: str) -> str:
    """The last level of a topic: the device id of a descriptor, the source of a presence message."""
    return topic.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Topics:
    """Topic templates for one device, `{id}`-resolved; `{prop}` is left to `state`/`set`."""

    state: str
    set: str
    reject: str

    def state_topic(self, prop: str) -> str:
        return self.state.replace("{prop}", prop)

    def set_topic(self, prop: str) -> str:
        return self.set.replace("{prop}", prop)


def resolve_topics(desc: Descriptor, il_prefix: str = DEFAULT_IL_PREFIX) -> Topics:
    """The device's topics: its `x-mqtt` block, else the defaults under `<il_prefix>/<id>`."""
    base = f"{il_prefix}/{desc.id}"
    defaults = {"state": f"{base}/{{prop}}", "set": f"{base}/{{prop}}/set", "reject": f"{base}/reject"}
    return Topics(**{
        name: desc.x_mqtt.get(name, default).replace("{id}", desc.id) for name, default in defaults.items()
    })


def _prop_topic(desc: Descriptor, topics: Topics, prop: str, which: str) -> str:
    """A property's own `x-mqtt.<which>` overrides the device's template."""
    if own := desc.props[prop].x_mqtt.get(which):
        return own.replace("{id}", desc.id).replace("{prop}", prop)
    return getattr(topics, which).replace("{prop}", prop)


def state_topic(desc: Descriptor, topics: Topics, prop: str) -> str:
    """The state topic of one property."""
    return _prop_topic(desc, topics, prop, "state")


def set_topic(desc: Descriptor, topics: Topics, prop: str) -> str:
    """The set topic of one property."""
    return _prop_topic(desc, topics, prop, "set")
