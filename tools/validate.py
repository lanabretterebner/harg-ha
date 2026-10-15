"""Validate the portable plugin structure and every release-pinned local byte."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def validate():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    package = json.loads((ROOT / "package.json").read_text())
    release = json.loads((ROOT / "release.json").read_text())
    assert hacs == {"name": "Hargassner Thermostat Popup",
                    "filename": "hargassner-thermostat-popup.js"}
    assert package["name"] == "harg-ha"
    assert package["version"] == release["version"] == "1.4.7"
    assert package["type"] == "module"
    script = ROOT / "dist" / hacs["filename"]
    text = script.read_text()
    assert 'const VERSION = "1.4.7";' in text
    assert 'const LABEL = "Modus";' in text
    assert "configured_room_setpoint_v1" in text
    assert "hargassner-thermostat-card" in text
    assert "customCards" in text
    assert "_renderEntities" in text
    assert not re.search(r'import\s*(?:\(|[\s\S]*?from\s*)[\"\']\.', text)
    for name in ("test-thermostat-popup.js", "test-thermostat-card.js", "test-device-groups.js"):
        fixture = (ROOT / "tests" / name).read_text()
        assert "../dist/hargassner-thermostat-popup.js" in fixture
    assert (ROOT / "tests/device-group-fixture-data.js").is_file()
    assert set(path.name for path in (ROOT / "dist").iterdir()) == {hacs["filename"]}
    for name, expected in release["files"].items():
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts
        assert not any(part.startswith(".") for part in path.parts)
        target = ROOT / name
        assert target.is_file() and not target.is_symlink()
        assert re.fullmatch(r"[0-9a-f]{64}", expected)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected, name
    actual = {str(path.relative_to(ROOT)) for path in ROOT.rglob("*")
              if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts and "node_modules" not in path.parts}
    assert actual == set(release["files"]) | {"release.json"}
    assert release["hacs_installation_verified"] is False
    assert release["supervisor_app_installation_verified"] is False
    assert (ROOT / "repository.yaml").is_file()
    assert (ROOT / "hargassner-dashboard/config.yaml").is_file()
    assert (ROOT / "licenses/Apache-2.0.txt").is_file()
    print(f"Plugin structure and {len(release['files'])} pinned files verified; release-specific hosted installation checks are recorded separately.")


if __name__ == "__main__":
    validate()
