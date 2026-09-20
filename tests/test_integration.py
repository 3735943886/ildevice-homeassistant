"""The integration in a real (test) Home Assistant, driven by MQTT messages the way a producer would."""

import json
from datetime import timedelta

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)

from conftest import load
from custom_components.il_ha.const import DOMAIN, EVENT_COMMAND_REJECTED


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations, mock_hass_config):
    return


async def setup(hass, **options):
    entry = MockConfigEntry(domain=DOMAIN, options={"il_prefix": "il", **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def descriptor(model, device_id):
    doc = load(f"rusthinq/{model}.json")
    doc["id"] = device_id
    return doc


def announce(hass, doc):
    async_fire_mqtt_message(hass, f"il/{doc['id']}", json.dumps(doc))


def value(hass, device_id, prop, payload):
    async_fire_mqtt_message(hass, f"rusthinq/{device_id}/{prop}", payload)


def assert_published(mqtt, topic, payload):
    sent = [(c.args[0], c.args[1]) for c in mqtt.async_publish.call_args_list]
    assert (topic, payload) in sent, f"{(topic, payload)} not among {sent}"


def entity_id(hass, platform, unique_id):
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert found, f"no {platform} entity {unique_id}"
    return found


# ---- the dehumidifier: humidifier + plain entities ------------------------------------


async def test_the_dehumidifier_becomes_a_humidifier_and_its_plain_entities(hass, mqtt_mock):
    await setup(hass)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()

    for prop, payload in [
        ("available", "true"), ("power", "true"), ("mode", "smart"), ("target", "45"),
        ("humidity", "52"), ("temperature", "27.5"), ("fan", "low"), ("uvnano", "true"),
        ("tank_full", "false"), ("error", "0"),
    ]:
        value(hass, "dhum1", prop, payload)
    await hass.async_block_till_done()

    h = hass.states.get(entity_id(hass, "humidifier", "dhum1-humidifier"))
    assert h.state == STATE_ON
    assert h.attributes["humidity"] == 45
    assert h.attributes["mode"] == "smart"
    assert h.attributes["device_class"] == "dehumidifier"
    assert (h.attributes["min_humidity"], h.attributes["max_humidity"]) == (30, 70)
    assert h.attributes["available_modes"] == ["smart", "jet", "silent", "spot", "laundry"]

    humidity = hass.states.get(entity_id(hass, "sensor", "dhum1-humidity"))
    assert humidity.state == "52"
    assert humidity.attributes["device_class"] == "humidity"
    assert humidity.attributes["state_class"] == "measurement"
    assert hass.states.get(entity_id(hass, "sensor", "dhum1-temperature")).state == "27.5"
    assert hass.states.get(entity_id(hass, "select", "dhum1-fan")).state == "low"
    assert hass.states.get(entity_id(hass, "switch", "dhum1-uvnano")).state == STATE_ON
    assert hass.states.get(entity_id(hass, "binary_sensor", "dhum1-tank_full")).state == STATE_OFF
    assert er.async_get(hass).async_get(entity_id(hass, "sensor", "dhum1-error")).entity_category == "diagnostic"

    device = next(d for d in dr.async_get(hass).devices if (DOMAIN, "dhum1") in d.identifiers)
    assert device.manufacturer == "LG" and device.model == "DHUM_056905_WW"


async def test_commands_go_to_the_set_topics_in_the_wire_form(hass, mqtt_mock):
    await setup(hass)
    mqtt = mqtt_mock
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    value(hass, "dhum1", "available", "true")
    value(hass, "dhum1", "power", "true")
    await hass.async_block_till_done()
    h = entity_id(hass, "humidifier", "dhum1-humidifier")

    await hass.services.async_call("humidifier", "set_humidity", {"entity_id": h, "humidity": 55}, blocking=True)
    assert_published(mqtt, "rusthinq/dhum1/target/set", "55")
    await hass.services.async_call("humidifier", "set_mode", {"entity_id": h, "mode": "jet"}, blocking=True)
    assert_published(mqtt, "rusthinq/dhum1/mode/set", "jet")
    await hass.services.async_call("humidifier", "turn_off", {"entity_id": h}, blocking=True)
    assert_published(mqtt, "rusthinq/dhum1/power/set", "false")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id(hass, "switch", "dhum1-uvnano")}, blocking=True
    )
    assert_published(mqtt, "rusthinq/dhum1/uvnano/set", "true")
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": entity_id(hass, "select", "dhum1-fan"), "option": "high"}, blocking=True,
    )
    assert_published(mqtt, "rusthinq/dhum1/fan/set", "high")


