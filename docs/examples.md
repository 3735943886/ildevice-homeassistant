# Examples

These examples show what a producer publishes and what Home Assistant does with it. They use
`mosquitto_pub` / `mosquitto_sub`, so you can try them against your own broker with no producer
running. Each one uses the default topic prefix `il`.

The full descriptor and value rules are in the [ildevice](https://github.com/3735943886/ildevice)
repository: `il.md` for descriptors and `il-mqtt.md` for topics and payloads.

The flow is the same for every device:

1. **Presence.** The producer publishes `online` (retained) on `il/_producer/<source>`, and sets
   `offline` as its MQTT Last Will.
2. **Registration.** The producer publishes the descriptor (retained) on `il/<id>`. Home Assistant
   shows the device under "Discovered". Once you add it, its entities are created. With
   "Add discovered devices automatically" turned on, they are created at once.
3. **State.** The producer publishes each value (retained) on its state topic, by default
   `il/<id>/<prop>`.
4. **Commands.** Home Assistant writes to the set topic, by default `il/<id>/<prop>/set` (QoS 1, not
   retained). The producer checks the write, applies it, and publishes the new value on the state
   topic. If it refuses the write, it publishes a reason on `il/<id>/reject`.
5. **Removal.** The producer clears the retained values, then publishes an empty retained payload on
   `il/<id>`. The device and its entities are removed from Home Assistant.

Payloads are plain text: `true` / `false`, a number such as `21.5`, or a string such as `cool`,
without JSON quotes. An empty retained payload on a state topic means the value is unknown.

## 1. Smart plug: plain entities

A device whose `kind` has no composite (`switch` here) becomes one plain entity per property. The
entity type follows from the property's type and whether it is writable (`rw`).

```sh
mosquitto_pub -r -q 1 -t il/_producer/demo -m online

mosquitto_pub -r -q 1 -t il/plug1 -m '{
  "il": 0, "id": "plug1", "source": "demo",
  "kind": "switch", "class": "outlet",
  "label": "Desk plug", "vendor": "Acme", "model": "P100",
  "props": {
    "available":   { "type": "binary", "role": "available" },
    "power":       { "type": "binary", "rw": true, "class": "outlet", "label": "Power" },
    "watts":       { "type": "number", "class": "power", "unit": "W", "label": "Power draw" },
    "energy":      { "type": "number", "class": "energy", "series": "counter", "unit": "kWh" },
    "relay_start": { "type": "select", "rw": true, "options": ["on", "off", "last"], "category": "config" }
  }
}'

mosquitto_pub -r -q 1 -t il/plug1/available -m true
mosquitto_pub -r -q 1 -t il/plug1/power -m false
mosquitto_pub -r -q 1 -t il/plug1/watts -m 0
mosquitto_pub -r -q 1 -t il/plug1/energy -m 12.4
mosquitto_pub -r -q 1 -t il/plug1/relay_start -m last
```

After you add **Desk plug**, it has these entities:

| property | entity | unique id |
|---|---|---|
| `power` | switch (outlet) | `plug1-power` |
| `watts` | sensor, power, W | `plug1-watts` |
| `energy` | sensor, energy, total increasing | `plug1-energy` |
| `relay_start` | select (configuration) | `plug1-relay_start` |

`available` does not become an entity. It decides whether the other entities are available.

When you turn the switch on in Home Assistant:

```
il/plug1/power/set   true      <- Home Assistant
il/plug1/power       true      <- producer, retained, once the plug has switched
```

## 2. Light: a composite entity

With `kind: "light"`, properties that carry light roles form one light entity rather than separate
ones. The roles present decide the supported color modes: here brightness, color temperature and
RGB color.

```sh
mosquitto_pub -r -q 1 -t il/bulb1 -m '{
  "il": 0, "id": "bulb1", "source": "demo", "kind": "light", "label": "Hall light",
  "props": {
    "on":     { "type": "binary", "rw": true, "role": "on" },
    "bright": { "type": "number", "rw": true, "role": "brightness", "unit": "%", "min": 1, "max": 100, "step": 1 },
    "temp":   { "type": "number", "rw": true, "role": "color_temperature", "unit": "K", "min": 2700, "max": 6500 },
    "colour": { "type": "text",   "rw": true, "role": "color" },
    "mode":   { "type": "select", "role": "color_mode", "options": ["white", "color"] }
  }
}'

mosquitto_pub -r -q 1 -t il/bulb1/on -m true
mosquitto_pub -r -q 1 -t il/bulb1/bright -m 80
mosquitto_pub -r -q 1 -t il/bulb1/temp -m 4000
mosquitto_pub -r -q 1 -t il/bulb1/mode -m white
```

This gives one entity, `light.hall_light`, with unique id `bulb1-light`. Brightness is a
percentage on the wire, and the integration converts it to and from Home Assistant's 0 to 255 scale.

`light.turn_on` with `brightness_pct: 50` and `rgb_color: [255, 0, 0]` sends one write per
property:

```
il/bulb1/on/set       true
il/bulb1/colour/set   #ff0000
il/bulb1/bright/set   50
```

## 3. Air conditioner: climate, and a control with a condition

`kind: "climate"` needs `target_temperature`. The other climate roles are optional. A property
with `requires` accepts writes only while the condition holds. While it does not, the integration
refuses the write itself and never sends it.

```sh
mosquitto_pub -r -q 1 -t il/ac1 -m '{
  "il": 0, "id": "ac1", "source": "demo",
  "kind": "climate", "class": "air_conditioner", "label": "Bedroom AC",
  "props": {
    "available": { "type": "binary", "role": "available" },
    "power":     { "type": "binary", "rw": true, "role": "on" },
    "mode":      { "type": "select", "rw": true, "role": "mode", "options": ["cool", "dry", "fan_only", "auto"] },
    "fan":       { "type": "select", "rw": true, "role": "fan_speed", "options": ["low", "mid", "high", "auto"] },
    "target":    { "type": "number", "rw": true, "role": "target_temperature", "unit": "°C", "min": 18, "max": 30, "step": 0.5 },
    "room":      { "type": "number", "role": "current_temperature", "unit": "°C" },
    "power_save": { "type": "binary", "rw": true, "label": "Power save",
                    "requires": { "prop": "mode", "in": ["cool"] } }
  }
}'

for kv in available=true power=true mode=cool fan=auto target=24 room=26.5 power_save=false; do
  mosquitto_pub -r -q 1 -t "il/ac1/${kv%%=*}" -m "${kv#*=}"
done
```

The result is `climate.bedroom_ac`, with HVAC modes built from `mode` plus off (from `power`), fan
modes from `fan`, a target temperature from 18 to 30 in steps of 0.5, and the current temperature.
Only `mode` options that are Home Assistant HVAC mode names (`off`, `heat`, `cool`, `heat_cool`,
`auto`, `dry`, `fan_only`) become HVAC modes.
`power_save` becomes a separate switch. When `mode` is not `cool`, the switch is unavailable and
nothing is sent.

Setting the climate to "off" writes `false` to `power`. Setting it to "dry" while it is off turns
it on first:

```
il/ac1/power/set   true
il/ac1/mode/set    dry
```

## 4. Commands the producer refuses

The producer checks every write against its own descriptor: the range, the step, `requires`, and
anything else it knows about. It answers a write it will not carry out on the reject topic. For
example, the AC in example 3 receives a target of `24.3`, which is off its 0.5 step:

```sh
mosquitto_pub -q 1 -t il/ac1/reject -m '{"prop": "target", "code": "bad_step", "reason": "24.3 is not a multiple of 0.5"}'
```

The reject message is not retained. Home Assistant logs a warning and fires an event you can use in
an automation:

```yaml
triggers:
  - trigger: event
    event_type: ildevice_command_rejected
    event_data:
      device_id: ac1
actions:
  - action: persistent_notification.create
    data:
      message: "{{ trigger.event.data.prop }}: {{ trigger.event.data.reason }}"
```

The event data holds `device_id`, `prop`, `code` and `reason`.

## 5. Curtain with a light: groups

A group with its own `kind` is a composite of its own, so one device can hold several. Here the
device is a cover, and the properties in the `light` group form a light.

```sh
mosquitto_pub -r -q 1 -t il/curtain1 -m '{
  "il": 0, "id": "curtain1", "source": "demo",
  "kind": "cover", "class": "curtain", "label": "Living room curtain",
  "groups": { "light": { "kind": "light", "label": "Curtain light" } },
  "props": {
    "position":   { "type": "number", "rw": true, "role": "position", "unit": "%", "min": 0, "max": 100 },
    "state":      { "type": "select", "role": "cover_state", "options": ["open", "closed", "opening", "closing", "stopped"] },
    "stop":       { "type": "trigger", "role": "stop" },
    "led":        { "type": "binary", "rw": true, "role": "on", "group": "light" },
    "led_bright": { "type": "number", "rw": true, "role": "brightness", "unit": "%", "min": 1, "max": 100, "group": "light" }
  }
}'

mosquitto_pub -r -q 1 -t il/curtain1/position -m 100
mosquitto_pub -r -q 1 -t il/curtain1/state -m open
mosquitto_pub -r -q 1 -t il/curtain1/led -m false
```

One device gets two entities:

| entity | unique id | from |
|---|---|---|
| `cover.living_room_curtain` (curtain) | `curtain1-cover` | `position`, `state`, `stop` |
| `light.curtain_light` | `curtain1-light` | `led`, `led_bright` |

The cover has no `open` or `close` trigger, so opening it writes `100` to `position` and closing it
writes `0`. Stop presses the `stop` trigger with an empty payload:

```
il/curtain1/position/set   0
il/curtain1/stop/set       (empty)
```

## 6. Wireless button: events

An `event` property is the opposite of a `trigger`. It has no state: every message is one press.
It becomes an event entity whose event types are the property's `options`.

```sh
mosquitto_pub -r -q 1 -t il/btn1 -m '{
  "il": 0, "id": "btn1", "source": "demo", "kind": "remote", "label": "Bedside button",
  "props": {
    "button":  { "type": "event", "options": ["click", "double_click", "long_press"] },
    "battery": { "type": "number", "role": "battery", "unit": "%", "category": "diagnostic" }
  }
}'

mosquitto_pub -q 1 -t il/btn1/button -m double_click     # not retained
```

`event.bedside_button_button` records `double_click`. Publish the event **without** the retain
flag: the integration ignores a retained message on an event topic, because the broker would
replay an old press every time Home Assistant reconnects.

## 7. Your own topic layout, and separate hubs

A producer that already has a topic layout keeps it. The descriptor says where the values are with
an `x-mqtt` block: `{id}` and `{prop}` are filled in, and a property can override the device's
layout. The descriptor itself always lives under the prefix.

```sh
mosquitto_pub -r -q 1 -t il/tuya/_producer/rustuya -m online

mosquitto_pub -r -q 1 -t il/tuya/valve1 -m '{
  "il": 0, "id": "valve1", "source": "rustuya", "kind": "valve", "class": "water", "label": "Garden valve",
  "props": {
    "opened": { "type": "binary", "rw": true, "role": "opened" },
    "flow":   { "type": "number", "unit": "L", "series": "counter", "class": "volume",
                "x-mqtt": { "state": "tuya/{id}/dp5" } }
  },
  "x-mqtt": {
    "state":  "tuya/{id}/{prop}",
    "set":    "tuya/{id}/{prop}/command",
    "reject": "tuya/{id}/error"
  }
}'

mosquitto_pub -r -q 1 -t tuya/valve1/opened -m false
mosquitto_pub -r -q 1 -t tuya/valve1/dp5 -m 132.5
```

| property | state topic | set topic |
|---|---|---|
| `opened` | `tuya/valve1/opened` | `tuya/valve1/opened/command` |
| `flow` | `tuya/valve1/dp5` | none (read only) |

This descriptor is under `il/tuya`, so it belongs to an integration entry whose topic prefix is
`il/tuya`. You can add a second entry for `il/thinq`, and each keeps its own devices and options.
The producer's presence topic is under the same prefix (`il/tuya/_producer/rustuya`), and when it
goes `offline`, every device from that producer becomes unavailable.

A valve or lock needs its `opened` or `locked` property to be writable. When it is read only, the
device shows a plain binary sensor instead of a valve or lock you cannot operate.

## 8. Changing and removing a device

Publishing a new descriptor on the same topic updates the device. Its entities are rebuilt from the
new descriptor, and unique ids that stay the same keep their entity ids and settings.

To remove a device, clear its retained values first, then the descriptor:

```sh
for p in available power watts energy relay_start; do
  mosquitto_pub -r -q 1 -t il/plug1/$p -n
done
mosquitto_pub -r -q 1 -t il/plug1 -n
```

The device and its entities are removed from Home Assistant, including the device registry entry.
If the descriptor is published again, the device returns.

## Watching the traffic

To see everything a producer and Home Assistant exchange under the default prefix:

```sh
mosquitto_sub -v -t 'il/#'
```

Add `-t 'tuya/#'` or similar for devices whose `x-mqtt` points elsewhere.
