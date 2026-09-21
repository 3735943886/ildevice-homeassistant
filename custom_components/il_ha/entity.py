"""Entities: one class per platform, all reading the hub's values and writing to the device's set topics."""

from __future__ import annotations

from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.components.vacuum import StateVacuumEntity, VacuumActivity, VacuumEntityFeature
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.components.valve import ValveEntity, ValveEntityFeature
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.components.text import TextEntity
from homeassistant.const import ATTR_TEMPERATURE, EntityCategory, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util.color import color_hs_to_RGB, color_RGB_to_hs
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .const import DOMAIN
from .core import EntitySpec, encode_command
from .core.topics import set_topic
from .hub import Device, IlHub, event_signal, signal


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
        if not self.dev.online or not self.hub.source_up(self.dev):
            return False
        req = self.spec.requires
        return req is None or req.met(self.dev.values)

    # ---- writing ---------------------------------------------------------------------

    async def _write(self, slot: str, value: Any = None) -> None:
        prop = self._prop(slot)
        if prop is None:
            return
        desc = self.dev.desc
        # a control whose condition does not hold now is not written (il.md S-2)
        requires = desc.props[prop].requires
        if requires is not None and not requires.met(self.dev.values):
            raise ServiceValidationError(f"{prop} needs {requires.prop} first")
        await mqtt.async_publish(
            self.hass,
            set_topic(desc, self.dev.topics, prop),
            encode_command(desc.props[prop], value),
            qos=1,
        )


# ---- plain entities -------------------------------------------------------------------


