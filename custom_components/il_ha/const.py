"""Constants of the IL integration."""

DOMAIN = "il_ha"

CONF_IL_PREFIX = "il_prefix"
CONF_OFFLINE_GRACE = "offline_grace"
CONF_ALIASES = "aliases"

DEFAULT_IL_PREFIX = "il"
DEFAULT_OFFLINE_GRACE = 0  # seconds a device may report unavailable before entities say so

EVENT_COMMAND_REJECTED = f"{DOMAIN}_command_rejected"

PLATFORMS = [
    "binary_sensor",
    "button",
    "climate",
    "fan",
    "humidifier",
    "number",
    "select",
    "sensor",
    "switch",
    "text",
]
