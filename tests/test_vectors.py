"""The language-neutral conformance vectors of the ildevice repository (../ildevice/vectors, or $ILDEVICE)."""

import json
import os
from pathlib import Path

import pytest

from custom_components.il_ha.core import decode_value, encode_command, parse_descriptor, plan_entities
from custom_components.il_ha.core.descriptor import Prop
from custom_components.il_ha.core.topics import resolve_topics, set_topic, state_topic

ROOT = Path(os.environ.get("ILDEVICE", Path(__file__).resolve().parents[2] / "ildevice"))
VECTORS = ROOT / "vectors"
pytestmark = pytest.mark.skipif(not VECTORS.is_dir(), reason=f"no ildevice checkout at {ROOT} (set ILDEVICE)")


def load(name):
    return json.loads((VECTORS / name).read_text())


def prop(type_):
    return Prop(name="p", type=type_)


@pytest.mark.parametrize("case", load("wire-values.json")["state_decode"] if VECTORS.is_dir() else [],
                         ids=lambda c: f"{c['type']}:{c['payload']!r}")
def test_state_payloads_decode_as_the_vectors_say(case):
    assert decode_value(prop(case["type"]), case["payload"]) == case["value"]


@pytest.mark.parametrize("case", load("wire-values.json")["write_encode"] if VECTORS.is_dir() else [],
                         ids=lambda c: f"{c['type']}:{c['value']!r}")
def test_writes_are_encoded_as_the_vectors_say(case):
    assert encode_command(prop(case["type"]), case["value"]) == case["payload"]


@pytest.mark.parametrize("case", load("topics.json")["layouts"] if VECTORS.is_dir() else [], ids=lambda c: c["name"])
def test_topics_follow_the_vectors(case):
    desc = parse_descriptor(case["descriptor"])
    topics = resolve_topics(desc, case["il_prefix"])
    assert {p: state_topic(desc, topics, p) for p in case["state"]} == case["state"]
    assert {p: set_topic(desc, topics, p) for p in case["set"]} == case["set"]
    assert topics.reject == case["reject"]


_KIND_OF = {"alarm_control_panel": "alarm"}


def formed(desc):
    """{(kind, group): set of (role, property)} of the composites plan_entities builds."""
    by_name = {p.name: p for p in desc.props.values()}
    out = {}
    for spec in plan_entities(desc):
        if spec.platform in ("sensor", "binary_sensor", "switch", "number", "select", "text", "button", "event"):
            continue
        group = spec.key if spec.key in desc.groups else None
        roles = {**spec.slots, **spec.shared}
        out[(_KIND_OF.get(spec.platform, spec.platform), group)] = {(r, n) for r, n in roles.items() if by_name[n].role == r}
    return out


@pytest.mark.parametrize("case", load("composites.json")["cases"] if VECTORS.is_dir() else [], ids=lambda c: c["name"])
def test_composites_follow_the_vectors(case):
    desc = parse_descriptor(case["descriptor"])
    got = formed(desc)
    for want in case["composites"]:
        actual = got.get((want["kind"], want["group"]))
        assert actual is not None, f"no {want['kind']} composite (group {want['group']}); formed: {sorted(got)}"
        required = set(want["roles"].items())
        allowed = required | set(want["optional"].items())
        assert required <= actual <= allowed, (required, actual, allowed)
    for none in case["no_composite"]:
        assert not [k for k in got if k[0] == none["kind"]], f"{none['kind']} formed but must not: {got}"
    assert len(got) == len(case["composites"]), f"unexpected composites: {sorted(got)}"
