![IL device](custom_components/ildevice/brand/logo@2x.png#gh-light-mode-only)
![IL device](custom_components/ildevice/brand/dark_logo@2x.png#gh-dark-mode-only)

# IL device for Home Assistant (il-ha)

A Home Assistant integration for [ildevice](https://github.com/3735943886/ildevice) (Intermediate
Layer device model). It turns the ildevice descriptors that producers publish over MQTT into
Home Assistant devices and entities. It knows nothing about any one producer or model: a device
from rusthinq, rustuya or anything else that publishes a descriptor gets its device and entities
from the descriptor alone.

## Setup

Requires Home Assistant's `mqtt` integration. Install this repository as a custom repository in
HACS (or copy `custom_components/ildevice`), restart, and add the **IL device** integration. Add it once per hub (see [Hubs](#hubs)).

| option | meaning |
|---|---|
| Topic prefix | The ildevice prefix the producers publish under (default `il`), for example `il/tuya`. Each entry is a hub, so it must be unique. |
| Seconds before a device is shown unavailable | Keeps a short outage from showing (default `0`). |
| Legacy entity ids | JSON aliases that keep the unique ids of an earlier integration, see below. |
| Add discovered devices automatically | Skip the add / ignore question, see below. |

## Hubs

Each entry of the integration is a **hub**: one topic prefix, such as `il/tuya` or `il/thinq`. Add the
integration again for each producer and give it its own prefix. Every hub shows up in Home Assistant
as a device named after its prefix, and the devices found under that prefix are placed beneath it
(`via_device`). A discovered device is offered to, and added to, the hub it was found on. Each hub has
its own options (auto-add, offline grace, legacy ids); changing a hub's prefix renames it. A prefix can
be used by one hub only, and a hub device is removed together with its entry, not on its own. Device
ids should be unique across hubs, as they are the device identifier and the base of the unique ids.

## How it works

- It subscribes to `<il_prefix>/+` (the retained descriptors) and `<il_prefix>/_producer/+`
  (producer presence). For each device it subscribes to the value topics and the `reject` topic the
  descriptor points at (`x-mqtt`, see `il-mqtt.md` in the ildevice repository).
- A device's **kind** and the **roles** of its properties decide the composite entities. A group
  with its own `kind` is a composite of its own, so one device can be a light and a cover.

  | kind | entity | it needs |
  |---|---|---|
  | `climate` | `climate` | `target_temperature` |
  | `humidifier` | `humidifier` (class `dehumidifier` for a dehumidifier) | `on`, `target_humidity` |
  | `fan` | `fan` (`fan_speed`, or `speed` when the device takes a percentage; `oscillate`, `direction`) | `on` |
  | `light` | `light`; modes follow from `brightness`, `color_temperature`, `color` | `on` |
  | `cover` | `cover` | `position`, `open` or `close` |
  | `lock` | `lock` | writable `locked` |
  | `valve` | `valve` | writable `opened` |
  | `siren` | `siren` | `on` |
  | `alarm` | `alarm_control_panel` | see `il.md` |
  | `vacuum` | `vacuum` | see `il.md` |

  A cover without `open` / `close` moves by writing `position` 100 / 0. A cover is closed when its
  `position` is 0, or else when its `cover_state` is `closed`; `cover_state` also says opening and
  closing. A lock shows `lock_state` (locking, unlocking, jammed, open) when it has one. A lock or valve that is not
  writable stays a plain binary sensor: a role never implies control.
- Every other property is a plain entity chosen by its type: `sensor` / `binary_sensor` /
  `switch` / `number` / `select` / `text` / `button` / `event`. `class`, `series` and `category`
  become `device_class`, `state_class` and `entity_category`; a property with no device class gets
  an icon chosen from its name, unit and kind.
- A control whose property has `requires` (a binary that must be true, or a select whose value must
  be in a list) is unavailable, or for one control of a composite refused, until the condition
  holds. A refused command arrives as an `ildevice_command_rejected` event (`device_id`, `prop`,
  `reason`).
- Availability is the device's `available` property and the producer's presence
  (`<il_prefix>/_producer/<source>`; `offline` makes all of its devices unavailable).
  `offline_grace` keeps a short outage from showing.
- An empty (null) descriptor withdraws the device: its entities, registry entries and value
  subscriptions go, and it is created again if the descriptor is published again. A changed
  descriptor re-plans its entities.
- A property with an unknown role, type or field is still usable as a plain entity.

## Adding devices

A device that publishes a descriptor is not created at once. Home Assistant shows it under
"Discovered" (named after the descriptor's `label`), and you add it or ignore it. An ignored device
is not offered again. Deleting a device in Home Assistant makes it a new offer the next time it
appears. The option "Add discovered devices automatically" skips the question and creates every
device, and turning it off keeps the devices it had added.

## Keeping the ids of an earlier integration

An entity's unique id is `<device id>-<key>`, where the key is the property name, or for a
composite its platform (`climate`, `light`, ...) or its group name. To keep the ids an earlier
integration created, give aliases in the options, as JSON keyed by model (`*` for any):

```json
{"DHUM_056905_WW": {"uvnano": "uv_nano", "tank_full": "bucket_full"}, "*": {}}
```

## Layout

```
custom_components/ildevice/core/  Home Assistant free: descriptor, plan (entities), topics, values,
                                  the consumer model, transport interface, in-process broker, icons
custom_components/ildevice/       the integration: hub (the model's sink), entity classes, platforms,
                                  config flow, HA mqtt transport, attach
tests/                            core and model tests, the ildevice vectors, and the integration
                                  in a test Home Assistant
```

`core/plan.py` is where a descriptor becomes entities; nothing in it looks at a model.
`core/model.py` follows descriptors, values, presence and rejections on a `Transport` and tells a
`Sink` what changed, so the same core runs without Home Assistant.

### Embedding

Another integration that produces ildevice descriptors in the same process can use this one as its
consumer instead of going through a broker: `attach.build_hub(hass, options, transport, platform)`
takes its own transport (for example `core.memory.InProcessTransport`, an in-process broker with
MQTT's filters and retained messages) and its own domain. It forwards the platforms listed in
`const.PLATFORMS` to its config entry and hands each one's `async_add_entities` to
`hub.register_platform`.

## Tests

```
python -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```

- `tests/fixtures/rusthinq/` holds the descriptors the rusthinq drivers publish (ten LG
  appliances), which cover the composite kinds, `trigger` / `requires` and groups.
- `tests/fixtures/sample/` holds descriptors from a Tuya producer with its own topic layout: a
  plug, bulb, curtain, lock, valve, siren and button.
- `tests/test_vectors.py` runs the language-neutral conformance vectors of the ildevice
  repository. It looks for a checkout at `../ildevice`, or at `$ILDEVICE`, and is skipped without
  one.

## Not yet

- Moving an existing MQTT-discovery entity onto this integration without losing its history.
