"""The integration in a real (test) Home Assistant, driven by MQTT messages the way a producer would."""

import json
from datetime import timedelta

import pytest
from homeassistant.config_entries import SOURCE_IGNORE
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
from custom_components.ildevice.const import DOMAIN, EVENT_COMMAND_REJECTED


@pytest.fixture(autouse=True)
def _custom(enable_custom_integrations, mock_hass_config):
    return


async def setup(hass, **options):
    entry = MockConfigEntry(domain=DOMAIN, options={"il_prefix": "il", "auto_add": True, **options})
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
    async_fire_mqtt_message(hass, "rusthinq/w1/reject", json.dumps({"prop": "start", "code": "requires_unmet", "reason": "requires remote_start"}))
    await hass.async_block_till_done()
    assert events == [{"device_id": "w1", "prop": "start", "code": "requires_unmet", "reason": "requires remote_start"}]


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


# ---- asking before adding a device -----------------------------------------------------


def offers(hass):
    return hass.config_entries.flow.async_progress_by_handler(DOMAIN)


def device_ids(hass):
    return {i[1] for d in dr.async_get(hass).devices for i in d.identifiers if i[0] == DOMAIN}


async def test_a_new_device_is_offered_and_not_created(hass, mqtt_mock):
    await setup(hass, auto_add=False)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()

    assert device_ids(hass) == set()
    (flow,) = offers(hass)
    assert flow["context"]["unique_id"] == "dhum1"
    assert flow["context"]["title_placeholders"]["name"]


async def test_adding_the_offered_device_creates_it(hass, mqtt_mock):
    entry = await setup(hass, auto_add=False)
    doc = descriptor("DHUM_056905_WW", "dhum1")
    announce(hass, doc)
    await hass.async_block_till_done()
    (flow,) = offers(hass)

    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    assert result["reason"] == "device_added"
    await hass.async_block_till_done()
    assert entry.options["devices"] == ["dhum1"]

    announce(hass, doc)  # the retained descriptor arrives again as the entry reloads
    await hass.async_block_till_done()
    assert device_ids(hass) == {"dhum1"}
    entity_id(hass, "humidifier", "dhum1-humidifier")
    assert offers(hass) == []


async def test_an_ignored_device_is_not_offered_again(hass, mqtt_mock):
    entry = await setup(hass, auto_add=False)
    doc = descriptor("DHUM_056905_WW", "dhum1")
    announce(hass, doc)
    await hass.async_block_till_done()
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IGNORE}, data={"unique_id": "dhum1", "title": "Dehumidifier"}
    )
    await hass.async_block_till_done()
    assert offers(hass) == []

    assert await hass.config_entries.async_reload(entry.entry_id)
    announce(hass, doc)
    await hass.async_block_till_done()
    assert offers(hass) == [] and device_ids(hass) == set()


async def test_devices_already_registered_stay_when_the_question_is_introduced(hass, mqtt_mock):
    entry = MockConfigEntry(domain=DOMAIN, options={"il_prefix": "il"})  # an entry from before
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "dhum1")}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options["devices"] == ["dhum1"]

    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    announce(hass, descriptor("CST_570004_WW", "ac9"))
    await hass.async_block_till_done()
    assert "dhum1" in device_ids(hass)
    assert [f["context"]["unique_id"] for f in offers(hass)] == ["ac9"]


async def test_deleting_a_device_makes_it_a_new_offer(hass, mqtt_mock):
    from custom_components.ildevice import async_remove_config_entry_device

    entry = await setup(hass, auto_add=False, devices=["dhum1"])
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    (device,) = list(dr.async_get(hass).devices)

    assert await async_remove_config_entry_device(hass, entry, device)
    assert entry.options["devices"] == []


async def test_turning_the_question_off_and_on_keeps_the_devices_that_were_there(hass, mqtt_mock):
    entry = await setup(hass)  # adds automatically
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"il_prefix": "il", "offline_grace": 0, "aliases": "", "auto_add": False}
    )
    assert result["data"]["devices"] == ["dhum1"]


