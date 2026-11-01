"""The IL transport over Home Assistant's `mqtt` integration."""

from __future__ import annotations

import inspect

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from .core.transport import Callback, Message, Unsubscribe


class HaMqttTransport:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def subscribe(self, topic: str, cb: Callback, qos: int = 1) -> Unsubscribe:
        hass = self.hass

        @callback
        def handler(msg) -> None:
            result = cb(Message(msg.topic, msg.payload, bool(msg.retain)))
            if inspect.isawaitable(result):
                hass.async_create_task(result)

        return await mqtt.async_subscribe(hass, topic, handler, qos=qos)

    async def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        await mqtt.async_publish(self.hass, topic, payload, qos=qos, retain=retain)