# ---- the air conditioner: climate -----------------------------------------------------


async def test_the_air_conditioner_is_a_climate(hass, mqtt_mock):
    await setup(hass)
    mqtt = mqtt_mock
    announce(hass, descriptor("CST_570004_WW", "ac1"))
    await hass.async_block_till_done()
    for prop, payload in [
        ("available", "true"), ("power", "true"), ("mode", "cool"), ("fan", "high"),
        ("target", "24.5"), ("temperature", "26"), ("humidity", "51"), ("action", "cooling"),
        ("swing_vertical", "true"), ("swing_horizontal", "false"),
    ]:
        value(hass, "ac1", prop, payload)
    await hass.async_block_till_done()

    c = hass.states.get(entity_id(hass, "climate", "ac1-climate"))
    assert c.state == "cool"
    assert c.attributes["temperature"] == 24.5
    assert c.attributes["current_temperature"] == 26
    assert c.attributes["current_humidity"] == 51
    assert c.attributes["fan_mode"] == "high"
    assert c.attributes["swing_mode"] == "on"
    assert c.attributes["swing_horizontal_mode"] == "off"
    assert c.attributes["hvac_action"] == "cooling"
    assert c.attributes["hvac_modes"] == ["off", "cool", "dry", "fan_only", "auto"]
    assert (c.attributes["min_temp"], c.attributes["max_temp"]) == (16, 30)
    assert c.attributes["target_temp_step"] == 0.5

    cid = entity_id(hass, "climate", "ac1-climate")
    await hass.services.async_call("climate", "set_temperature", {"entity_id": cid, "temperature": 23}, blocking=True)
    assert_published(mqtt, "rusthinq/ac1/target/set", "23")
    await hass.services.async_call("climate", "set_fan_mode", {"entity_id": cid, "fan_mode": "low"}, blocking=True)
    assert_published(mqtt, "rusthinq/ac1/fan/set", "low")
    await hass.services.async_call("climate", "set_swing_mode", {"entity_id": cid, "swing_mode": "off"}, blocking=True)
    assert_published(mqtt, "rusthinq/ac1/swing_vertical/set", "false")
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": cid, "hvac_mode": "dry"}, blocking=True)
    assert_published(mqtt, "rusthinq/ac1/mode/set", "dry")
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": cid, "hvac_mode": "off"}, blocking=True)
    assert_published(mqtt, "rusthinq/ac1/power/set", "false")


async def test_a_mode_from_off_turns_the_unit_on_first(hass, mqtt_mock):
    await setup(hass)
    mqtt = mqtt_mock
    announce(hass, descriptor("CST_570004_WW", "ac1"))
    await hass.async_block_till_done()
    value(hass, "ac1", "available", "true")
    value(hass, "ac1", "power", "false")
    value(hass, "ac1", "mode", "cool")
    await hass.async_block_till_done()
    cid = entity_id(hass, "climate", "ac1-climate")
    assert hass.states.get(cid).state == "off"
    mqtt.async_publish.reset_mock()
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": cid, "hvac_mode": "cool"}, blocking=True)
    topics = [c.args[0] for c in mqtt.async_publish.call_args_list]
    assert topics == ["rusthinq/ac1/power/set", "rusthinq/ac1/mode/set"]


# ---- appliances: plain entities, requires, buttons -----------------------------------