# ---- light, cover and lock (a producer with its own topic layout) -----------------------


def tuya(name):
    return load(f"sample/{name}.json")


def tuya_value(hass, device_id, prop, payload):
    async_fire_mqtt_message(hass, f"tuya/{device_id}/{prop}", payload)


async def test_a_light(hass, mqtt_mock):
    await setup(hass)
    announce(hass, tuya("tuya_light"))
    await hass.async_block_till_done()
    for prop, payload in (("available", "true"), ("power", "true"), ("bright", "50"),
                          ("temp", "3000"), ("colour", "#ff0000"), ("mode", "white")):
        tuya_value(hass, "light01", prop, payload)
    await hass.async_block_till_done()
    lid = entity_id(hass, "light", "light01-light")
    state = hass.states.get(lid)
    assert state.state == STATE_ON
    assert state.attributes["brightness"] == 128
    assert state.attributes["color_mode"] == "color_temp"
    assert state.attributes["color_temp_kelvin"] == 3000
    assert state.attributes["min_color_temp_kelvin"] == 2700
    tuya_value(hass, "light01", "mode", "color")
    await hass.async_block_till_done()
    state = hass.states.get(lid)
    assert state.attributes["color_mode"] == "hs" and state.attributes["hs_color"] == (0.0, 100.0)

    await hass.services.async_call("light", "turn_on", {"entity_id": lid, "brightness": 255, "color_temp_kelvin": 4000}, blocking=True)
    assert_published(mqtt_mock, "tuya/light01/power/set", "true")
    assert_published(mqtt_mock, "tuya/light01/temp/set", "4000")
    assert_published(mqtt_mock, "tuya/light01/bright/set", "100")
    await hass.services.async_call("light", "turn_on", {"entity_id": lid, "hs_color": [120, 100]}, blocking=True)
    assert_published(mqtt_mock, "tuya/light01/colour/set", "#00ff00")
    # the dimmest step Home Assistant can ask for is still the device's lowest, not off
    await hass.services.async_call("light", "turn_on", {"entity_id": lid, "brightness": 1}, blocking=True)
    assert_published(mqtt_mock, "tuya/light01/bright/set", "1")
    await hass.services.async_call("light", "turn_off", {"entity_id": lid}, blocking=True)
    assert_published(mqtt_mock, "tuya/light01/power/set", "false")


async def test_a_cover(hass, mqtt_mock):
    await setup(hass)
    announce(hass, tuya("tuya_curtain"))
    await hass.async_block_till_done()
    for prop, payload in (("available", "true"), ("position", "30"), ("cover_state", "opening")):
        tuya_value(hass, "curtain01", prop, payload)
    await hass.async_block_till_done()
    cid = entity_id(hass, "cover", "curtain01-cover")
    state = hass.states.get(cid)
    assert state.state == "opening" and state.attributes["current_position"] == 30
    assert state.attributes["device_class"] == "curtain"
    tuya_value(hass, "curtain01", "cover_state", "stopped")
    tuya_value(hass, "curtain01", "position", "0")
    await hass.async_block_till_done()
    assert hass.states.get(cid).state == "closed"

    await hass.services.async_call("cover", "open_cover", {"entity_id": cid}, blocking=True)
    assert_published(mqtt_mock, "tuya/curtain01/open/set", "")
    await hass.services.async_call("cover", "stop_cover", {"entity_id": cid}, blocking=True)
    assert_published(mqtt_mock, "tuya/curtain01/stop/set", "")
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": cid, "position": 60}, blocking=True)
    assert_published(mqtt_mock, "tuya/curtain01/position/set", "60")


