"""Entities: one class per platform, all reading the hub's values and writing to the device's set topics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import IntFlag, StrEnum
from typing import Any

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
from homeassistant.components.valve import ValveDeviceClass, ValveEntity, ValveEntityFeature
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
from .core import EntitySpec, Prop, encode_command
from .core.plan import VALUE
from .core.topics import set_topic
from .hub import Device, IlHub, event_signal, signal

# a light's brightness is a percentage in the IL and 0..255 in Home Assistant
_HA_BRIGHTNESS = 255
_IL_BRIGHTNESS = 100


def _enum[E: StrEnum](cls: type[E], value: Any) -> E | None:
    """`value` as a member of the enum `cls`, or `None` when it is not one (a class the
    producer names that Home Assistant does not know is simply not applied)."""
    try:
        return cls(value) if value else None
    except ValueError:
        return None


def _is(value: Any, expected: Any) -> bool | None:
    """Whether a reported `value` is `expected`; None while it is not reported."""
    return None if value is None else value == expected


def _on_off(value: bool | None) -> str | None:
    return None if value is None else ("on" if value else "off")


def _with_current(options: Iterable[str], value: Any) -> list[str]:
    """The listed options, and the current value when the descriptor does not list it (il.md V-3)."""
    options = list(options)
    return options + [value] if value is not None and value not in options else options


class IlEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _device_classes: type[StrEnum] | None = None
    """The platform's device class enum: the spec's device class is applied when it is one of its members."""

    def __init__(self, hub: IlHub, dev: Device, spec: EntitySpec) -> None:
        self.hub = hub
        self.dev = dev
        self.spec = spec
        desc = dev.desc
        self._attr_unique_id = spec.unique_id
        self._attr_name = spec.name
        if spec.icon:
            self._attr_icon = spec.icon
        self._attr_entity_category = _enum(EntityCategory, spec.entity_category)
        if self._device_classes is not None:
            self._attr_device_class = _enum(self._device_classes, spec.device_class)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, desc.id)},
            name=desc.display_name,
            manufacturer=desc.vendor,
            model=desc.model,
        )

    def _set_known(self, **attrs: Any) -> None:
        """Set `_attr_<name>` for each attribute the descriptor gives (not None); the rest keep Home Assistant's."""
        for name, value in attrs.items():
            if value is not None:
                setattr(self, f"_attr_{name}", value)

    def _features[F: IntFlag](self, features: F, by_slot: Mapping[str, F]) -> F:
        """`features`, and each feature whose slot the entity has."""
        for slot, feature in by_slot.items():
            if self._has(slot):
                features |= feature
        return features

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal(self.dev.desc.id), self._changed)
        )

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    # ---- reading ---------------------------------------------------------------------

    def _has(self, slot: str) -> bool:
        return slot in self.spec.slots

    def _prop(self, slot: str) -> str | None:
        return self.spec.slots.get(slot) or self.spec.shared.get(slot)

    def _slot_prop(self, slot: str) -> Prop | None:
        """The property of a slot the entity owns."""
        name = self.spec.slots.get(slot)
        return self.dev.desc.props[name] if name else None

    def _options(self, slot: str) -> list[str]:
        prop = self._slot_prop(slot)
        return list(prop.options) if prop else []

    def _v(self, slot: str = VALUE) -> Any:
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
        await self.hub.transport.publish(
            set_topic(desc, self.dev.topics, prop), encode_command(desc.props[prop], value), qos=1
        )


