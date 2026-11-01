import json
from pathlib import Path

import pytest

# Imported before the test Home Assistant puts its own testing_config (a `custom_components` package without this
# integration) first on sys.path, so `custom_components` stays this repository's.
import custom_components.ildevice  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"


def load(path: str) -> dict:
    return json.loads((FIXTURES / path).read_text())


def all_fixtures():
    return sorted(FIXTURES.glob("*/*.json"))


@pytest.fixture(params=all_fixtures(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def descriptor_doc(request):
    return json.loads(request.param.read_text())


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The MQTT client keeps a periodic housekeeping timer alive; that is Home Assistant's."""
    return True
