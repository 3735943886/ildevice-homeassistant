# il-ha

A Home Assistant integration that turns [IL](https://github.com/3735943886/il) descriptors into
entities. It knows nothing about any one producer or model: a device from rusthinq, rustuya or
anything else that publishes an IL descriptor over MQTT gets its device and entities from the
descriptor alone.

## How it works

- It subscribes to `<il_prefix>/+` (the retained descriptors) and, for each device, to the value
  topics and the `reject` topic the descriptor points at (`x-mqtt`, see the IL's `il-mqtt.md`).
- A device's **kind** and the **roles** of its properties decide the composite entities:
  `climate`, `humidifier` (a dehumidifier is class `dehumidifier`), `fan`. Every other property is
  a plain entity chosen by its type: `sensor` / `binary_sensor` / `switch` / `number` / `select` /
  `text` / `button`. `class`, `series` and `category` become `device_class`, `state_class` and
  `entity_category`.
- A control whose property has `requires` is unavailable until the property it names is true.
  A refused command arrives as an `il_ha_command_rejected` event (`device_id`, `prop`, `reason`).
- Availability is the device's `available` property. `offline_grace` (seconds) keeps a short
  outage from showing.
- An empty descriptor removes the device; a changed one re-plans its entities.

## Adding devices

A device that publishes a descriptor is not created at once. Home Assistant shows it under
"Discovered" (named after the descriptor's `label`), and you add it or ignore it. An ignored device
is not offered again. Deleting a device in Home Assistant makes it a new offer the next time it
appears. The option "Add discovered devices automatically" skips the question and creates every
device, and turning it off keeps the devices it had added. An entry set up before this existed keeps
the devices it already had.

## Layout

```
custom_components/il_ha/core/   Home Assistant free: parse, topics, values, plan (entities)
custom_components/il_ha/        the integration: hub, entity classes, platforms, config flow
tests/                          core tests, and the integration in a test Home Assistant
```

`core/plan.py` is where a descriptor becomes entities; nothing in it looks at a model.

## Keeping the ids of an earlier integration

An entity's unique id is `<device id>-<key>` (the property name, or `climate` / `humidifier` /
`fan`). To keep the ids an earlier integration created, give aliases in the options, as JSON:

```json
{"DHUM_056905_WW": {"uvnano": "uv_nano", "tank_full": "bucket_full"}, "*": {}}
```

## Tests

```
python -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```

The fixtures under `tests/fixtures/rusthinq/` are the descriptors the rusthinq drivers publish;
`tests/fixtures/sample/` holds one from a different producer with its own topic layout.

## Not yet

- `light`, `cover`, `lock`: the IL has no roles for brightness, colour, position yet. They are
  added to the IL when a producer needs them.
- Moving an existing MQTT-discovery entity onto this integration without losing its history.
