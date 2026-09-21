"""The alarm_control_panel platform: entities are created by the hub as descriptors arrive."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hass.data[DOMAIN][entry.entry_id].register_platform("alarm_control_panel", async_add_entities)
