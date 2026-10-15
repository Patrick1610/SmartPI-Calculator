"""Generate pinned vendor provenance from the verified upstream checkout."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "custom_components/smartpi_calculator/vendor"
COMMIT = "6a131df56cf3cfe2974f78f55f33ce8a095076b3"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    upstream = Path(sys.argv[1]).resolve()
    assert (
        subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
        ).strip()
        == COMMIT
    )
    source = upstream / "custom_components/vtherm_smartpi"
    files = []
    for path in sorted(VENDOR.rglob("*.py")):
        relative = path.relative_to(VENDOR)
        if relative.as_posix() == "__init__.py":
            continue  # New inert package marker, not copied integration setup.
        original = source / relative
        original_hash, local_hash = digest(original), digest(path)
        if relative.as_posix() != "hvac_mode.py":
            assert original_hash == local_hash, relative
        files.append(
            {
                "path": relative.as_posix(),
                "upstream_sha256": original_hash,
                "vendored_sha256": local_hash,
            }
        )
    payload = {
        "repository": "https://github.com/KipK/vtherm_smartpi",
        "commit": COMMIT,
        "license": "Apache-2.0",
        "adaptations": {
            "hvac_mode.py": "Replace VTherm logger import with standard logging only."
        },
        "files": files,
    }
    (VENDOR.parent / "UPSTREAM.json").write_text(json.dumps(payload, indent=2) + "\n")
