"""IL descriptor -> typed objects (specification: il.md, section 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The five value types of the IL. A property of any other type is skipped, not an error.
TYPES = ("binary", "number", "select", "text", "trigger")


class DescriptorError(ValueError):
    """The document is not a usable descriptor."""


@dataclass(frozen=True)
class Prop:
    name: str
    type: str
    rw: bool = False
    role: str | None = None
    requires: str | None = None
    group: str | None = None
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[str, ...] = ()
    label: str | None = None
    klass: str | None = None  # the property's `class`: what kind of value it is
    series: str | None = None  # "gauge" (default) or "counter"
    category: str | None = None  # "diagnostic" | "config" | None
    x_mqtt: dict[str, str] = field(default_factory=dict)

    @property
    def writable(self) -> bool:
        return self.rw or self.type == "trigger"


@dataclass(frozen=True)
class Descriptor:
    il: int
    id: str
    source: str | None
    kind: str | None
    klass: str | None  # the descriptor's `class`: a refinement of `kind`
    label: str | None
    vendor: str | None
    model: str | None
    identifiers: dict[str, str]
    props: dict[str, Prop]
    x_mqtt: dict[str, str]


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _opt_num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _parse_prop(name: str, doc: Any) -> Prop | None:
    if not isinstance(doc, dict) or doc.get("type") not in TYPES:
        return None
    options = doc.get("options")
    return Prop(
        name=name,
        type=doc["type"],
        rw=doc.get("rw") is True,
        role=_opt_str(doc.get("role")),
        requires=_opt_str(doc.get("requires")),
        group=_opt_str(doc.get("group")),
        unit=_opt_str(doc.get("unit")),
        min=_opt_num(doc.get("min")),
        max=_opt_num(doc.get("max")),
        step=_opt_num(doc.get("step")),
        options=tuple(o for o in options if isinstance(o, str)) if isinstance(options, list) else (),
        label=_opt_str(doc.get("label")),
        klass=_opt_str(doc.get("class")),
        series=doc.get("series") if doc.get("series") in ("gauge", "counter") else None,
        category=doc.get("category") if doc.get("category") in ("diagnostic", "config") else None,
        x_mqtt=_str_map(doc.get("x-mqtt")),
    )


def parse_descriptor(doc: Any) -> Descriptor:
    """Parse a descriptor document (already JSON-decoded). Unknown fields are ignored and a
    property of an unknown type is skipped, as the compatibility rules say."""
    if not isinstance(doc, dict):
        raise DescriptorError("a descriptor is a JSON object")
    device_id = doc.get("id")
    if not isinstance(device_id, str) or not device_id:
        raise DescriptorError("a descriptor needs an id")
    raw_props = doc.get("props")
    if not isinstance(raw_props, dict):
        raise DescriptorError("a descriptor needs props")
    props = {}
    for name, body in raw_props.items():
        prop = _parse_prop(name, body)
        if prop is not None:
            props[name] = prop
    il = doc.get("il")
    return Descriptor(
        il=il if isinstance(il, int) and not isinstance(il, bool) else 0,
        id=device_id,
        source=_opt_str(doc.get("source")),
        kind=_opt_str(doc.get("kind")),
        klass=_opt_str(doc.get("class")),
        label=_opt_str(doc.get("label")),
        vendor=_opt_str(doc.get("vendor")),
        model=_opt_str(doc.get("model")),
        identifiers=_str_map(doc.get("identifiers")),
        props=props,
        x_mqtt=_str_map(doc.get("x-mqtt")),
    )