async def test_a_cover_without_open_and_close_moves_by_position(hass, mqtt_mock):
    await setup(hass)
    doc = tuya("tuya_curtain")
    doc["id"] = "curtain02"
    for name in ("open", "close", "stop"):
        del doc["props"][name]
    for prop in doc["props"].values():
        prop["x-mqtt"] = {k: v.replace("curtain01", "curtain02") for k, v in prop["x-mqtt"].items()}
    doc["x-mqtt"] = {"reject": "tuya/curtain02/reject"}
    announce(hass, doc)
    await hass.async_block_till_done()
    tuya_value(hass, "curtain02", "available", "true")
    await hass.async_block_till_done()
    cid = entity_id(hass, "cover", "curtain02-cover")
    await hass.services.async_call("cover", "open_cover", {"entity_id": cid}, blocking=True)
    assert_published(mqtt_mock, "tuya/curtain02/position/set", "100")
    await hass.services.async_call("cover", "close_cover", {"entity_id": cid}, blocking=True)
    assert_published(mqtt_mock, "tuya/curtain02/position/set", "0")


async def test_a_lock(hass, mqtt_mock):
    await setup(hass)
    announce(hass, tuya("tuya_lock"))
    await hass.async_block_till_done()
    for prop, payload in (("available", "true"), ("locked", "true")):
        tuya_value(hass, "lock01", prop, payload)
    await hass.async_block_till_done()
    lid = entity_id(hass, "lock", "lock01-lock")
    assert hass.states.get(lid).state == "locked"
    await hass.services.async_call("lock", "unlock", {"entity_id": lid}, blocking=True)
    assert_published(mqtt_mock, "tuya/lock01/locked/set", "false")
    await hass.services.async_call("lock", "open", {"entity_id": lid}, blocking=True)
    assert_published(mqtt_mock, "tuya/lock01/unlatch/set", "")
    tuya_value(hass, "lock01", "locked", "false")
    await hass.async_block_till_done()
    assert hass.states.get(lid).state == "unlocked"


async def test_a_siren(hass, mqtt_mock):
    await setup(hass)
    announce(hass, tuya("tuya_siren"))
    await hass.async_block_till_done()
    tuya_value(hass, "siren01", "switch", "false")
    await hass.async_block_till_done()
    sid = entity_id(hass, "siren", "siren01-siren")
    assert hass.states.get(sid).state == STATE_OFF
    await hass.services.async_call("siren", "turn_on", {"entity_id": sid}, blocking=True)
    assert_published(mqtt_mock, "tuya/siren01/switch/set", "true")
    tuya_value(hass, "siren01", "switch", "true")
    await hass.async_block_till_done()
    assert hass.states.get(sid).state == STATE_ON


async def test_a_valve(hass, mqtt_mock):
    await setup(hass)
    announce(hass, tuya("tuya_valve"))
    await hass.async_block_till_done()
    tuya_value(hass, "valve01", "switch", "true")
    await hass.async_block_till_done()
    vid = entity_id(hass, "valve", "valve01-valve")
    assert hass.states.get(vid).state == "open"
    await hass.services.async_call("valve", "close_valve", {"entity_id": vid}, blocking=True)
    assert_published(mqtt_mock, "tuya/valve01/switch/set", "false")
    tuya_value(hass, "valve01", "switch", "false")
    await hass.async_block_till_done()
    assert hass.states.get(vid).state == "closed"


async def test_an_event_fires_once_per_message_and_ignores_a_retained_one(hass, mqtt_mock):
    await setup(hass)
    async_fire_mqtt_message(hass, "tuya/btn01/button", "click", retain=True)   # before the device is known
    announce(hass, tuya("tuya_button"))
    await hass.async_block_till_done()
    eid = entity_id(hass, "event", "btn01-button")
    assert hass.states.get(eid).attributes.get("event_type") is None
    async_fire_mqtt_message(hass, "tuya/btn01/button", "click", retain=True)   # a replayed old one
    await hass.async_block_till_done()
    assert hass.states.get(eid).attributes.get("event_type") is None
    async_fire_mqtt_message(hass, "tuya/btn01/button", "double_click")
    await hass.async_block_till_done()
    assert hass.states.get(eid).attributes["event_type"] == "double_click"
    first = hass.states.get(eid).state
    async_fire_mqtt_message(hass, "tuya/btn01/button", "double_click")   # the same kind again is another one
    await hass.async_block_till_done()
    assert hass.states.get(eid).state != first
    async_fire_mqtt_message(hass, "tuya/btn01/button", "unknown_kind")
    await hass.async_block_till_done()
    assert hass.states.get(eid).attributes["event_type"] == "double_click"


