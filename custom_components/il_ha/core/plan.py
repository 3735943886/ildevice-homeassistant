"""Which entities a descriptor becomes.

A device's *kind* and the *roles* of its properties decide the composite entities (a `climate`,
a `humidifier`, a `fan`, a `light`, a `cover`, a `lock`, a `siren`, a `valve`, an `alarm`,
a `vacuum`); every other property becomes a plain entity chosen by its type, and
its `class` / `series` / `category` say how it is classified. Nothing here looks at a model or a
producer, so a device from any producer is planned the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from .descriptor import Descriptor, Prop, Requires
from .icons import icon_for


@dataclass(frozen=True)
class EntitySpec:
    platform: str
    """`climate`, `humidifier`, `fan`, `light`, `cover`, `lock`, `sensor`, `binary_sensor`,
    `switch`, `number`, `select`, `text`, `button` or `event`."""
    key: str
    """Stable name of the entity within its device (the property name, or the composite's
    platform)."""
    unique_id: str
    name: str | None
    """`None` for a composite: it takes the device's name."""
    slots: Mapping[str, str]
    """Slot -> property name. `value` for a plain entity; roles for a composite."""
    shared: Mapping[str, str] = field(default_factory=dict)
    """Properties a composite reads but does not own (they also have an entity of their own)."""
    device_class: str | None = None
    state_class: str | None = None
    entity_category: str | None = None
    unit: str | None = None
    options: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    step: float | None = None
    requires: Requires | None = None
    """A condition on another property for the whole entity to accept a command."""
    slot_requires: Mapping[str, Requires] = field(default_factory=dict)
    """Slot -> the condition on that one property, for a composite whose controls differ."""
    icon: str | None = None
    """`mdi:...` for a plain entity that has no device class of its own to give it one (`icons.py`)."""


# kind -> (composite platform, slot roles it owns, roles it only reads, roles it needs)
_COMPOSITES = {
    "climate": (
        "climate",
        ("on", "mode", "fan_speed", "target_temperature", "current_temperature",
         "swing_vertical", "swing_horizontal", "action", "current_humidity"),
        (),
        ("target_temperature",),
    ),
    "humidifier": (
        "humidifier",
        ("on", "mode", "target_humidity"),
        ("current_humidity",),
        ("on", "target_humidity"),
    ),
    "fan": (
        "fan",
        ("on", "mode", "fan_speed", "speed", "oscillate", "direction"),
        (),
        ("on",),
    ),
    "light": (
        "light",
        ("on", "brightness", "color_temperature", "color", "color_mode"),
        (),
        ("on",),
    ),
    "cover": (
        "cover",
        ("position", "tilt", "motion", "open", "close", "stop"),
        (),
        (),
    ),
    "lock": (
        "lock",
        ("locked", "unlatch"),
        (),
        ("locked",),
    ),
    "siren": ("siren", ("on",), (), ("on",)),
    "valve": ("valve", ("opened",), (), ("opened",)),
    "alarm": (
        "alarm_control_panel",
        ("alarm_state", "arm_home", "arm_away", "arm_night", "disarm"),
        (),
        ("alarm_state",),
    ),
    "vacuum": (
        "vacuum",
        ("vacuum_state", "start", "pause", "return_home", "locate", "fan_speed"),
        (),
        ("vacuum_state",),
    ),
}

# Registry of il.md section 9: a role's type. A property that carries the role with another type
# has no role (O-5).
_ROLE_TYPES = {
    "available": "binary", "on": "binary", "mode": "select", "fan_speed": "select",
    "speed": "number", "oscillate": "binary", "direction": "select",
    "target_humidity": "number", "current_humidity": "number", "current_temperature": "number",
    "target_temperature": "number", "swing_vertical": "binary", "swing_horizontal": "binary",
    "action": "select", "brightness": "number", "color_temperature": "number", "color": "text",
    "color_mode": "select", "position": "number", "tilt": "number", "motion": "select",
    "open": "trigger", "close": "trigger", "stop": "trigger", "locked": "binary",
    "unlatch": "trigger", "opened": "binary", "alarm_state": "select", "arm_home": "trigger",
    "arm_away": "trigger", "arm_night": "trigger", "disarm": "trigger", "vacuum_state": "select",
    "start": "trigger", "pause": "trigger", "return_home": "trigger", "locate": "trigger",
    "battery": "number",
}

# a descriptor `class` -> the device class Home Assistant knows it as
_COVER_CLASSES = {"garage_door": "garage"}


