"""Home Assistant free core of the IL consumer: parse descriptors, locate and decode values,
encode commands, and plan which entities a descriptor becomes.

Nothing here imports Home Assistant, so it can be tested without it and reused by another
consumer. `plan.py` says *what* entities a device becomes; the integration's platform modules
turn each `EntitySpec` into a real entity.
"""

from .descriptor import Descriptor, DescriptorError, Prop, Requires, parse_descriptor
from .model import Device, IlModel, Sink, parse_aliases
from .plan import EntitySpec, plan_entities
from .topics import Topics, resolve_topics
from .transport import CallLater, Message, Transport
from .values import decode_value, encode_command

__all__ = [
    "CallLater",
    "Descriptor",
    "DescriptorError",
    "Device",
    "EntitySpec",
    "IlModel",
    "Message",
    "Prop",
    "Requires",
    "Sink",
    "Topics",
    "Transport",
    "decode_value",
    "encode_command",
    "parse_aliases",
    "parse_descriptor",
    "plan_entities",
    "resolve_topics",
]
