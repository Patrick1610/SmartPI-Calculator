"""Check distributed source and license integrity without network access."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/smartpi_calculator"


def test_vendor_matches_pinned_hashes():
    manifest = json.loads((COMPONENT / "UPSTREAM.json").read_text())
    actual = {
        p.relative_to(COMPONENT / "vendor").as_posix()
        for p in (COMPONENT / "vendor").rglob("*.py")
    }
    assert actual - {"__init__.py"} == {f["path"] for f in manifest["files"]}
    for item in manifest["files"]:
        data = (COMPONENT / "vendor" / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["vendored_sha256"]
        if item["path"] not in manifest["adaptations"]:
            assert item["upstream_sha256"] == item["vendored_sha256"]


def test_license_and_notice_travel_with_component():
    for name in ("LICENSE", "NOTICE"):
        assert (ROOT / name).read_bytes() == (COMPONENT / name).read_bytes()