class IlSensor(IlEntity, SensorEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(SensorDeviceClass, spec.device_class)
        self._attr_state_class = _enum(SensorStateClass, spec.state_class)
        self._attr_native_unit_of_measurement = spec.unit
        if self._attr_device_class == SensorDeviceClass.ENUM:
            self._attr_options = list(spec.options)

    @property
    def options(self) -> list[str] | None:
        # a value the descriptor does not list is shown as it is (il.md V-3)
        value = self._v()
        if self._attr_device_class != SensorDeviceClass.ENUM:
            return None
        return list(self.spec.options) + ([value] if value is not None and value not in self.spec.options else [])

    @property
    def native_value(self) -> Any:
        value = self._v()
        if self._attr_device_class == SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(value) if isinstance(value, str) else None
        return value


class IlBinarySensor(IlEntity, BinarySensorEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(BinarySensorDeviceClass, spec.device_class)

    @property
    def is_on(self) -> bool | None:
        return self._v()


class IlSwitch(IlEntity, SwitchEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(SwitchDeviceClass, spec.device_class)

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

    @property
    def options(self) -> list[str]:
        value = self._v()
        return list(self.spec.options) + ([value] if value is not None and value not in self.spec.options else [])

    @property
    def current_option(self) -> str | None:
        return self._v()

    async def async_select_option(self, option: str) -> None:
        await self._write("value", option)


class IlText(IlEntity, TextEntity):
    @property
    def native_value(self) -> str | None:
        return self._v()

    async def async_set_value(self, value: str) -> None:
        await self._write("value", value)


class IlButton(IlEntity, ButtonEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = _enum(ButtonDeviceClass, spec.device_class)

    async def async_press(self) -> None:
        await self._write("value")


class IlEvent(IlEntity, EventEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_event_types = list(spec.options)
        self._attr_device_class = _enum(EventDeviceClass, spec.device_class)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, event_signal(self.dev.desc.id, self.spec.key), self._occurred
            )
        )

    @callback
    def _occurred(self, kind: str) -> None:
        if kind in self._attr_event_types:
            self._trigger_event(kind)
            self.async_write_ha_state()


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


class IlLight(IlEntity, LightEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        props = dev.desc.props
        modes: set[ColorMode] = set()
        if "color" in spec.slots:
            modes.add(ColorMode.HS)
        if "color_temperature" in spec.slots:
            modes.add(ColorMode.COLOR_TEMP)
            ct = props[spec.slots["color_temperature"]]
            if ct.min is not None:
                self._attr_min_color_temp_kelvin = int(ct.min)
            if ct.max is not None:
                self._attr_max_color_temp_kelvin = int(ct.max)
        if not modes:
            modes.add(ColorMode.BRIGHTNESS if "brightness" in spec.slots else ColorMode.ONOFF)
        self._attr_supported_color_modes = modes
        self._lowest = 1
        if "brightness" in spec.slots and props[spec.slots["brightness"]].min:
            self._lowest = max(1, round(props[spec.slots["brightness"]].min))

    @property
    def is_on(self) -> bool | None:
        return self._v("on")

    @property
    def brightness(self) -> int | None:
        value = self._v("brightness")
        return None if value is None else round(value * 255 / 100)

    @property
    def color_mode(self) -> ColorMode:
        modes = self._attr_supported_color_modes
        if len(modes) == 1:
            return next(iter(modes))
        return ColorMode.HS if self._v("color_mode") == "color" else ColorMode.COLOR_TEMP

    @property
    def color_temp_kelvin(self) -> int | None:
        value = self._v("color_temperature")
        return None if value is None else round(value)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        text = self._v("color")
        try:
            rgb = tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))
        except (TypeError, ValueError):
            return None
        if not text.startswith("#") or len(text) != 7:
            return None
        return color_RGB_to_hs(*rgb)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write("on", True)
        if ATTR_HS_COLOR in kwargs and "color" in self.spec.slots:
            r, g, b = color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
            await self._write("color", f"#{r:02x}{g:02x}{b:02x}")
        if ATTR_COLOR_TEMP_KELVIN in kwargs and "color_temperature" in self.spec.slots:
            await self._write("color_temperature", kwargs[ATTR_COLOR_TEMP_KELVIN])
        if ATTR_BRIGHTNESS in kwargs and "brightness" in self.spec.slots:
            percent = round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)
            await self._write("brightness", max(self._lowest, min(100, percent)))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write("on", False)


class IlCover(IlEntity, CoverEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        props = dev.desc.props
        self._attr_device_class = _enum(CoverDeviceClass, spec.device_class)
        position = props[spec.slots["position"]] if "position" in spec.slots else None
        features = CoverEntityFeature(0)
        if "open" in spec.slots or (position and position.writable):
            features |= CoverEntityFeature.OPEN
        if "close" in spec.slots or (position and position.writable):
            features |= CoverEntityFeature.CLOSE
        if "stop" in spec.slots:
            features |= CoverEntityFeature.STOP
        if position and position.writable:
            features |= CoverEntityFeature.SET_POSITION
        if "tilt" in spec.slots and props[spec.slots["tilt"]].writable:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features
        # without a position the cover never says where it is
        self._attr_assumed_state = position is None

    @property
    def current_cover_position(self) -> int | None:
        return self._v("position")

    @property
    def current_cover_tilt_position(self) -> int | None:
        return self._v("tilt")

    @property
    def is_closed(self) -> bool | None:
        position = self._v("position")
        return None if position is None else position == 0

    @property
    def is_opening(self) -> bool | None:
        motion = self._v("motion")
        return None if motion is None else motion == "opening"

    @property
    def is_closing(self) -> bool | None:
        motion = self._v("motion")
        return None if motion is None else motion == "closing"

    async def async_open_cover(self, **kwargs: Any) -> None:
        if "open" in self.spec.slots:
            await self._write("open")
        else:
            await self._write("position", 100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        if "close" in self.spec.slots:
            await self._write("close")
        else:
            await self._write("position", 0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._write("stop")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._write("position", kwargs[ATTR_POSITION])

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        await self._write("tilt", kwargs[ATTR_TILT_POSITION])


class IlLock(IlEntity, LockEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        if "unlatch" in spec.slots:
            self._attr_supported_features = LockEntityFeature.OPEN

    @property
    def is_locked(self) -> bool | None:
        return self._v("locked")

    async def async_lock(self, **kwargs: Any) -> None:
        await self._write("locked", True)

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._write("locked", False)

    async def async_open(self, **kwargs: Any) -> None:
        await self._write("unlatch")


class IlSiren(IlEntity, SirenEntity):
    _attr_supported_features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF

    @property
    def is_on(self) -> bool | None:
        return self._v("on")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write("on", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write("on", False)


class IlValve(IlEntity, ValveEntity):
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE

    @property
    def is_closed(self) -> bool | None:
        opened = self._v("opened")
        return None if opened is None else not opened

    async def async_open_valve(self, **kwargs: Any) -> None:
        await self._write("opened", True)

    async def async_close_valve(self, **kwargs: Any) -> None:
        await self._write("opened", False)


_ALARM_STATES = {s.value for s in AlarmControlPanelState}
_ALARM_FEATURES = {
    "arm_home": AlarmControlPanelEntityFeature.ARM_HOME,
    "arm_away": AlarmControlPanelEntityFeature.ARM_AWAY,
    "arm_night": AlarmControlPanelEntityFeature.ARM_NIGHT,
}


class IlAlarmControlPanel(IlEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        features = AlarmControlPanelEntityFeature(0)
        for role, feature in _ALARM_FEATURES.items():
            if role in spec.slots:
                features |= feature
        self._attr_supported_features = features

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        return _enum(AlarmControlPanelState, self._v("alarm_state"))

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._write("disarm")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._write("arm_home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._write("arm_away")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._write("arm_night")


class IlVacuum(IlEntity, StateVacuumEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        features = VacuumEntityFeature.STATE
        for role, feature in (
            ("start", VacuumEntityFeature.START),
            ("pause", VacuumEntityFeature.PAUSE),
            ("return_home", VacuumEntityFeature.RETURN_HOME),
            ("locate", VacuumEntityFeature.LOCATE),
        ):
            if role in spec.slots:
                features |= feature
        if "fan_speed" in spec.slots:
            features |= VacuumEntityFeature.FAN_SPEED
            self._attr_fan_speed_list = list(dev.desc.props[spec.slots["fan_speed"]].options)
        self._attr_supported_features = features

    @property
    def activity(self) -> VacuumActivity | None:
        return _enum(VacuumActivity, self._v("vacuum_state"))

    @property
    def fan_speed(self) -> str | None:
        return self._v("fan_speed")

    async def async_start(self) -> None:
        await self._write("start")

    async def async_pause(self) -> None:
        await self._write("pause")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self._write("return_home")

    async def async_locate(self, **kwargs: Any) -> None:
        await self._write("locate")

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        await self._write("fan_speed", fan_speed)


ENTITY_CLASSES = {
    "alarm_control_panel": IlAlarmControlPanel,
    "vacuum": IlVacuum,
    "sensor": IlSensor,
    "binary_sensor": IlBinarySensor,
    "switch": IlSwitch,
    "number": IlNumber,
    "select": IlSelect,
    "text": IlText,
    "button": IlButton,
    "event": IlEvent,
    "climate": IlClimate,
    "humidifier": IlHumidifier,
    "fan": IlFan,
    "light": IlLight,
    "cover": IlCover,
    "lock": IlLock,
    "siren": IlSiren,
    "valve": IlValve,
}
