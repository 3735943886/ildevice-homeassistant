"""The Home Assistant free core, against the real descriptors the rusthinq drivers publish."""

import pytest

from conftest import FIXTURES, load
from custom_components.il_ha.core import (
    DescriptorError,
    decode_value,
    encode_command,
    parse_descriptor,
    plan_entities,
    resolve_topics,
)
from custom_components.il_ha.core.plan import availability_prop
from custom_components.il_ha.core.topics import set_topic, state_topic


def plan(model, aliases=None):
    desc = parse_descriptor(load(f"rusthinq/{model}.json"))
    return desc, plan_entities(desc, aliases)


def by_key(specs):
    return {s.key: s for s in specs}


# ---- parsing --------------------------------------------------------------------------


def test_every_fixture_parses_and_plans(descriptor_doc):
    desc = parse_descriptor(descriptor_doc)
    specs = plan_entities(desc)
    assert specs
    assert len({s.unique_id for s in specs}) == len(specs)


def test_every_property_lands_in_exactly_one_entity(descriptor_doc):
    desc = parse_descriptor(descriptor_doc)
    specs = plan_entities(desc)
    owned = [p for s in specs for p in s.slots.values()]
    assert len(owned) == len(set(owned)), "a property is owned by two entities"
    missing = set(desc.props) - set(owned) - {availability_prop(desc)}
    assert not missing


def test_a_descriptor_needs_an_id_and_props():
    with pytest.raises(DescriptorError):
        parse_descriptor({"props": {}})
    with pytest.raises(DescriptorError):
        parse_descriptor({"id": "x"})
    with pytest.raises(DescriptorError):
        parse_descriptor([])


def test_unknown_types_and_fields_are_ignored():
    desc = parse_descriptor(
        {
            "id": "x",
            "future": 1,
            "props": {
                "a": {"type": "binary", "rw": True, "surprise": 3},
                "b": {"type": "quaternion"},
                "c": "not even an object",
            },
        }
    )
    assert list(desc.props) == ["a"]


def test_a_higher_il_version_is_still_read():
    assert parse_descriptor({"il": 7, "id": "x", "props": {}}).il == 7


# ---- topics ---------------------------------------------------------------------------


def test_topics_come_from_the_descriptors_x_mqtt():
    desc = parse_descriptor(load("rusthinq/DHUM_056905_WW.json"))
    topics = resolve_topics(desc)
    assert topics.state_topic("power") == "rusthinq/harness/power"
    assert topics.set_topic("power") == "rusthinq/harness/power/set"
    assert topics.reject == "rusthinq/harness/reject"


def test_topics_default_to_the_il_prefix():
    desc = parse_descriptor({"id": "d1", "props": {"p": {"type": "binary"}}})
    topics = resolve_topics(desc, "il")
    assert topics.state_topic("p") == "il/d1/p"
    assert topics.set_topic("p") == "il/d1/p/set"
    assert topics.reject == "il/d1/reject"


def test_a_property_can_override_its_own_topics():
    desc = parse_descriptor(
        {"id": "d1", "props": {"p": {"type": "binary", "x-mqtt": {"state": "tuya/{id}/dp1"}}}}
    )
    topics = resolve_topics(desc)
    assert state_topic(desc, topics, "p") == "tuya/d1/dp1"
    assert set_topic(desc, topics, "p") == "il/d1/p/set"


# ---- values ---------------------------------------------------------------------------


def props(model):
    return parse_descriptor(load(f"rusthinq/{model}.json")).props


def test_values_decode_by_type():
    p = props("DHUM_056905_WW")
    assert decode_value(p["power"], "true") is True
    assert decode_value(p["power"], "false") is False
    assert decode_value(p["target"], "45") == 45
    assert decode_value(p["target"], "45.5") == 45.5
    assert decode_value(p["mode"], "smart") == "smart"


def test_an_empty_or_unfitting_payload_is_absent():
    p = props("DHUM_056905_WW")
    assert decode_value(p["power"], "") is None
    assert decode_value(p["power"], "maybe") is None
    assert decode_value(p["target"], "abc") is None
    assert decode_value(p["target"], "nan") is None
    assert decode_value(p["power"], None) is None
    assert decode_value(p["power"], b"true") is True


def test_commands_are_encoded_in_the_wire_form():
    p = props("DHUM_056905_WW")
    assert encode_command(p["power"], True) == "true"
    assert encode_command(p["power"], False) == "false"
    assert encode_command(p["target"], 45.0) == "45"
    assert encode_command(p["target"], 24.5) == "24.5"
    assert encode_command(p["mode"], "jet") == "jet"
    trigger = props("F24VDD")["start"]
    assert encode_command(trigger, None) == ""


# ---- planning: composites -------------------------------------------------------------


def test_the_air_conditioner_is_a_climate_with_its_roles():
    _, specs = plan("CST_570004_WW")
    climate = by_key(specs)["climate"]
    assert climate.platform == "climate" and climate.name is None
    assert dict(climate.slots) == {
        "on": "power", "mode": "mode", "fan_speed": "fan",
        "target_temperature": "target", "current_temperature": "temperature",
        "swing_vertical": "swing_vertical", "swing_horizontal": "swing_horizontal",
        "action": "action", "current_humidity": "humidity",
    }
    assert (climate.min, climate.max, climate.step) == (16, 30, 0.5)
    # the climate owns those properties, the measured humidity included (it is an attribute
    # of the climate, so no sensor repeats it); the rest keep their own entities
    keys = set(by_key(specs))
    assert {"power", "mode", "fan", "target", "temperature", "action", "humidity"}.isdisjoint(keys)
    assert {"energy_save", "wind_mode", "sleep_timer", "filter_used"} <= keys


