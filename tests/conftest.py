import json
from pathlib import Path

import pytest

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
