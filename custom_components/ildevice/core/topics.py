"""Where a device's values live (il-mqtt.md, section 2)."""

from __future__ import annotations

from dataclasses import dataclass

from .descriptor import Descriptor


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


def resolve_topics(desc: Descriptor, il_prefix: str = "il") -> Topics:
    """The device's topics: its `x-mqtt` block, else the defaults under `<il_prefix>/<id>`."""
    base = f"{il_prefix}/{desc.id}"
    x = desc.x_mqtt

    def fill(template: str) -> str:
        return template.replace("{id}", desc.id)

    return Topics(
        state=fill(x.get("state", base + "/{prop}")),
        set=fill(x.get("set", base + "/{prop}/set")),
        reject=fill(x.get("reject", base + "/reject")),
    )


def state_topic(desc: Descriptor, topics: Topics, prop: str) -> str:
    """The state topic of one property: its own `x-mqtt.state` overrides the device's."""
    own = desc.props[prop].x_mqtt.get("state")
    if own:
        return own.replace("{id}", desc.id).replace("{prop}", prop)
    return topics.state_topic(prop)


def set_topic(desc: Descriptor, topics: Topics, prop: str) -> str:
    own = desc.props[prop].x_mqtt.get("set")
    if own:
        return own.replace("{id}", desc.id).replace("{prop}", prop)
    return topics.set_topic(prop)
