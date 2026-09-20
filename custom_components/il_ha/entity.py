"""Entities: one class per platform, all reading the hub's values and writing to the device's set topics."""

from __future__ import annotations

from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.text import TextEntity
from homeassistant.const import ATTR_TEMPERATURE, EntityCategory, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .const import DOMAIN
from .core import EntitySpec, encode_command
from .core.topics import set_topic
from .hub import Device, IlHub, signal


def _enum(cls, value):
    """`value` as a member of the enum `cls`, or `None` when it is not one (a class the
    producer names that Home Assistant does not know is simply not applied)."""
    try:
        return cls(value) if value else None
    except ValueError:
        return None


class IlEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: IlHub, dev: Device, spec: EntitySpec) -> None:
        self.hub = hub
        self.dev = dev
        self.spec = spec
        desc = dev.desc
        self._attr_unique_id = spec.unique_id
        self._attr_name = spec.name
        self._attr_entity_category = _enum(EntityCategory, spec.entity_category)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, desc.id)},
            name=desc.label or desc.model or desc.id,
            manufacturer=desc.vendor,
            model=desc.model,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal(self.dev.desc.id), self._changed)
        )

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    # ---- reading ---------------------------------------------------------------------

    def _prop(self, slot: str) -> str | None:
        return self.spec.slots.get(slot) or self.spec.shared.get(slot)

    def _v(self, slot: str = "value") -> Any:
        prop = self._prop(slot)
        return self.dev.values.get(prop) if prop else None

    @property
    def available(self) -> bool:
        if not self.dev.online:
            return False
        req = self.spec.requires
        return req is None or self.dev.values.get(req) is True

    # ---- writing ---------------------------------------------------------------------

    async def _write(self, slot: str, value: Any = None) -> None:
        prop = self._prop(slot)
        if prop is None:
            return
        desc = self.dev.desc
        await mqtt.async_publish(
            self.hass,
            set_topic(desc, self.dev.topics, prop),
            encode_command(desc.props[prop], value),
        )


# ---- plain entities -------------------------------------------------------------------


