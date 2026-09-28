"""Which entities a descriptor becomes.

A device's *kind* and the *roles* of its properties decide the composite entities (a `climate`,
a `humidifier`, a `fan`, a `light`, a `cover`, a `lock`, a `siren`, a `valve`, an `alarm`,
a `vacuum`); every other property becomes a plain entity chosen by its type, and
its `class` / `series` / `category` say how it is classified. Nothing here looks at a model or a
producer, so a device from any producer is planned the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

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


VALUE = "value"
"""The one slot of a plain entity."""


@dataclass(frozen=True)
class _Composite:
    platform: str
    owns: tuple[str, ...]
    """The roles it takes over: they get no entity of their own."""
    reads: tuple[str, ...] = ()
    """Roles it only reads: they keep their own entity."""
    needs: tuple[str, ...] = ()
    needs_any: tuple[str, ...] = ()
    """At least one of these must be present."""
    writable: str | None = None
    """A role that must be writable: a role never implies control, so a read-only lock is a binary sensor."""


_CLIMATE_ROLES = (
    "on", "mode", "fan_speed", "target_temperature", "current_temperature",
    "swing_vertical", "swing_horizontal", "action", "current_humidity",
)

# kind -> its composite
_COMPOSITES = {
    "climate": _Composite("climate", _CLIMATE_ROLES, needs=("target_temperature",)),
    "humidifier": _Composite(
        "humidifier", ("on", "mode", "target_humidity"), reads=("current_humidity",), needs=("on", "target_humidity"),
    ),
    "fan": _Composite("fan", ("on", "mode", "fan_speed", "speed", "oscillate", "direction"), needs=("on",)),
    "light": _Composite("light", ("on", "brightness", "color_temperature", "color", "color_mode"), needs=("on",)),
    "cover": _Composite(
        "cover", ("position", "tilt", "cover_state", "open", "close", "stop"), needs_any=("position", "open", "close"),
    ),
    "lock": _Composite("lock", ("locked", "lock_state", "unlatch"), writable="locked"),
    "siren": _Composite("siren", ("on",), needs=("on",)),
    "valve": _Composite("valve", ("opened",), writable="opened"),
    "alarm": _Composite(
        "alarm_control_panel", ("alarm_state", "arm_home", "arm_away", "arm_night", "disarm"), needs=("alarm_state",),
    ),
    "vacuum": _Composite(
        "vacuum", ("vacuum_state", "start", "pause", "return_home", "locate", "fan_speed"), needs=("vacuum_state",),
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
    "color_mode": "select", "position": "number", "tilt": "number", "cover_state": "select", "lock_state": "select",
    "open": "trigger", "close": "trigger", "stop": "trigger", "locked": "binary",
    "unlatch": "trigger", "opened": "binary", "alarm_state": "select", "arm_home": "trigger",
    "arm_away": "trigger", "arm_night": "trigger", "disarm": "trigger", "vacuum_state": "select",
    "start": "trigger", "pause": "trigger", "return_home": "trigger", "locate": "trigger",
    "battery": "number",
}

# a descriptor `class` -> the device class Home Assistant knows it as
_COVER_CLASSES = {"garage_door": "garage"}

# a number's `series` -> its state class
_STATE_CLASSES = {"counter": "total_increasing", "gauge": "measurement"}


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


def _generic(prop: Prop, unique_id: str) -> EntitySpec:
    spec = _plain(prop, unique_id)
    icon = icon_for(spec.platform, prop.name, spec.unit, spec.device_class)
    return replace(spec, icon=icon) if icon else spec


def _plain(prop: Prop, unique_id: str) -> EntitySpec:
    common = dict(
        key=prop.name,
        unique_id=unique_id,
        name=_name(prop),
        slots={VALUE: prop.name},
        entity_category=_category(prop),
        requires=prop.requires,
    )
    match prop.type, prop.rw:
        case "binary", rw:
            return EntitySpec(platform="switch" if rw else "binary_sensor", device_class=prop.klass, **common)
        case "number", True:
            return EntitySpec(
                platform="number", device_class=prop.klass, unit=prop.unit, **_range(prop), **common,
            )
        case "number", False:
            return EntitySpec(
                platform="sensor", device_class=prop.klass or ("battery" if prop.role == "battery" else None),
                unit=prop.unit, state_class=_STATE_CLASSES.get(prop.series or ""), **common,
            )
        case "select", True:
            return EntitySpec(platform="select", options=prop.options, **common)
        case "select", False:
            # a read-only select is an enumeration sensor
            return EntitySpec(platform="sensor", device_class="enum", options=prop.options, **common)
        case "text", False if prop.klass == "datetime":
            return EntitySpec(platform="sensor", device_class="timestamp", **common)
        case "text", rw:
            return EntitySpec(platform="text" if rw else "sensor", **common)
        case "event", _:
            return EntitySpec(platform="event", device_class=prop.klass, options=prop.options, **common)
        case _:  # trigger
            return EntitySpec(platform="button", device_class=prop.klass, **common)


def _range(prop: Prop) -> dict:
    return dict(min=prop.min, max=prop.max, step=prop.step)


def _by_role(props: Iterable[Prop]) -> dict[str, Prop]:
    """The first property of each role; one that carries a role with another type has no role (O-5)."""
    by_role: dict[str, Prop] = {}
    for prop in props:
        if prop.role and _ROLE_TYPES.get(prop.role, prop.type) == prop.type:
            by_role.setdefault(prop.role, prop)
    return by_role


def _composite_extra(platform: str, klass: str | None, by_role: Mapping[str, Prop]) -> dict:
    """What a composite takes from its properties and its `class`, beyond its slots."""
    match platform:
        case "climate":
            target = by_role["target_temperature"]
            return {**_range(target), "unit": target.unit}
        case "humidifier":
            device_class = "dehumidifier" if klass == "dehumidifier" else "humidifier"
            return {**_range(by_role["target_humidity"]), "device_class": device_class}
        case "cover":
            return {"device_class": _COVER_CLASSES.get(klass or "", klass)}
        case "lock":
            return {"requires": by_role["locked"].requires}
        case "valve":
            return {"device_class": klass}
    return {}


def _composite(
    uid: Callable[[str], str], kind: str | None, klass: str | None, props: Mapping[str, Prop],
    key: str | None = None, name: str | None = None,
) -> EntitySpec | None:
    """The composite entity of one unit (the device itself, or a group with its own `kind`).
    `props` are the unit's properties; roles are looked up among them only."""
    rule = _COMPOSITES.get(kind or "")
    if rule is None:
        return None
    by_role = _by_role(props.values())
    if not all(r in by_role for r in rule.needs):
        return None
    if rule.needs_any and not by_role.keys() & set(rule.needs_any):
        return None
    if rule.writable and not (rule.writable in by_role and by_role[rule.writable].writable):
        return None
    slots = {r: by_role[r].name for r in rule.owns if r in by_role}
    key = key or rule.platform
    return EntitySpec(
        platform=rule.platform,
        key=key,
        unique_id=uid(key),
        name=name,
        # a composite made of settings (a light's backlight) is itself a config entity: its own properties say so
        entity_category=next((c for p in slots.values() if (c := _category(props[p]))), None),
        slots=slots,
        shared={r: by_role[r].name for r in rule.reads if r in by_role},
        slot_requires={r: by_role[r].requires for r in slots if by_role[r].requires is not None},
        **_composite_extra(rule.platform, klass, by_role),
    )