def test_the_dehumidifier_is_a_humidifier_of_class_dehumidifier():
    _, specs = plan("DHUM_056905_WW")
    h = by_key(specs)["humidifier"]
    assert h.platform == "humidifier" and h.device_class == "dehumidifier"
    assert dict(h.slots) == {"on": "power", "mode": "mode", "target_humidity": "target"}
    assert (h.min, h.max, h.step) == (30, 70, 1)
    keys = set(by_key(specs))
    # the fan strength and the measured humidity keep their own entities
    assert {"fan", "humidity", "temperature", "tank_full"} <= keys
    assert "target" not in keys and "power" not in keys


def test_the_air_purifier_is_a_fan():
    _, specs = plan("AIR_910604_WW")
    fan = by_key(specs)["fan"]
    assert fan.platform == "fan"
    assert dict(fan.slots) == {"on": "power", "mode": "mode", "fan_speed": "fan"}


def test_appliances_without_a_power_role_have_no_composite():
    for model in ("F24VDD", "RH14_N_KR", "Pd0F_F", "S3BF_POD_DN4", "WBEY3GT", "D140110", "1WPU4CIGCR__2"):
        _, specs = plan(model)
        assert not {"climate", "humidifier", "fan"} & {s.platform for s in specs}, model


def test_a_climate_without_temperatures_falls_back_to_plain_entities():
    desc = parse_descriptor(
        {"id": "x", "kind": "climate", "props": {"p": {"type": "binary", "rw": True, "role": "on"}}}
    )
    assert [s.platform for s in plan_entities(desc)] == ["switch"]


# ---- planning: plain entities ---------------------------------------------------------


def test_types_choose_the_platform():
    _, specs = plan("CST_570004_WW")
    k = by_key(specs)
    assert k["swing_vertical"].platform == "climate" if "swing_vertical" in k else True
    assert k["energy_save"].platform == "switch"
    assert k["wind_mode"].platform == "select"
    assert k["sleep_timer"].platform == "number"
    assert k["power_draw"].platform == "sensor"
    assert k["error"].platform == "sensor"
    assert "humidity" not in k  # the climate carries it
    _, laundry = plan("F24VDD")
    k = by_key(laundry)
    assert k["start"].platform == "button"
    assert k["course_select"].platform == "select"
    assert k["course"].platform == "sensor"  # a read only select is a plain sensor
    assert k["remote_start"].platform == "binary_sensor"
    assert k["error_message"].platform == "sensor"


def test_class_series_and_category_carry_over():
    _, specs = plan("CST_570004_WW")
    k = by_key(specs)
    assert k["power_draw"].device_class == "power"
    assert k["power_draw"].state_class == "measurement"
    assert by_key(plan("DHUM_056905_WW")[1])["humidity"].device_class == "humidity"
    assert k["filter_used"].device_class == "duration"
    assert k["filter_used"].state_class == "total_increasing"
    assert k["error"].entity_category == "diagnostic"
    assert k["energy_save"].entity_category == "config"
    assert k["sleep_timer"].entity_category is None
    _, laundry = plan("RH14_N_KR")
    k = by_key(laundry)
    assert k["energy"].device_class == "energy" and k["energy"].state_class == "total_increasing"
    assert k["error"].device_class == "problem"
    assert k["power"].device_class == "running"
    assert k["remote_start"].entity_category == "diagnostic"


def test_requires_travels_with_the_controls():
    _, specs = plan("F24VDD")
    assert by_key(specs)["start"].requires == "remote_start"
    _, cooktop = plan("WBEY3GT")
    assert by_key(cooktop)["right_remaining_time"].requires == "remote_start"


def test_grouped_properties_are_named_with_their_label():
    _, specs = plan("WBEY3GT")
    assert by_key(specs)["right_state"].name == "Right state"


def test_availability_is_not_an_entity_but_the_devices_state():
    desc, specs = plan("DHUM_056905_WW")
    assert availability_prop(desc) == "available"
    assert "available" not in by_key(specs)


# ---- planning: unique ids -------------------------------------------------------------


def test_unique_ids_are_the_device_id_and_the_key():
    _, specs = plan("DHUM_056905_WW")
    assert by_key(specs)["humidifier"].unique_id == "harness-humidifier"
    assert by_key(specs)["uvnano"].unique_id == "harness-uvnano"


def test_aliases_keep_the_ids_an_earlier_integration_created():
    _, specs = plan("DHUM_056905_WW", {"uvnano": "uv_nano", "tank_full": "bucket_full"})
    ids = {s.key: s.unique_id for s in specs}
    assert ids["uvnano"] == "harness-uv_nano"
    assert ids["tank_full"] == "harness-bucket_full"
    assert ids["sterilize"] == "harness-sterilize"


def test_aliases_that_collide_are_refused():
    desc = parse_descriptor(load("rusthinq/DHUM_056905_WW.json"))
    with pytest.raises(ValueError):
        plan_entities(desc, {"uvnano": "sterilize"})
