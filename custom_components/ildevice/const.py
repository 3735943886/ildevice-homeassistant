"""Constants of the IL integration."""

DOMAIN = "ildevice"

CONF_IL_PREFIX = "il_prefix"
CONF_OFFLINE_GRACE = "offline_grace"
CONF_ALIASES = "aliases"
CONF_AUTO_ADD = "auto_add"
CONF_DEVICES = "devices"  # ids of the devices the user chose to add

DEFAULT_IL_PREFIX = "il"
DEFAULT_OFFLINE_GRACE = 0  # seconds a device may report unavailable before entities say so

EVENT_COMMAND_REJECTED = f"{DOMAIN}_command_rejected"

PLATFORMS = [
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "climate",
    "cover",
    "event",
    "fan",
    "humidifier",
    "light",
    "lock",
    "number",
    "select",
    "sensor",
    "siren",
    "switch",
    "text",
    "vacuum",
    "valve",
]
