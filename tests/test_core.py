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


def sample(name, aliases=None):
    desc = parse_descriptor(load(f"sample/{name}.json"))
    return desc, plan_entities(desc, aliases)


def test_a_light_owns_its_roles():
    _, specs = sample("tuya_light")
    light = by_key(specs)["light"]
    assert light.platform == "light"
    assert dict(light.slots) == {
        "on": "power", "brightness": "bright", "color_temperature": "temp",
        "color": "colour", "color_mode": "mode",
    }
    assert [s.platform for s in specs] == ["light"]


def test_a_cover_needs_a_position_or_a_way_to_open_and_close():
    _, specs = sample("tuya_curtain")
    cover = by_key(specs)["cover"]
    assert cover.platform == "cover" and cover.device_class == "curtain"
    assert dict(cover.slots) == {
        "position": "position", "motion": "motion", "open": "open", "close": "close", "stop": "stop",
    }
    desc = parse_descriptor({"id": "x", "kind": "cover", "props": {"m": {"type": "select", "role": "motion", "options": ["stopped"]}}})
    assert [s.platform for s in plan_entities(desc)] == ["sensor"]


def test_a_garage_door_is_the_garage_device_class():
    desc = parse_descriptor(
        {"id": "x", "kind": "cover", "class": "garage_door", "props": {"o": {"type": "trigger", "role": "open"}}}
    )
    assert by_key(plan_entities(desc))["cover"].device_class == "garage"


def test_a_lock_is_only_a_lock_when_it_can_be_written():
    _, specs = sample("tuya_lock")
    lock = by_key(specs)["lock"]
    assert lock.platform == "lock" and dict(lock.slots) == {"locked": "locked", "unlatch": "unlatch"}
    assert by_key(specs)["battery"].platform == "sensor"
    desc = parse_descriptor(
        {"id": "x", "kind": "lock", "props": {"l": {"type": "binary", "role": "locked"}, "u": {"type": "trigger", "role": "unlatch"}}}
    )
    assert {s.key: s.platform for s in plan_entities(desc)} == {"l": "binary_sensor", "u": "button"}


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
    assert by_key(specs)["start"].requires.prop == "remote_start"
    _, cooktop = plan("WBEY3GT")
    assert by_key(cooktop)["right_remaining_time"].requires.prop == "remote_start"


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


# ---- planning: a group with its own kind is a composite of its own -------------------------


def _two_composites():
    return parse_descriptor({
        "il": 0, "id": "clkg1", "kind": "cover",
        "groups": {"light": {"kind": "light", "label": "Light"}},
        "props": {
            "position": {"type": "number", "rw": True, "role": "position", "unit": "%", "min": 0, "max": 100, "step": 1},
            "open": {"type": "trigger", "role": "open"},
            "close": {"type": "trigger", "role": "close"},
            "switch_led": {"type": "binary", "rw": True, "role": "on", "group": "light"},
            "brightness": {"type": "number", "rw": True, "role": "brightness", "unit": "%", "min": 1, "max": 100, "step": 1, "group": "light"},
        },
    })


def test_a_group_with_a_kind_is_its_own_composite():
    specs = by_key(plan_entities(_two_composites()))
    assert {s.platform for s in specs.values()} == {"cover", "light"}
    assert dict(specs["cover"].slots) == {"position": "position", "open": "open", "close": "close"}
    assert dict(specs["light"].slots) == {"on": "switch_led", "brightness": "brightness"}
    assert specs["light"].unique_id == "clkg1-light" and specs["light"].name == "Light"


def test_two_lights_on_one_device_each_own_their_roles():
    desc = parse_descriptor({
        "il": 0, "id": "dj1",
        "groups": {"switch_led": {"kind": "light"}, "switch_1": {"kind": "light", "label": "Second"}},
        "props": {
            "led": {"type": "binary", "rw": True, "role": "on", "group": "switch_led"},
            "one": {"type": "binary", "rw": True, "role": "on", "group": "switch_1"},
        },
    })
    specs = plan_entities(desc)
    assert [(s.platform, s.key, s.slots["on"]) for s in specs] == [("light", "switch_led", "led"), ("light", "switch_1", "one")]