class IlSensor(IlEntity, SensorEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(SensorDeviceClass, spec.device_class)
        self._attr_state_class = _enum(SensorStateClass, spec.state_class)
        self._attr_native_unit_of_measurement = spec.unit

    @property
    def native_value(self) -> Any:
        return self._v()


class IlBinarySensor(IlEntity, BinarySensorEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(BinarySensorDeviceClass, spec.device_class)

    @property
    def is_on(self) -> bool | None:
        return self._v()


class IlSwitch(IlEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        return self._v()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write("value", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write("value", False)


class IlNumber(IlEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(NumberDeviceClass, spec.device_class)
        self._attr_native_unit_of_measurement = spec.unit
        if spec.min is not None:
            self._attr_native_min_value = spec.min
        if spec.max is not None:
            self._attr_native_max_value = spec.max
        if spec.step is not None:
            self._attr_native_step = spec.step

    @property
    def native_value(self) -> float | None:
        return self._v()

    async def async_set_native_value(self, value: float) -> None:
        await self._write("value", value)


class IlSelect(IlEntity, SelectEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_options = list(spec.options)

    @property
    def current_option(self) -> str | None:
        value = self._v()
        return value if value in self.spec.options else None

    async def async_select_option(self, option: str) -> None:
        await self._write("value", option)


class IlText(IlEntity, TextEntity):
    @property
    def native_value(self) -> str | None:
        return self._v()

    async def async_set_value(self, value: str) -> None:
        await self._write("value", value)


class IlButton(IlEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self._write("value")


# ---- composites -----------------------------------------------------------------------

_HVAC_MODES = {m.value for m in HVACMode}
_HVAC_ACTIONS = {a.value: a for a in HVACAction}


class IlClimate(IlEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        props = dev.desc.props
        modes = [o for o in props[spec.slots["mode"]].options if o in _HVAC_MODES] if "mode" in spec.slots else []
        self._modes = modes
        self._attr_hvac_modes = ([HVACMode.OFF] if "on" in spec.slots else []) + [HVACMode(m) for m in modes]
        features = ClimateEntityFeature(0)
        if "on" in spec.slots:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if "target_temperature" in spec.slots:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if "fan_speed" in spec.slots:
            features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = list(props[spec.slots["fan_speed"]].options)
        if "swing_vertical" in spec.slots:
            features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = ["on", "off"]
        if "swing_horizontal" in spec.slots:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
            self._attr_swing_horizontal_modes = ["on", "off"]
        self._attr_supported_features = features
        if spec.min is not None:
            self._attr_min_temp = spec.min
        if spec.max is not None:
            self._attr_max_temp = spec.max
        if spec.step is not None:
            self._attr_target_temperature_step = spec.step
            self._attr_precision = spec.step if spec.step < 1 else 1

    @property
    def hvac_mode(self) -> HVACMode | None:
        if "on" in self.spec.slots and self._v("on") is False:
            return HVACMode.OFF
        mode = self._v("mode")
        return HVACMode(mode) if mode in self._modes else None

    @property
    def hvac_action(self) -> HVACAction | None:
        return _HVAC_ACTIONS.get(self._v("action"))

    @property
    def current_temperature(self) -> float | None:
        return self._v("current_temperature")

    @property
    def target_temperature(self) -> float | None:
        return self._v("target_temperature")

    @property
    def current_humidity(self) -> float | None:
        return self._v("current_humidity")

    @property
    def fan_mode(self) -> str | None:
        return self._v("fan_speed")

    @property
    def swing_mode(self) -> str | None:
        value = self._v("swing_vertical")
        return None if value is None else ("on" if value else "off")

    @property
    def swing_horizontal_mode(self) -> str | None:
        value = self._v("swing_horizontal")
        return None if value is None else ("on" if value else "off")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._write("on", False)
            return
        if "on" in self.spec.slots and self._v("on") is not True:
            await self._write("on", True)
        await self._write("mode", hvac_mode.value)

    async def async_turn_on(self) -> None:
        await self._write("on", True)

    async def async_turn_off(self) -> None:
        await self._write("on", False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_HVAC_MODE in kwargs:
            await self.async_set_hvac_mode(kwargs[ATTR_HVAC_MODE])
        if ATTR_TEMPERATURE in kwargs:
            await self._write("target_temperature", kwargs[ATTR_TEMPERATURE])

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self._write("fan_speed", fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self._write("swing_vertical", swing_mode == "on")

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        await self._write("swing_horizontal", swing_horizontal_mode == "on")


class IlHumidifier(IlEntity, HumidifierEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(HumidifierDeviceClass, spec.device_class) or HumidifierDeviceClass.HUMIDIFIER
        if "mode" in spec.slots:
            self._attr_supported_features = HumidifierEntityFeature.MODES
            self._attr_available_modes = list(dev.desc.props[spec.slots["mode"]].options)
        if spec.min is not None:
            self._attr_min_humidity = spec.min
        if spec.max is not None:
            self._attr_max_humidity = spec.max

    @property
    def is_on(self) -> bool | None:
        return self._v("on")

    @property
    def target_humidity(self) -> float | None:
        return self._v("target_humidity")

    @property
    def current_humidity(self) -> float | None:
        return self._v("current_humidity")

    @property
    def mode(self) -> str | None:
        return self._v("mode")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write("on", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write("on", False)

    async def async_set_humidity(self, humidity: int) -> None:
        await self._write("target_humidity", humidity)

    async def async_set_mode(self, mode: str) -> None:
        await self._write("mode", mode)


class IlFan(IlEntity, FanEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        props = dev.desc.props
        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        self._speeds: list[str] = []
        if "fan_speed" in spec.slots:
            features |= FanEntityFeature.SET_SPEED
            self._speeds = list(props[spec.slots["fan_speed"]].options)
        if "mode" in spec.slots:
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = list(props[spec.slots["mode"]].options)
        self._attr_supported_features = features

    @property
    def speed_count(self) -> int:
        return len(self._speeds) or 1

    @property
    def is_on(self) -> bool | None:
        return self._v("on")

    @property
    def percentage(self) -> int | None:
        value = self._v("fan_speed")
        if not self._speeds or value not in self._speeds:
            return None
        return ordered_list_item_to_percentage(self._speeds, value)

    @property
    def preset_mode(self) -> str | None:
        return self._v("mode")

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        await self._write("on", True)
        if preset_mode is not None:
            await self._write("mode", preset_mode)
        if percentage is not None:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write("on", False)

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self._write("on", False)
        elif self._speeds:
            await self._write("fan_speed", percentage_to_ordered_list_item(self._speeds, percentage))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._write("mode", preset_mode)


ENTITY_CLASSES = {
    "sensor": IlSensor,
    "binary_sensor": IlBinarySensor,
    "switch": IlSwitch,
    "number": IlNumber,
    "select": IlSelect,
    "text": IlText,
    "button": IlButton,
    "climate": IlClimate,
    "humidifier": IlHumidifier,
    "fan": IlFan,
}
