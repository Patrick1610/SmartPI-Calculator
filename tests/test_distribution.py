"""Minimal HACS package and HA integration visibility contract."""

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/smartpi_calculator"


def test_hacs_package_metadata_and_brand():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["integration_type"] == "service"
    assert manifest["config_flow"] is True
    assert manifest["version"] == "0.2.0"
    for key in ("domain", "documentation", "issue_tracker", "codeowners", "name"):
        assert manifest[key]
    assert json.loads((ROOT / "hacs.json").read_text())["homeassistant"] == "2026.10.0"
    with Image.open(COMPONENT / "brand/icon.png") as icon:
        assert icon.format == "PNG"
        assert icon.size == (256, 256)