class _Toggle(IlEntity):
    """An entity that is on or off by one binary slot."""

    _toggle_slot = "on"

    @property
    def is_on(self) -> bool | None:
        return self._v(self._toggle_slot)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(self._toggle_slot, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(self._toggle_slot, False)


# ---- plain entities -------------------------------------------------------------------


class IlSensor(IlEntity, SensorEntity):
    _device_classes = SensorDeviceClass

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_state_class = _enum(SensorStateClass, spec.state_class)
        self._attr_native_unit_of_measurement = spec.unit
        if self._attr_device_class == SensorDeviceClass.ENUM:
            self._attr_options = list(spec.options)

    @property
    def options(self) -> list[str] | None:
        if self._attr_device_class != SensorDeviceClass.ENUM:
            return None
        return _with_current(self.spec.options, self._v())

    @property
    def native_value(self) -> Any:
        value = self._v()
        if self._attr_device_class == SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(value) if isinstance(value, str) else None
        return value


class IlBinarySensor(IlEntity, BinarySensorEntity):
    _device_classes = BinarySensorDeviceClass

    @property
    def is_on(self) -> bool | None:
        return self._v()


class IlSwitch(_Toggle, SwitchEntity):
    _device_classes = SwitchDeviceClass
    _toggle_slot = VALUE


class IlNumber(IlEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _device_classes = NumberDeviceClass

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_native_unit_of_measurement = spec.unit
        self._set_known(native_min_value=spec.min, native_max_value=spec.max, native_step=spec.step)

    @property
    def native_value(self) -> float | None:
        return self._v()

    async def async_set_native_value(self, value: float) -> None:
        await self._write(VALUE, value)


class IlSelect(IlEntity, SelectEntity):
    @property
    def options(self) -> list[str]:
        return _with_current(self.spec.options, self._v())

    @property
    def current_option(self) -> str | None:
        return self._v()

    async def async_select_option(self, option: str) -> None:
        await self._write(VALUE, option)


class IlText(IlEntity, TextEntity):
    @property
    def native_value(self) -> str | None:
        return self._v()

    async def async_set_value(self, value: str) -> None:
        await self._write(VALUE, value)


class IlButton(IlEntity, ButtonEntity):
    _device_classes = ButtonDeviceClass

    async def async_press(self) -> None:
        await self._write(VALUE)


class IlEvent(IlEntity, EventEntity):
    _device_classes = EventDeviceClass

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_event_types = list(spec.options)

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
_SWING_MODES = ["on", "off"]


class IlClimate(IlEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._modes = [o for o in self._options("mode") if o in _HVAC_MODES]
        self._attr_hvac_modes = ([HVACMode.OFF] if self._has("on") else []) + [HVACMode(m) for m in self._modes]
        self._attr_supported_features = self._features(ClimateEntityFeature(0), {
            "on": ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF,
            "target_temperature": ClimateEntityFeature.TARGET_TEMPERATURE,
            "fan_speed": ClimateEntityFeature.FAN_MODE,
            "swing_vertical": ClimateEntityFeature.SWING_MODE,
            "swing_horizontal": ClimateEntityFeature.SWING_HORIZONTAL_MODE,
        })
        if self._has("fan_speed"):
            self._attr_fan_modes = self._options("fan_speed")
        if self._has("swing_vertical"):
            self._attr_swing_modes = _SWING_MODES
        if self._has("swing_horizontal"):
            self._attr_swing_horizontal_modes = _SWING_MODES
        self._set_known(min_temp=spec.min, max_temp=spec.max, target_temperature_step=spec.step)
        if spec.step is not None:
            self._attr_precision = min(spec.step, 1)

    @property
    def hvac_mode(self) -> HVACMode | None:
        if self._has("on") and self._v("on") is False:
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
        return _on_off(self._v("swing_vertical"))

    @property
    def swing_horizontal_mode(self) -> str | None:
        return _on_off(self._v("swing_horizontal"))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._write("on", False)
            return
        if self._has("on") and self._v("on") is not True:
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


class IlHumidifier(_Toggle, HumidifierEntity):
    _device_classes = HumidifierDeviceClass

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_device_class = self._attr_device_class or HumidifierDeviceClass.HUMIDIFIER
        if self._has("mode"):
            self._attr_supported_features = HumidifierEntityFeature.MODES
            self._attr_available_modes = self._options("mode")
        self._set_known(min_humidity=spec.min, max_humidity=spec.max)

    @property
    def target_humidity(self) -> float | None:
        return self._v("target_humidity")

    @property
    def current_humidity(self) -> float | None:
        return self._v("current_humidity")

    @property
    def mode(self) -> str | None:
        return self._v("mode")

    async def async_set_humidity(self, humidity: int) -> None:
        await self._write("target_humidity", humidity)

    async def async_set_mode(self, mode: str) -> None:
        await self._write("mode", mode)


class IlFan(_Toggle, FanEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        # a percentage is preferred to named levels (il.md, `speed`)
        self._percent = self._has("speed")
        self._speeds = [] if self._percent else self._options("fan_speed")
        features = self._features(FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF, {
            "speed": FanEntityFeature.SET_SPEED,
            "fan_speed": FanEntityFeature.SET_SPEED,
            "oscillate": FanEntityFeature.OSCILLATE,
            "direction": FanEntityFeature.DIRECTION,
            "mode": FanEntityFeature.PRESET_MODE,
        })
        if self._has("mode"):
            self._attr_preset_modes = self._options("mode")
        self._attr_supported_features = features

    @property
    def speed_count(self) -> int:
        return 100 if self._percent else len(self._speeds) or 1

    @property
    def oscillating(self) -> bool | None:
        return self._v("oscillate")

    @property
    def current_direction(self) -> str | None:
        return self._v("direction")

    @property
    def percentage(self) -> int | None:
        if self._percent:
            value = self._v("speed")
            return None if value is None else round(value)
        value = self._v("fan_speed")
        if value not in self._speeds:
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

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self._write("on", False)
        elif self._percent:
            await self._write("speed", percentage)
        elif self._speeds:
            await self._write("fan_speed", percentage_to_ordered_list_item(self._speeds, percentage))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._write("mode", preset_mode)

    async def async_oscillate(self, oscillating: bool) -> None:
        await self._write("oscillate", oscillating)

    async def async_set_direction(self, direction: str) -> None:
        await self._write("direction", direction)


class IlLight(_Toggle, LightEntity):
    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        modes: set[ColorMode] = set()
        if self._has("color"):
            modes.add(ColorMode.HS)
        if ct := self._slot_prop("color_temperature"):
            modes.add(ColorMode.COLOR_TEMP)
            self._set_known(
                min_color_temp_kelvin=None if ct.min is None else int(ct.min),
                max_color_temp_kelvin=None if ct.max is None else int(ct.max),
            )
        if not modes:
            modes.add(ColorMode.BRIGHTNESS if self._has("brightness") else ColorMode.ONOFF)
        self._attr_supported_color_modes = modes
        brightness = self._slot_prop("brightness")
        self._lowest = max(1, round(brightness.min)) if brightness and brightness.min else 1

    @property
    def brightness(self) -> int | None:
        value = self._v("brightness")
        return None if value is None else round(value * _HA_BRIGHTNESS / _IL_BRIGHTNESS)

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
        if not isinstance(text, str) or len(text) != 7 or not text.startswith("#"):
            return None
        try:
            rgb = [int(text[i:i + 2], 16) for i in (1, 3, 5)]
        except ValueError:
            return None
        return color_RGB_to_hs(*rgb)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write("on", True)
        if ATTR_HS_COLOR in kwargs and self._has("color"):
            r, g, b = color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
            await self._write("color", f"#{r:02x}{g:02x}{b:02x}")
        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._has("color_temperature"):
            await self._write("color_temperature", kwargs[ATTR_COLOR_TEMP_KELVIN])
        if ATTR_BRIGHTNESS in kwargs and self._has("brightness"):
            percent = round(kwargs[ATTR_BRIGHTNESS] * _IL_BRIGHTNESS / _HA_BRIGHTNESS)
            await self._write("brightness", max(self._lowest, min(_IL_BRIGHTNESS, percent)))


class IlCover(IlEntity, CoverEntity):
    _device_classes = CoverDeviceClass

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        position = self._slot_prop("position")
        tilt = self._slot_prop("tilt")
        movable = bool(position and position.writable)
        features = self._features(CoverEntityFeature(0), {
            "open": CoverEntityFeature.OPEN,
            "close": CoverEntityFeature.CLOSE,
            "stop": CoverEntityFeature.STOP,
        })
        if movable:
            features |= CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.SET_POSITION
        if tilt and tilt.writable:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features
        # without a position or a state the cover never says where it is
        self._attr_assumed_state = position is None and not self._has("cover_state")

    @property
    def current_cover_position(self) -> int | None:
        return self._v("position")

    @property
    def current_cover_tilt_position(self) -> int | None:
        return self._v("tilt")

    @property
    def is_closed(self) -> bool | None:
        position = self._v("position")
        if position is not None:
            return position == 0
        return _is(self._v("cover_state"), "closed")

    @property
    def is_opening(self) -> bool | None:
        return _is(self._v("cover_state"), "opening")

    @property
    def is_closing(self) -> bool | None:
        return _is(self._v("cover_state"), "closing")

    async def async_open_cover(self, **kwargs: Any) -> None:
        if self._has("open"):
            await self._write("open")
        else:
            await self._write("position", 100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        if self._has("close"):
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
        if self._has("unlatch"):
            self._attr_supported_features = LockEntityFeature.OPEN

    @property
    def is_locked(self) -> bool | None:
        state = self._v("lock_state")
        return self._v("locked") if state is None else state == "locked"

    @property
    def is_locking(self) -> bool | None:
        return _is(self._v("lock_state"), "locking")

    @property
    def is_unlocking(self) -> bool | None:
        return _is(self._v("lock_state"), "unlocking")

    @property
    def is_jammed(self) -> bool | None:
        return _is(self._v("lock_state"), "jammed")

    @property
    def is_open(self) -> bool | None:
        return _is(self._v("lock_state"), "open")

    async def async_lock(self, **kwargs: Any) -> None:
        await self._write("locked", True)

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._write("locked", False)

    async def async_open(self, **kwargs: Any) -> None:
        await self._write("unlatch")


class IlSiren(_Toggle, SirenEntity):
    _attr_supported_features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF


class IlValve(IlEntity, ValveEntity):
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _device_classes = ValveDeviceClass

    @property
    def is_closed(self) -> bool | None:
        return _is(self._v("opened"), False)

    async def async_open_valve(self, **kwargs: Any) -> None:
        await self._write("opened", True)

    async def async_close_valve(self, **kwargs: Any) -> None:
        await self._write("opened", False)


class IlAlarmControlPanel(IlEntity, AlarmControlPanelEntity):
    _attr_code_arm_required = False

    def __init__(self, hub, dev, spec) -> None:
        super().__init__(hub, dev, spec)
        self._attr_supported_features = self._features(AlarmControlPanelEntityFeature(0), {
            "arm_home": AlarmControlPanelEntityFeature.ARM_HOME,
            "arm_away": AlarmControlPanelEntityFeature.ARM_AWAY,
            "arm_night": AlarmControlPanelEntityFeature.ARM_NIGHT,
        })

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
        self._attr_supported_features = self._features(VacuumEntityFeature.STATE, {
            "start": VacuumEntityFeature.START,
            "pause": VacuumEntityFeature.PAUSE,
            "return_home": VacuumEntityFeature.RETURN_HOME,
            "locate": VacuumEntityFeature.LOCATE,
            "fan_speed": VacuumEntityFeature.FAN_SPEED,
        })
        if self._has("fan_speed"):
            self._attr_fan_speed_list = self._options("fan_speed")

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


ENTITY_CLASSES: dict[str, type[IlEntity]] = {
    "alarm_control_panel": IlAlarmControlPanel,
    "binary_sensor": IlBinarySensor,
    "button": IlButton,
    "climate": IlClimate,
    "cover": IlCover,
    "event": IlEvent,
    "fan": IlFan,
    "humidifier": IlHumidifier,
    "light": IlLight,
    "lock": IlLock,
    "number": IlNumber,
    "select": IlSelect,
    "sensor": IlSensor,
    "siren": IlSiren,
    "switch": IlSwitch,
    "text": IlText,
    "vacuum": IlVacuum,
    "valve": IlValve,
}