def test_a_group_without_a_kind_stays_a_label_and_the_device_kind_keeps_its_roles():
    desc = parse_descriptor({
        "il": 0, "id": "x", "kind": "fan",
        "props": {"power": {"type": "binary", "rw": True, "role": "on", "group": "left"}},
    })
    specs = plan_entities(desc)
    assert [s.platform for s in specs] == ["fan"]


def test_a_kinded_group_that_cannot_form_its_composite_falls_back_to_plain_entities():
    desc = parse_descriptor({
        "il": 0, "id": "x", "groups": {"light": {"kind": "light"}},
        "props": {"level": {"type": "number", "rw": True, "role": "brightness", "group": "light"}},
    })
    assert [s.platform for s in plan_entities(desc)] == ["number"]


# ---- planning: classes and categories the first Tuya spike found missing ---------------------


def _one(prop, **extra):
    return plan_entities(parse_descriptor({"il": 0, "id": "x", "props": {"p": prop}, **extra}))[0]


def test_a_writable_binary_keeps_its_class():
    spec = _one({"type": "binary", "rw": True, "class": "outlet"})
    assert spec.platform == "switch" and spec.device_class == "outlet"


def test_a_trigger_keeps_its_class_and_can_be_a_diagnostic():
    spec = _one({"type": "trigger", "class": "restart", "category": "diagnostic"})
    assert (spec.platform, spec.device_class, spec.entity_category) == ("button", "restart", "diagnostic")


def test_a_read_only_select_is_an_enumeration_sensor():
    spec = _one({"type": "select", "options": ["a", "b"]})
    assert (spec.platform, spec.device_class, spec.options) == ("sensor", "enum", ("a", "b"))
    assert _one({"type": "select", "rw": True, "options": ["a"]}).device_class is None


# ---- planning: siren and valve ---------------------------------------------------------------


def test_a_siren_is_on_and_off():
    _, specs = sample("tuya_siren")
    assert [(s.platform, dict(s.slots)) for s in specs] == [("siren", {"on": "switch"})]


def test_a_valve_is_opened_and_closed_and_only_when_it_can_be_written():
    _, specs = sample("tuya_valve")
    assert [(s.platform, dict(s.slots)) for s in specs] == [("valve", {"opened": "switch"})]
    desc = parse_descriptor({"il": 0, "id": "v", "kind": "valve", "props": {"p": {"type": "binary", "role": "opened"}}})
    assert [s.platform for s in plan_entities(desc)] == ["binary_sensor"]


def test_an_event_property_is_an_event_entity_with_its_kinds_as_options():
    _, specs = sample("tuya_button")
    assert [(s.platform, s.options) for s in specs] == [("event", ("click", "double_click", "long_press"))]


# ---- requires, role types, minimal composites ---------------------------------------------


def test_requires_has_a_select_form():
    desc = parse_descriptor({"id": "x", "props": {
        "m": {"type": "select", "options": ["cool", "heat"]},
        "t": {"type": "number", "rw": True, "requires": {"prop": "m", "in": ["cool"]}},
        "bad": {"type": "number", "rw": True, "requires": {"prop": "m", "in": []}},
    }})
    req = desc.props["t"].requires
    assert req.met({"m": "cool"}) and not req.met({"m": "heat"}) and not req.met({})
    assert desc.props["bad"].requires is None


def test_a_role_on_the_wrong_type_is_ignored():
    desc = parse_descriptor({"id": "x", "kind": "fan", "props": {"p": {"type": "number", "rw": True, "role": "on"}}})
    assert [s.platform for s in plan_entities(desc)] == ["number"]


def test_a_climate_needs_only_a_target_temperature():
    desc = parse_descriptor({"id": "x", "kind": "climate", "props": {
        "t": {"type": "number", "rw": True, "role": "target_temperature", "unit": "\u00b0C"}}})
    assert [s.platform for s in plan_entities(desc)] == ["climate"]
    desc = parse_descriptor({"id": "x", "kind": "climate", "props": {
        "c": {"type": "number", "role": "current_temperature"},
        "p": {"type": "binary", "rw": True, "role": "on"}}})
    assert [s.platform for s in plan_entities(desc)] == ["sensor", "switch"]


def test_micro_is_normalised():
    desc = parse_descriptor({"id": "x", "props": {"p": {"type": "number", "unit": "\u00b5g/m\u00b3"}}})
    assert desc.props["p"].unit == "\u03bcg/m\u00b3"
