"""Values on the wire: decode what a producer publishes, encode what a consumer writes."""

from __future__ import annotations

import json
from typing import Any

from .descriptor import Prop


def decode_value(prop: Prop, payload: str | bytes | None) -> Any:
    """A state payload as the property's value, or `None` when it is *absent* (an empty
    payload) or does not fit the type. Never raises."""
    if payload is None:
        return None
    text = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else payload
    if text.strip() == "":
        return None
    if prop.type == "binary":
        lowered = text.strip().lower()
        if lowered in ("true", "on", "1"):
            return True
        if lowered in ("false", "off", "0"):
            return False
        return None
    if prop.type == "number":
        try:
            number = float(json.loads(text.strip()))
        except (ValueError, TypeError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return int(number) if number.is_integer() else number
    if prop.type in ("select", "text"):
        return text
    return None  # a trigger never has a value


def number_text(value: float) -> str:
    """The canonical text of a number: an integer when it is one."""
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


def encode_command(prop: Prop, value: Any) -> str:
    """The payload to write to a property's set topic. The producer validates and refuses a
    bad write itself; this only puts a value in the wire form."""
    if prop.type == "trigger":
        return ""
    if prop.type == "binary":
        return "true" if value in (True, "true", "on", "ON", 1, "1") else "false"
    if prop.type == "number":
        return number_text(float(value))
    return str(value)
