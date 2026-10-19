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
    assert manifest["version"] == "0.2.3"
    for key in ("domain", "documentation", "issue_tracker", "codeowners", "name"):
        assert manifest[key]
    assert json.loads((ROOT / "hacs.json").read_text())["homeassistant"] == "2026.10.0"
    with Image.open(COMPONENT / "brand/icon.png") as icon:
        assert icon.format == "PNG"
        assert icon.size == (256, 256)


def test_hysteresis_labels_and_errors_exist_in_both_flows():
    def unique_keys(pairs):
        assert len(dict(pairs)) == len(pairs)
        return dict(pairs)

    for path in (
        COMPONENT / "strings.json",
        *sorted((COMPONENT / "translations").glob("*.json")),
    ):
        text = json.loads(path.read_text(), object_pairs_hook=unique_keys)
        for kind, step in (("config", "user"), ("options", "init")):
            assert "hysteresis_on" in text[kind]["step"][step]["data"]
            assert "hysteresis_off" in text[kind]["step"][step]["data"]
            assert text[kind]["error"]["invalid_hysteresis"]
            assert text[kind]["step"]["valve_minima"]["data"]["minimum_openings"]
            assert text[kind]["error"]["invalid_minimum_opening"]