def humanize(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _name(prop: Prop) -> str:
    if prop.label:
        return prop.label
    if prop.group:
        return f"{humanize(prop.group)} {prop.name.replace(prop.group + '_', '', 1).replace('_', ' ')}"
    return humanize(prop.name)


def _category(prop: Prop) -> str | None:
    # a diagnostic is read only, or a trigger (a factory reset); a config entity is a setting
    if prop.category == "diagnostic" and (not prop.writable or prop.type == "trigger"):
        return "diagnostic"
    if prop.category == "config" and prop.writable:
        return "config"
    return None


def _generic(desc: Descriptor, prop: Prop, unique_id: str) -> EntitySpec:
    spec = _plain(desc, prop, unique_id)
    icon = icon_for(spec.platform, prop.name, spec.unit, spec.device_class)
    return replace(spec, icon=icon) if icon else spec


def _plain(desc: Descriptor, prop: Prop, unique_id: str) -> EntitySpec:
    common = dict(
        key=prop.name,
        unique_id=unique_id,
        name=_name(prop),
        slots={"value": prop.name},
        entity_category=_category(prop),
        requires=prop.requires,
    )
    if prop.type == "binary":
        platform = "switch" if prop.rw else "binary_sensor"
        return EntitySpec(platform=platform, device_class=prop.klass, **common)
    if prop.type == "number":
        if prop.rw:
            return EntitySpec(
                platform="number", device_class=prop.klass, unit=prop.unit,
                min=prop.min, max=prop.max, step=prop.step, **common,
            )
        return EntitySpec(
            platform="sensor", device_class=prop.klass or ("battery" if prop.role == "battery" else None),
            unit=prop.unit, state_class={"counter": "total_increasing", "gauge": "measurement"}.get(prop.series or ""),
            **common,
        )
    if prop.type == "select":
        if prop.rw:
            return EntitySpec(platform="select", options=prop.options, **common)
        # a read-only select is an enumeration sensor
        return EntitySpec(platform="sensor", device_class="enum", options=prop.options, **common)
    if prop.type == "text":
        if not prop.rw and prop.klass == "datetime":
            return EntitySpec(platform="sensor", device_class="timestamp", **common)
        return EntitySpec(platform="text" if prop.rw else "sensor", **common)
    if prop.type == "event":
        return EntitySpec(platform="event", device_class=prop.klass, options=prop.options, **common)
    return EntitySpec(platform="button", device_class=prop.klass, **common)  # trigger


def _composite(
    desc: Descriptor, uid, kind: str | None, klass: str | None, props: Mapping[str, Prop],
    key: str | None = None, name: str | None = None,
) -> tuple[EntitySpec | None, set[str]]:
    """The composite entity of one unit (the device itself, or a group with its own `kind`), and
    the properties it owns. `props` are the unit's properties; roles are looked up among them only."""
    rule = _COMPOSITES.get(kind or "")
    if rule is None:
        return None, set()
    platform, owned_roles, shared_roles, needed = rule
    by_role: dict[str, Prop] = {}
    for prop in props.values():
        if prop.role and prop.role not in by_role and _ROLE_TYPES.get(prop.role, prop.type) == prop.type:
            by_role[prop.role] = prop
    if platform == "climate":
        if not all(r in by_role for r in needed):
            return None, set()
    elif platform == "cover":
        if not {"position", "open", "close"} & by_role.keys():
            return None, set()
    elif platform in ("lock", "valve"):
        # a lock or valve that cannot be written is a plain binary sensor: a role never implies control
        role = "locked" if platform == "lock" else "opened"
        if role not in by_role or not by_role[role].writable:
            return None, set()
    elif not all(r in by_role for r in needed):
        return None, set()
    slots = {r: by_role[r].name for r in owned_roles if r in by_role}
    shared = {r: by_role[r].name for r in shared_roles if r in by_role}
    extra: dict = {}
    if platform == "climate":
        target = by_role.get("target_temperature")
        if target is not None:
            extra = dict(min=target.min, max=target.max, step=target.step, unit=target.unit)
    elif platform == "humidifier":
        target = by_role["target_humidity"]
        extra = dict(
            min=target.min, max=target.max, step=target.step,
            device_class="dehumidifier" if klass == "dehumidifier" else "humidifier",
        )
    elif platform == "cover":
        extra = dict(device_class=_COVER_CLASSES.get(klass or "", klass))
    elif platform == "lock":
        extra = dict(requires=by_role["locked"].requires)
    elif platform == "valve":
        extra = dict(device_class=klass)
    key = key or platform
    slot_requires = {r: by_role[r].requires for r in slots if by_role[r].requires is not None}
    # a composite made of settings (a light's backlight) is itself a config entity: its own properties say so
    category = next((c for r in slots.values() if (c := _category(props[r]))), None)
    spec = EntitySpec(
        platform=platform, key=key, unique_id=uid(key), name=name, entity_category=category,
        slots=slots, shared=shared, slot_requires=slot_requires, **extra,
    )
    return spec, set(slots.values())


def plan_entities(
    desc: Descriptor, key_aliases: Mapping[str, str] | None = None
) -> list[EntitySpec]:
    """The entities of one device. `key_aliases` renames an entity's key (and so its
    unique id), which is how a consumer keeps the ids an earlier integration created."""
    aliases = key_aliases or {}

    def uid(key: str) -> str:
        return f"{desc.id}-{aliases.get(key, key)}"

    specs: list[EntitySpec] = []
    owned: set[str] = set()
    # A group with its own `kind` is a composite of its own (a light and a cover on one device);
    # the device's `kind` covers every property that is not in such a group.
    kinded = {name for name in desc.groups}
    rest = {n: p for n, p in desc.props.items() if p.group not in kinded}
    composite, taken = _composite(desc, uid, desc.kind, desc.klass, rest)
    if composite is not None:
        specs.append(composite)
        owned |= taken
    for group in desc.groups.values():
        members = {n: p for n, p in desc.props.items() if p.group == group.name}
        spec, taken = _composite(
            desc, uid, group.kind, group.klass or desc.klass, members,
            key=group.name, name=group.label or humanize(group.name),
        )
        if spec is not None:
            specs.append(spec)
            owned |= taken
    for prop in desc.props.values():
        if prop.role == "available" or prop.name in owned:
            continue
        specs.append(_generic(desc, prop, uid(prop.name)))

    seen: dict[str, str] = {}
    for spec in specs:
        if spec.unique_id in seen:
            raise ValueError(f"{desc.id}: {spec.key} and {seen[spec.unique_id]} share {spec.unique_id}")
        seen[spec.unique_id] = spec.key
    return specs


def availability_prop(desc: Descriptor) -> str | None:
    """The property with the `available` role, if the device has one."""
    for prop in desc.props.values():
        if prop.role == "available" and prop.type == "binary":
            return prop.name
    return None
