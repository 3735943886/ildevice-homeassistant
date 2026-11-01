"""A fitting Material Design icon for an entity that Home Assistant would otherwise show with its generic one.

An entity with a device class (a temperature, a door) already has a good icon of its own, so this returns nothing for
it. For the rest the name of the property says what it is (`child_lock`, `motion_sensitivity`, `filter_reset`); the
first rule that matches wins, then the unit, then the kind of entity. Nothing here imports Home Assistant, so another
consumer can use the same names.
"""

from __future__ import annotations

import re

# (pattern on the property name, icon). Ordered: the specific before the general.
_BY_NAME: list[tuple[str, str]] = [
    # locks, safety, alarms
    (r"child_?lock|kid_?lock", "mdi:human-child"),
    (r"^lock|_lock$|lock_", "mdi:lock-outline"),
    (r"mute|muffl|silen", "mdi:volume-off"),
    (r"snooze", "mdi:alarm-snooze"),
    (r"alarm.*(vol|sound)|(vol|sound).*alarm", "mdi:bell-ring"),
    (r"alarm.*light|siren.*light", "mdi:alarm-light-outline"),
    (r"alarm|siren", "mdi:bell-ring-outline"),
    (r"doorbell", "mdi:doorbell"),
    (r"anti_?theft|tamper", "mdi:shield-alert-outline"),
    (r"disturb|dnd|quiet", "mdi:sleep"),
    # camera / video
    (r"motion.*(sens|level)|sens.*motion", "mdi:tune-variant"),
    (r"motion.*track|track", "mdi:target-account"),
    (r"motion.*area|area.*motion", "mdi:vector-rectangle"),
    (r"motion|pir|move", "mdi:motion-sensor"),
    (r"record.*mode|mode.*record", "mdi:record-circle-outline"),
    (r"record|rec_", "mdi:record-rec"),
    (r"flip|mirror", "mdi:flip-vertical"),
    (r"osd|watermark|timestamp", "mdi:subtitles-outline"),
    (r"night_?vision|ir_?cut|infrared|ir_?mode", "mdi:weather-night"),
    (r"wdr", "mdi:brightness-6"),
    (r"anti_?flicker|flicker", "mdi:sine-wave"),
    (r"private|privacy", "mdi:eye-off-outline"),
    (r"decibel.*sens|sound.*sens", "mdi:tune-variant"),
    (r"decibel|noise|sound_?detect", "mdi:microphone"),
    (r"sharp", "mdi:blur-off"),
    (r"contrast", "mdi:contrast-box"),
    (r"ipc_bright|bright", "mdi:brightness-6"),
    (r"pic|photo|snapshot|image", "mdi:image-outline"),
    (r"zoom|ptz|focus", "mdi:cctv"),
    (r"ipc_auto_siren", "mdi:alarm-light-outline"),
    # power, energy, batteries
    (r"relay", "mdi:electric-switch"),
    (r"power_?factor", "mdi:angle-acute"),
    (r"(save|eco).*energy|energy.*sav|^eco", "mdi:leaf"),
    (r"wireless.*(electric|batt)", "mdi:battery-wireless"),
    (r"batt|electricity_left|charge_?(level|percent)|va_battery", "mdi:battery-medium"),
    (r"charg", "mdi:battery-charging"),
    (r"backup|reserve", "mdi:battery-heart-outline"),
    (r"usb", "mdi:usb-port"),
    (r"overload|over_?curr|leak(age)?_?curr", "mdi:flash-alert-outline"),
    (r"voltage", "mdi:sine-wave"),
    (r"electric.?curr|(^|_)amp(s|ere)?(_|$)|phase.*curr|^current$", "mdi:current-ac"),
    (r"power|watt|consum", "mdi:flash-outline"),
    # time
    (r"weather", "mdi:weather-cloudy-clock"),
    (r"countdown|timer|delay|schedule|cycle", "mdi:timer-outline"),
    (r"time_?(total|use)|(total|run|work|use).*time|runtime", "mdi:timer-sand"),
    (r"remain|left_?time|time_?left|cook_?time", "mdi:timer-sand"),
    (r"clean_?time|pump_?time", "mdi:timer-outline"),
    # air, water, environment
    (r"anion|(^|_)ion(izer)?(_|$)|negative", "mdi:atom"),
    (r"uv_?index", "mdi:sun-wireless-outline"),
    (r"(^|_)uv|steril|disinfect", "mdi:shield-sun-outline"),
    (r"pm_?(1|2|10|25)|(^|_)pm(_|$)|(^|_)dust(_|$)|particul", "mdi:blur"),
    (r"ch2o|hcho|formaldehyde", "mdi:molecule"),
    (r"co2|carbon", "mdi:molecule-co2"),
    (r"(^|_)gas(_|$)", "mdi:gas-cylinder"),
    (r"voc|tvoc|air_?quality", "mdi:air-filter"),
    (r"filter.*(life|remain|left)", "mdi:air-filter"),
    (r"filter.*reset|reset.*filter", "mdi:restore"),
    (r"filter", "mdi:air-filter"),
    (r"tds", "mdi:water-check"),
    (r"orp", "mdi:beaker-outline"),
    (r"ph$|_ph|ph_", "mdi:ph"),
    (r"chlorine|cl_", "mdi:water-opacity"),
    (r"liquid_?level|water_?level|level.*water|tank|cistern", "mdi:cup-water"),
    (r"defrost", "mdi:snowflake-melt"),
    (r"frost|freez|snow", "mdi:snowflake"),
    (r"pressure|pressture|baro", "mdi:gauge"),
    (r"rain|precip", "mdi:weather-rainy"),
    (r"wind.*dir|dir.*wind|wind_?direct", "mdi:compass-outline"),
    (r"wind|speed", "mdi:weather-windy"),
    (r"humid|dehumid|moist|wet", "mdi:water-percent"),
    (r"temp.*(correct|calib|offset|adjust)", "mdi:thermometer-plus"),
    (r"temp|thermo|degree", "mdi:thermometer"),
    (r"heat", "mdi:radiator"),
    (r"cool|compressor", "mdi:snowflake-thermometer"),
    (r"defog|demist", "mdi:car-defrost-front"),
    (r"light|lamp|led|backlight|indicator", "mdi:led-on"),
    (r"lux|illum", "mdi:brightness-5"),
    (r"door", "mdi:door"),
    (r"window", "mdi:window-closed-variant"),
    (r"smoke|fire", "mdi:smoke-detector-variant"),
    (r"water_?leak|flood|immers", "mdi:water-alert-outline"),
    # modes, controls
    (r"control_?back|reverse|invert|direction_?ctrl", "mdi:swap-horizontal"),
    (r"up_down|updown", "mdi:swap-vertical"),
    (r"direct|dir$", "mdi:compass-outline"),
    (r"swing|oscill", "mdi:rotate-3d-variant"),
    (r"sleep|night", "mdi:sleep"),
    (r"(^|_)kb(_|$)|keypad|keyboard", "mdi:keyboard-outline"),
    (r"beep|buzz|tone|voice|prompt", "mdi:volume-high"),
    (r"volume|vol$|_vol", "mdi:volume-medium"),
    (r"music|radio|song", "mdi:music"),
    (r"preset|scene|profile", "mdi:playlist-star"),
    (r"level|gear|strength|intens", "mdi:stairs"),
    (r"sens(itiv)?", "mdi:tune-variant"),
    (r"mode|type|work", "mdi:tune"),
    (r"auto", "mdi:autorenew"),
    (r"factory", "mdi:factory"),
    (r"reset|restore", "mdi:restore"),
    (r"^stop|_stop|pause", "mdi:stop-circle-outline"),
    (r"^start|_start|resume|^run", "mdi:play-circle-outline"),
    (r"return|dock|home_?base", "mdi:home-import-outline"),
    (r"locate|find|seek", "mdi:map-marker-radius-outline"),
    (r"update|upgrade|firmware|(^|_)ota(_|$)", "mdi:update"),
    (r"restart|reboot", "mdi:restart"),
    (r"calib", "mdi:tune"),
    (r"test|check|self", "mdi:clipboard-check-outline"),
    # appliances, pets, garden
    (r"(^|_)feed(_|$)|feeder|manual_feed", "mdi:food-drumstick-outline"),
    (r"excret|litter|poop|toilet", "mdi:cat"),
    (r"pump", "mdi:pump"),
    (r"valve", "mdi:valve"),
    (r"irrigat|sprinkl|water_?(set|time)", "mdi:sprinkler-variant"),
    (r"brush", "mdi:brush"),
    (r"mop|cloth|duster", "mdi:spray-bottle"),
    (r"clean|sweep|vacuum|broom", "mdi:broom"),
    (r"map", "mdi:map-outline"),
    (r"area", "mdi:texture-box"),
    (r"cook|oven|stove|grill|fry|steam", "mdi:pot-steam-outline"),
    (r"wash|laundry|spin|rinse|dry(er)?", "mdi:washing-machine"),
    (r"(^|_)max(_|$)", "mdi:arrow-collapse-up"),
    (r"(^|_)mini?(_|$)", "mdi:arrow-collapse-down"),
    (r"manual", "mdi:hand-back-right-outline"),
    (r"fan", "mdi:fan"),
    (r"count|times|number|total", "mdi:counter"),
    (r"fault|error|problem|alert|warn", "mdi:alert-circle-outline"),
    (r"status|state", "mdi:information-outline"),
    (r"sensor", "mdi:eye-outline"),
    (r"switch|enable|open|on_?off", "mdi:toggle-switch-outline"),
]
_COMPILED = [(re.compile(p), icon) for p, icon in _BY_NAME]