def plan_entities(
    desc: Descriptor, key_aliases: Mapping[str, str] | None = None
) -> list[EntitySpec]:
    """The entities of one device. `key_aliases` renames an entity's key (and so its
    unique id), which is how a consumer keeps the ids an earlier integration created."""
    aliases = key_aliases or {}

    def uid(key: str) -> str:
        return f"{desc.id}-{aliases.get(key, key)}"

    # A group with its own `kind` is a composite of its own (a light and a cover on one device);
    # the device's `kind` covers every property that is not in such a group.
    composites = [
        _composite(uid, desc.kind, desc.klass, {n: p for n, p in desc.props.items() if p.group not in desc.groups}),
        *(
            _composite(
                uid, group.kind, group.klass or desc.klass,
                {n: p for n, p in desc.props.items() if p.group == group.name},
                key=group.name, name=group.label or humanize(group.name),
            )
            for group in desc.groups.values()
        ),
    ]
    specs = [spec for spec in composites if spec is not None]
    owned = {prop for spec in specs for prop in spec.slots.values()}
    specs += [
        _generic(prop, uid(prop.name))
        for prop in desc.props.values()
        if prop.role != "available" and prop.name not in owned
    ]

    seen: dict[str, str] = {}
    for spec in specs:
        if spec.unique_id in seen:
            raise ValueError(f"{desc.id}: {spec.key} and {seen[spec.unique_id]} share {spec.unique_id}")
        seen[spec.unique_id] = spec.key
    return specs


def availability_prop(desc: Descriptor) -> str | None:
    """The property with the `available` role, if the device has one."""
    return next((p.name for p in desc.props.values() if p.role == "available" and p.type == "binary"), None)
