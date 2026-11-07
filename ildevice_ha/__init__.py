"""The IL consumer for Home Assistant as a library: `core` (no Home Assistant) and the Home Assistant layer on it (hub,
entity classes, the mqtt transport, `attach.build_hub`).

The `ildevice` integration (custom_components/ildevice) is a thin host of this package, and another integration can
embed it the same way. Importing this package or `core` needs no Home Assistant.
"""

__version__ = "0.2.1"