_BY_UNIT = {
    "%": "mdi:percent-outline", "°C": "mdi:thermometer", "K": "mdi:thermometer", "W": "mdi:flash-outline",
    "Wh": "mdi:lightning-bolt", "kWh": "mdi:lightning-bolt", "V": "mdi:sine-wave", "A": "mdi:current-ac",
    "Hz": "mdi:sine-wave", "s": "mdi:timer-outline", "min": "mdi:timer-outline", "h": "mdi:timer-outline",
    "mL": "mdi:water-outline", "L": "mdi:water-outline", "m³": "mdi:cube-outline", "μg/m³": "mdi:blur",
    "ppm": "mdi:molecule", "lx": "mdi:brightness-5", "Pa": "mdi:gauge", "hPa": "mdi:gauge",
    "m/s": "mdi:weather-windy", "km/h": "mdi:weather-windy", "dB": "mdi:volume-high",
}

# what a kind of entity with nothing else to go on looks like (Home Assistant's own is a bare toggle, an eye, ...)
_BY_PLATFORM = {
    "switch": "mdi:toggle-switch-outline",
    "binary_sensor": "mdi:checkbox-marked-circle-outline",
    "sensor": "mdi:gauge-empty",
    "number": "mdi:numeric",
    "select": "mdi:format-list-bulleted-type",
    "text": "mdi:form-textbox",
    "button": "mdi:gesture-tap-button",
    "event": "mdi:gesture-tap",
}

_NO_ICON_NEEDED = {None, "enum"}          # a device class other than these brings its own icon


def icon_for(platform: str, key: str, unit: str | None = None, device_class: str | None = None) -> str | None:
    """`key` is the property name (an entity key); returns `mdi:...` or None to leave Home Assistant's own."""
    if device_class not in _NO_ICON_NEEDED:
        return None
    name = re.sub(r"\d+$", "", key.lower()).strip("_")
    for pattern, icon in _COMPILED:
        if pattern.search(name):
            return icon
    return _BY_UNIT.get(unit or "") or _BY_PLATFORM.get(platform)
