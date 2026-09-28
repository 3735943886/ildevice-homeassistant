"""The cover platform: entities are created by the hub as descriptors arrive."""

from .platform_setup import platform_setup

async_setup_entry = platform_setup(__name__)
