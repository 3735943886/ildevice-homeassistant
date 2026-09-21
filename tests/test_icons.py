"""Icons for entities without a device class of their own."""

import pytest

from custom_components.il_ha.core import parse_descriptor, plan_entities
from custom_components.il_ha.core.icons import icon_for


@pytest.mark.parametrize("platform,key,unit,icon", [
    ("switch", "child_lock", None, "mdi:human-child"),
    ("select", "motion_sensitivity", None, "mdi:tune-variant"),
    ("switch", "motion_switch", None, "mdi:motion-sensor"),
    ("sensor", "temp_current", "°C", "mdi:thermometer"),          # a current temperature is not an electric current
    ("sensor", "humidity_current", "%", "mdi:water-percent"),
    ("number", "temp_correction", "℃", "mdi:thermometer-plus"),   # `correction` does not end in an ion
    ("button", "factory_reset", None, "mdi:factory"),
    ("button", "reset_filter", None, "mdi:restore"),
    ("sensor", "total_clean_area", "㎡", "mdi:broom"),             # `total` does not contain an OTA
    ("sensor", "uv_index", None, "mdi:sun-wireless-outline"),
    ("switch", "feedin_power_limit_enable", None, "mdi:flash-outline"),
    ("sensor", "something_unheard_of", "W", "mdi:flash-outline"),  # the unit, when the name says nothing
    ("select", "zzz", None, "mdi:format-list-bulleted-type"),      # the kind of entity, when nothing else does
    ("switch", "relay_status3", None, "mdi:electric-switch"),
])
def test_the_name_then_the_unit_then_the_kind_decides(platform, key, unit, icon):
    assert icon_for(platform, key, unit) == icon


def test_an_entity_with_a_device_class_keeps_the_icon_home_assistant_gives_it():
    assert icon_for("sensor", "temperature", "°C", "temperature") is None
    assert icon_for("binary_sensor", "door", None, "door") is None
    assert icon_for("sensor", "state", None, "enum") == "mdi:information-outline"     # an enum sensor has no icon of its own


def test_plain_entities_of_a_plan_carry_the_icon_and_composites_do_not():
    desc = parse_descriptor({"il": 0, "id": "d", "kind": "light", "props": {
        "power": {"type": "binary", "rw": True, "role": "on"},
        "child_lock": {"type": "binary", "rw": True, "category": "config"},
        "temp": {"type": "number", "class": "temperature", "unit": "°C"}}})
    icons = {s.key: s.icon for s in plan_entities(desc)}
    assert icons == {"light": None, "child_lock": "mdi:human-child", "temp": None}
