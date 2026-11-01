"""The integration and the `ildevice-homeassistant` package it requires are released together, at one version."""

import json
import tomllib
from pathlib import Path

import ildevice_ha

ROOT = Path(__file__).resolve().parents[1]


def test_the_manifest_requires_this_version_of_the_package():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    manifest = json.loads((ROOT / "custom_components/ildevice/manifest.json").read_text())
    assert project["version"] == ildevice_ha.__version__ == manifest["version"]
    assert manifest["requirements"] == [f"{project['name']}=={project['version']}"]