async def test_a_washer_is_plain_entities_and_its_controls_follow_remote_start(hass, mqtt_mock):
    await setup(hass)
    mqtt = mqtt_mock
    announce(hass, descriptor("F24VDD", "w1"))
    await hass.async_block_till_done()
    value(hass, "w1", "available", "true")
    value(hass, "w1", "status", "washing")
    value(hass, "w1", "remaining_time", "93")
    value(hass, "w1", "energy", "300")
    value(hass, "w1", "power", "true")
    value(hass, "w1", "remote_start", "false")
    await hass.async_block_till_done()

    assert hass.states.get(entity_id(hass, "sensor", "w1-status")).state == "washing"
    remaining = hass.states.get(entity_id(hass, "sensor", "w1-remaining_time"))
    assert remaining.state == "93" and remaining.attributes["device_class"] == "duration"
    energy = hass.states.get(entity_id(hass, "sensor", "w1-energy"))
    assert energy.attributes["state_class"] == "total_increasing"
    assert energy.attributes["device_class"] == "energy"
    assert hass.states.get(entity_id(hass, "binary_sensor", "w1-power")).attributes["device_class"] == "running"

    start = entity_id(hass, "button", "w1-start")
    assert hass.states.get(start).state == STATE_UNAVAILABLE, "start needs remote start armed at the panel"
    value(hass, "w1", "remote_start", "true")
    await hass.async_block_till_done()
    assert hass.states.get(start).state != STATE_UNAVAILABLE
    await hass.services.async_call("button", "press", {"entity_id": start}, blocking=True)
    assert_published(mqtt, "rusthinq/w1/start/set", "")


# ---- availability, rejections, removal ------------------------------------------------


async def test_the_device_is_unavailable_when_it_says_so(hass, mqtt_mock):
    await setup(hass)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    value(hass, "dhum1", "available", "true")
    value(hass, "dhum1", "power", "true")
    await hass.async_block_till_done()
    h = entity_id(hass, "humidifier", "dhum1-humidifier")
    assert hass.states.get(h).state == STATE_ON
    value(hass, "dhum1", "available", "false")
    await hass.async_block_till_done()
    assert hass.states.get(h).state == STATE_UNAVAILABLE


async def test_a_short_outage_inside_the_grace_period_is_not_shown(hass, mqtt_mock):
    await setup(hass, offline_grace=60)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    value(hass, "dhum1", "available", "true")
    value(hass, "dhum1", "power", "true")
    await hass.async_block_till_done()
    h = entity_id(hass, "humidifier", "dhum1-humidifier")
    value(hass, "dhum1", "available", "false")
    await hass.async_block_till_done()
    assert hass.states.get(h).state == STATE_ON  # the grace period has not run out
    value(hass, "dhum1", "available", "true")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=120))
    await hass.async_block_till_done()
    assert hass.states.get(h).state == STATE_ON  # it came back in time: never shown offline


async def test_an_outage_that_outlasts_the_grace_period_is_shown(hass, mqtt_mock):
    await setup(hass, offline_grace=60)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    value(hass, "dhum1", "available", "true")
    value(hass, "dhum1", "power", "true")
    await hass.async_block_till_done()
    h = entity_id(hass, "humidifier", "dhum1-humidifier")
    value(hass, "dhum1", "available", "false")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(h).state == STATE_UNAVAILABLE


async def test_a_refused_command_is_fired_as_an_event(hass, mqtt_mock):
    await setup(hass)
    announce(hass, descriptor("F24VDD", "w1"))
    await hass.async_block_till_done()
    events = []
    hass.bus.async_listen(EVENT_COMMAND_REJECTED, lambda e: events.append(e.data))
    async_fire_mqtt_message(hass, "rusthinq/w1/reject", json.dumps({"prop": "start", "reason": "requires remote_start"}))
    await hass.async_block_till_done()
    assert events == [{"device_id": "w1", "prop": "start", "reason": "requires remote_start"}]