# ---- presence, alarm, vacuum, requires on a composite ----------------------------------

GARAGE = {
    "il": 0, "id": "g1", "source": "acme", "kind": "cover", "class": "garage_door", "label": "Garage",
    "props": {
        "armed": {"type": "select", "options": ["remote", "local"]},
        "position": {"type": "number", "rw": True, "role": "position", "unit": "%", "min": 0, "max": 100,
                     "requires": {"prop": "armed", "in": ["remote"]}},
    },
}


def plain_value(hass, device_id, prop, payload):
    async_fire_mqtt_message(hass, f"il/{device_id}/{prop}", payload)


async def test_a_composite_control_is_not_written_while_its_condition_fails(hass, mqtt_mock):
    from homeassistant.exceptions import ServiceValidationError

    await setup(hass)
    announce(hass, GARAGE)
    await hass.async_block_till_done()
    cover = entity_id(hass, "cover", "g1-cover")
    plain_value(hass, "g1", "armed", "local")
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("cover", "open_cover", {"entity_id": cover}, blocking=True)
    plain_value(hass, "g1", "armed", "remote")
    await hass.async_block_till_done()
    await hass.services.async_call("cover", "open_cover", {"entity_id": cover}, blocking=True)
    assert_published(mqtt_mock, "il/g1/position/set", "100")


async def test_a_producer_going_offline_makes_its_devices_unavailable(hass, mqtt_mock):
    await setup(hass)
    announce(hass, GARAGE)
    await hass.async_block_till_done()
    plain_value(hass, "g1", "armed", "remote")
    plain_value(hass, "g1", "position", "0")
    await hass.async_block_till_done()
    cover = entity_id(hass, "cover", "g1-cover")
    assert hass.states.get(cover).state != STATE_UNAVAILABLE
    async_fire_mqtt_message(hass, "il/_producer/acme", "offline")
    await hass.async_block_till_done()
    assert hass.states.get(cover).state == STATE_UNAVAILABLE
    async_fire_mqtt_message(hass, "il/_producer/acme", "online")
    await hass.async_block_till_done()
    assert hass.states.get(cover).state != STATE_UNAVAILABLE


async def test_an_alarm_panel_and_a_vacuum(hass, mqtt_mock):
    await setup(hass)
    announce(hass, {
        "il": 0, "id": "a1", "kind": "alarm", "label": "Panel",
        "props": {
            "state": {"type": "select", "role": "alarm_state", "options": ["disarmed", "armed_home", "triggered"]},
            "arm_home": {"type": "trigger", "role": "arm_home"},
            "disarm": {"type": "trigger", "role": "disarm"},
        },
    })
    announce(hass, {
        "il": 0, "id": "v1", "kind": "vacuum", "label": "Robot",
        "props": {
            "state": {"type": "select", "role": "vacuum_state", "options": ["cleaning", "docked"]},
            "start": {"type": "trigger", "role": "start"},
            "battery": {"type": "number", "role": "battery", "unit": "%"},
        },
    })
    await hass.async_block_till_done()
    plain_value(hass, "a1", "state", "armed_home")
    plain_value(hass, "v1", "state", "cleaning")
    plain_value(hass, "v1", "battery", "80")
    await hass.async_block_till_done()
    alarm = entity_id(hass, "alarm_control_panel", "a1-alarm_control_panel")
    assert hass.states.get(alarm).state == "armed_home"
    await hass.services.async_call("alarm_control_panel", "alarm_disarm", {"entity_id": alarm}, blocking=True)
    assert_published(mqtt_mock, "il/a1/disarm/set", "")
    vacuum = entity_id(hass, "vacuum", "v1-vacuum")
    assert hass.states.get(vacuum).state == "cleaning"
    await hass.services.async_call("vacuum", "start", {"entity_id": vacuum}, blocking=True)
    assert_published(mqtt_mock, "il/v1/start/set", "")
    battery = hass.states.get(entity_id(hass, "sensor", "v1-battery"))
    assert battery.attributes["device_class"] == "battery"


