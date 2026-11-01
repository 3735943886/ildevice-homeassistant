"""Constants of the IL integration."""

from .core.topics import DEFAULT_IL_PREFIX

DOMAIN = "ildevice"

CONF_IL_PREFIX = "il_prefix"
CONF_OFFLINE_GRACE = "offline_grace"
CONF_ALIASES = "aliases"
CONF_AUTO_ADD = "auto_add"
CONF_DEVICES = "devices"  # ids of the devices the user chose to add

DEFAULT_OFFLINE_GRACE = 0  # seconds a device may report unavailable before entities say so
MAX_OFFLINE_GRACE = 3600

DEFAULT_OPTIONS = {
    CONF_IL_PREFIX: DEFAULT_IL_PREFIX,
    CONF_OFFLINE_GRACE: DEFAULT_OFFLINE_GRACE,
    CONF_ALIASES: "",
    CONF_AUTO_ADD: False,
    CONF_DEVICES: (),
}

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


def with_defaults(options) -> dict:
    """An entry's options, each one it lacks at its default."""
    return {**DEFAULT_OPTIONS, **options}
