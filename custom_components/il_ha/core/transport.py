"""What the IL consumer needs from a message transport: subscribe to a topic filter, publish a message.

The consumer never knows whether the messages come from an MQTT broker (Home Assistant's `mqtt`, paho) or from a
producer in the same process (`memory.py`); the messages are the same (il-mqtt.md). Nothing here imports Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes | str
    retain: bool = False
    """True for a retained message replayed on subscribe, False for one published live."""


Callback = Callable[[Message], "Awaitable[None] | None"]
Unsubscribe = Callable[[], None]
CallLater = Callable[[float, Callable[[], None]], Unsubscribe]
"""`call_later(seconds, fn)` runs `fn` once after the delay and returns a function that cancels it."""


class Transport(Protocol):
    async def subscribe(self, topic: str, callback: Callback, qos: int = 1) -> Unsubscribe: ...

    async def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None: ...


def text(payload: Any) -> str:
    """A payload as text (transports deliver bytes or str)."""
    return payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