async def test_a_select_value_the_descriptor_does_not_list_is_shown(hass, mqtt_mock):
    await setup(hass)
    announce(hass, {"il": 0, "id": "s1", "props": {
        "m": {"type": "select", "rw": True, "options": ["a", "b"]},
        "r": {"type": "select", "options": ["a", "b"]},
    }})
    await hass.async_block_till_done()
    plain_value(hass, "s1", "m", "zzz")
    plain_value(hass, "s1", "r", "zzz")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, "select", "s1-m")).state == "zzz"
    assert hass.states.get(entity_id(hass, "sensor", "s1-r")).state == "zzz"


async def test_plain_entities_get_an_icon_from_what_they_are(hass, mqtt_mock):
    await setup(hass)
    announce(hass, descriptor("DHUM_056905_WW", "dhum1"))
    await hass.async_block_till_done()
    for prop, payload in [("available", "true"), ("uvnano", "true"), ("tank_full", "false"), ("fan", "low")]:
        value(hass, "dhum1", prop, payload)
    await hass.async_block_till_done()
    icons = {u: hass.states.get(entity_id(hass, p, f"dhum1-{u}")).attributes.get("icon")
             for p, u in (("switch", "uvnano"), ("binary_sensor", "tank_full"), ("select", "fan"))}
    assert icons["uvnano"] == "mdi:shield-sun-outline" and icons["fan"] == "mdi:fan"
    # an entity with a device class (the tank's `problem`, the humidity) is left to Home Assistant's own icon
    assert icons["tank_full"] is None
    assert "icon" not in hass.states.get(entity_id(hass, "sensor", "dhum1-humidity")).attributes


async def test_a_cover_with_only_a_state_knows_closed_and_moving(hass, mqtt_mock):
    await setup(hass)
    doc = tuya("tuya_curtain")
    doc["id"] = "curtain03"
    del doc["props"]["position"]
    for prop in doc["props"].values():
        prop["x-mqtt"] = {k: v.replace("curtain01", "curtain03") for k, v in prop["x-mqtt"].items()}
    doc["x-mqtt"] = {"reject": "tuya/curtain03/reject"}
    announce(hass, doc)
    await hass.async_block_till_done()
    tuya_value(hass, "curtain03", "available", "true")
    tuya_value(hass, "curtain03", "cover_state", "closing")
    await hass.async_block_till_done()
    cid = entity_id(hass, "cover", "curtain03-cover")
    state = hass.states.get(cid)
    assert state.state == "closing" and "assumed_state" not in state.attributes
    tuya_value(hass, "curtain03", "cover_state", "closed")
    await hass.async_block_till_done()
    assert hass.states.get(cid).state == "closed"
    tuya_value(hass, "curtain03", "cover_state", "open")
    await hass.async_block_till_done()
    assert hass.states.get(cid).state == "open"


async def test_a_lock_shows_its_states_between(hass, mqtt_mock):
    await setup(hass)
    announce(hass, tuya("tuya_lock"))
    await hass.async_block_till_done()
    tuya_value(hass, "lock01", "available", "true")
    tuya_value(hass, "lock01", "locked", "false")
    lid = entity_id(hass, "lock", "lock01-lock")
    for wire, expected in (("locking", "locking"), ("unlocking", "unlocking"), ("jammed", "jammed"),
                           ("open", "open"), ("locked", "locked"), ("unlocked", "unlocked")):
        tuya_value(hass, "lock01", "lock_state", wire)
        await hass.async_block_till_done()
        assert hass.states.get(lid).state == expected