async def test_an_empty_descriptor_removes_the_device(hass, mqtt_mock):
    await setup(hass)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get_entity_id("humidifier", DOMAIN, "dhum1-humidifier")
    async_fire_mqtt_message(hass, "il/dhum1", "")
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get_entity_id("humidifier", DOMAIN, "dhum1-humidifier") is None


async def test_a_bad_descriptor_is_ignored(hass, mqtt_mock):
    await setup(hass)
    async_fire_mqtt_message(hass, "il/x", "not json")
    async_fire_mqtt_message(hass, "il/y", json.dumps({"props": {}}))
    await hass.async_block_till_done()
    assert er.async_get(hass).entities.get_entries_for_config_entry_id  # nothing raised


# ---- legacy ids -----------------------------------------------------------------------


async def test_aliases_keep_the_unique_ids_an_earlier_integration_used(hass, mqtt_mock):
    aliases = json.dumps({"DHUM_056905_WW": {"uvnano": "uv_nano", "tank_full": "bucket_full"}})
    await setup(hass, aliases=aliases)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, "dhum1-uv_nano")
    assert er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "dhum1-bucket_full")
    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, "dhum1-uvnano") is None


# ---- a second producer ----------------------------------------------------------------


async def test_a_device_from_another_producer_needs_no_code_of_its_own(hass, mqtt_mock):
    """A Tuya plug described by a different producer, with its own topic layout: same integration,
    same code path, and the values and commands go where the descriptor says."""
    await setup(hass)
    doc = load("sample/tuya_plug.json")
    announce(hass, doc)
    await hass.async_block_till_done()
    for topic, payload in [
        ("tuya/bf3a91c0d2e4/dp1", "true"), ("tuya/bf3a91c0d2e4/dp19", "42.5"),
        ("tuya/bf3a91c0d2e4/dp20", "231"), ("tuya/bf3a91c0d2e4/dp17", "12.34"),
        ("tuya/bf3a91c0d2e4/dp38", "last"),
        ("il/bf3a91c0d2e4/available", "true"),  # no x-mqtt for it: the default topic
    ]:
        async_fire_mqtt_message(hass, topic, payload)
    await hass.async_block_till_done()

    plug = "bf3a91c0d2e4"
    assert hass.states.get(entity_id(hass, "switch", f"{plug}-switch_1")).state == STATE_ON
    power = hass.states.get(entity_id(hass, "sensor", f"{plug}-cur_power"))
    assert (power.state, power.attributes["device_class"], power.attributes["unit_of_measurement"]) == ("42.5", "power", "W")
    energy = hass.states.get(entity_id(hass, "sensor", f"{plug}-add_ele"))
    assert energy.attributes["state_class"] == "total_increasing" and energy.attributes["device_class"] == "energy"
    assert hass.states.get(entity_id(hass, "select", f"{plug}-relay_status")).state == "last"
    registry = er.async_get(hass)
    assert registry.async_get(entity_id(hass, "sensor", f"{plug}-cur_voltage")).entity_category == "diagnostic"
    assert registry.async_get(entity_id(hass, "switch", f"{plug}-child_lock")).entity_category == "config"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id(hass, "switch", f"{plug}-switch_1")}, blocking=True
    )
    assert_published(mqtt_mock, "tuya/bf3a91c0d2e4/dp1/set", "false")
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": entity_id(hass, "number", f"{plug}-countdown_1"), "value": 600}, blocking=True,
    )
    assert_published(mqtt_mock, "tuya/bf3a91c0d2e4/dp9/set", "600")

    events = []
    hass.bus.async_listen(EVENT_COMMAND_REJECTED, lambda e: events.append(e.data))
    async_fire_mqtt_message(hass, "tuya/bf3a91c0d2e4/reject", json.dumps({"prop": "countdown_1", "reason": "above the maximum of 86400"}))
    await hass.async_block_till_done()
    assert events[0]["reason"] == "above the maximum of 86400"
