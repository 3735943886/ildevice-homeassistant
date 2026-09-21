"""The consumer model on an in-process transport: no Home Assistant anywhere."""

import json
import subprocess
import sys

import pytest

from custom_components.il_ha.core import IlModel
from custom_components.il_ha.core.memory import InProcessTransport, matches

DESC = {"il": 0, "id": "d1", "source": "prod", "kind": "light", "label": "Lamp", "props": {
    "available": {"type": "binary", "role": "available"},
    "power": {"type": "binary", "rw": True, "role": "on"},
    "click": {"type": "event", "options": ["single"]}}}


class Sink:
    def __init__(self):
        self.calls = []

    def discovered(self, desc): self.calls.append(("discovered", desc.id))
    def withdrawn(self, device_id): self.calls.append(("withdrawn", device_id))
    async def replacing(self, known): self.calls.append(("replacing", known.desc.id))
    async def ready(self, dev, known): self.calls.append(("ready", dev.desc.id, known is not None))
    async def removed(self, dev): self.calls.append(("removed", dev.desc.id))
    def values_changed(self, dev): self.calls.append(("values", dict(dev.values), dev.online))
    def event(self, dev, prop, kind): self.calls.append(("event", prop, kind))
    def rejected(self, dev, data): self.calls.append(("rejected", data))


class Timers:
    def __init__(self): self.pending = []

    def __call__(self, delay, fn):
        entry = [delay, fn]
        self.pending.append(entry)
        return lambda: self.pending.remove(entry) if entry in self.pending else None


async def make(**kw):
    bus, sink, timers = InProcessTransport(), Sink(), Timers()
    model = IlModel(bus, sink, timers, **kw)
    await model.start()
    return bus, sink, timers, model


async def publish_descriptor(bus, doc=DESC):
    await bus.publish("il/d1", json.dumps(doc), 1, True)
    await bus.settle()


def test_topic_filters_are_mqtt_filters():
    assert matches("il/+", "il/d1") and not matches("il/+", "il/d1/x") and matches("il/#", "il/d1/x")
    assert not matches("il/+/x", "il/d1") and matches("a/b", "a/b") and not matches("a/b", "a/c")


async def test_a_descriptor_sets_up_a_device_and_values_follow():
    bus, sink, _, model = await make()
    await publish_descriptor(bus)
    assert sink.calls == [("ready", "d1", False)]
    dev = model.devices["d1"]
    assert [s.platform for s in dev.specs] == ["light", "event"] and dev.online is False
    await bus.publish("il/d1/available", "true", 1, True)
    await bus.publish("il/d1/power", "true", 1, True)
    assert dev.values == {"available": True, "power": True} and dev.online is True
    await bus.publish("il/d1/power", "", 1, True)                      # empty retained = absent (M-10)
    assert "power" not in dev.values


async def test_a_retained_descriptor_is_seen_by_a_late_consumer_and_a_retained_event_is_not_an_occurrence():
    bus = InProcessTransport()
    await bus.publish("il/d1", json.dumps(DESC), 1, True)
    await bus.publish("il/d1/click", "single", 1, True)                 # an old occurrence
    sink = Sink()
    model = IlModel(bus, sink, Timers())
    await model.start()
    await bus.settle()
    assert ("ready", "d1", False) in sink.calls and not [c for c in sink.calls if c[0] == "event"]
    await bus.publish("il/d1/click", "single", 1, False)                # a live one
    assert ("event", "click", "single") in sink.calls


async def test_a_new_descriptor_replaces_and_an_identical_one_is_ignored():
    bus, sink, _, _ = await make()
    await publish_descriptor(bus)
    await publish_descriptor(bus)
    assert sink.calls == [("ready", "d1", False)]
    await publish_descriptor(bus, {**DESC, "label": "Lamp 2"})
    assert sink.calls[1:] == [("replacing", "d1"), ("ready", "d1", True)]


async def test_an_empty_descriptor_removes_the_device():
    bus, sink, _, model = await make()
    await publish_descriptor(bus)
    await bus.publish("il/d1", "", 1, True)
    await bus.settle()
    assert "d1" not in model.devices and sink.calls[-2:] == [("withdrawn", "d1"), ("removed", "d1")]
    await bus.publish("il/d1/power", "true")                            # its subscriptions are gone
    assert sink.calls[-1] == ("removed", "d1")


async def test_a_device_that_was_not_added_is_offered_once():
    bus, sink, _, model = await make(auto_add=False, allowed=set())
    await publish_descriptor(bus)
    await publish_descriptor(bus, {**DESC, "label": "x"})
    assert sink.calls == [("discovered", "d1")] and not model.devices
    model.allowed.add("d1")
    await publish_descriptor(bus, {**DESC, "label": "y"})
    assert sink.calls[-1] == ("ready", "d1", False)


async def test_the_grace_period_delays_offline_and_a_return_cancels_it():
    bus, sink, timers, model = await make(offline_grace=30)
    await publish_descriptor(bus)
    await bus.publish("il/d1/available", "true", 1, True)
    await bus.publish("il/d1/available", "false", 1, True)
    dev = model.devices["d1"]
    assert dev.online is True and [t[0] for t in timers.pending] == [30]
    await bus.publish("il/d1/available", "true", 1, True)
    assert timers.pending == [] and dev.online is True
    await bus.publish("il/d1/available", "false", 1, True)
    timers.pending[0][1]()
    assert dev.online is False and sink.calls[-1][0] == "values"


async def test_producer_presence_and_rejections_reach_the_sink():
    bus, sink, _, model = await make()
    await publish_descriptor(bus)
    dev = model.devices["d1"]
    await bus.publish("il/_producer/prod", "offline", 1, True)
    assert model.source_up(dev) is False and sink.calls[-1][0] == "values"
    await bus.publish("il/_producer/prod", "online", 1, True)
    assert model.source_up(dev) is True
    await bus.publish("il/d1/reject", json.dumps({"prop": "power", "code": "read_only", "reason": "no"}))
    assert sink.calls[-1] == ("rejected", {"device_id": "d1", "prop": "power", "code": "read_only", "reason": "no"})


async def test_stop_releases_every_subscription():
    bus, sink, _, model = await make()
    await publish_descriptor(bus)
    await model.stop()
    assert bus._subs == [] and model.devices == {}


def test_the_core_imports_without_home_assistant():
    code = ("import sys; sys.modules['homeassistant'] = None; "
            "import custom_components.il_ha.core as c; assert c.IlModel and c.plan_entities")
    subprocess.run([sys.executable, "-c", code], check=True)
