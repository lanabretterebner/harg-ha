"""Check public HA delivery metadata, permissions and client examples."""
from pathlib import Path
import json
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text())


class PackageTests(unittest.TestCase):
    def test_repository_and_app_are_discoverable_independently_of_hacs(self):
        repository = load("repository.yaml")
        app = load("hargassner-dashboard/config.yaml")
        package = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(repository["url"], "https://github.com/lanabretterebner/harg-ha")
        self.assertTrue(repository["name"])
        for field in ("name", "version", "slug", "description", "arch"):
            self.assertTrue(app[field])
        self.assertEqual(app["version"], package["version"])
        self.assertRegex(app["slug"], r"^[a-z0-9_]+$")
        self.assertEqual(set(app["arch"]), {"amd64", "aarch64"})
        self.assertNotIn("image", app, "Supervisor must build the included source")

    def test_app_needs_no_host_or_supervisor_privileges_or_public_ports(self):
        app = load("hargassner-dashboard/config.yaml")
        self.assertIs(app["ingress"], True)
        self.assertEqual(app["ingress_port"], 8099)
        self.assertIs(app["panel_admin"], True)
        for field in ("host_network", "host_pid", "hassio_api", "homeassistant_api",
                      "docker_api", "full_access", "privileged", "ports", "map"):
            self.assertFalse(app.get(field), field)
        self.assertEqual(app["options"], {"upstream_url": None, "ingress_token": None})
        self.assertEqual(app["schema"], {"upstream_url": "url", "ingress_token": "password"})

    def test_dockerfile_uses_verified_multiarch_index_and_only_proxy_source(self):
        image = json.loads((ROOT / "validation/base-image.json").read_text())
        dockerfile = (ROOT / "hargassner-dashboard/Dockerfile").read_text()
        self.assertIn(f"FROM {image['repository']}:{image['tag']}@{image['digest']}", dockerfile)
        platforms = {f"{row['os']}/{row['architecture']}" for row in image["platforms"]}
        self.assertTrue({"linux/amd64", "linux/arm64"}.issubset(platforms))
        self.assertEqual(re.findall(r"^COPY\s+(.*)$", dockerfile, re.M),
                         ["ingress_proxy.py /app/ingress_proxy.py"])
        self.assertIn('io.hass.type="app"', dockerfile)
        self.assertIn('io.hass.arch="aarch64|amd64"', dockerfile)
        self.assertNotIn("BUILD_FROM", dockerfile)
        self.assertFalse(image["container_build_performed"])

    def test_tiles_show_controller_status_and_open_native_dialog(self):
        cards = load("examples/thermostat-cards.yaml")["cards"]
        self.assertEqual(len(cards), 8)
        climates = [card for card in cards if card["entity"].startswith("climate.")]
        self.assertEqual(len(climates), 2)
        for card in climates:
            self.assertEqual(card["type"], "tile")
            self.assertEqual(card["state_content"], ["temperature", "Heizkreisstatus"])
            self.assertEqual(card["tap_action"], {"action": "more-info"})
            self.assertEqual(card["icon_tap_action"], {"action": "more-info"})
            self.assertNotIn("features", card)
        self.assertNotIn("preset", json.dumps(cards))

    def test_mapping_is_reference_metadata_without_installation_values(self):
        rows = json.loads((ROOT / "examples/entity-name-mapping.json").read_text())
        self.assertEqual(len(rows), 90)
        self.assertEqual(len({row["settings_selection_id"] for row in rows}), 90)
        for row in rows:
            self.assertEqual(set(row), {"settings_selection_id", "canonical_name",
                                        "default_entity_id", "discovery_object_id"})


if __name__ == "__main__":
    unittest.main()
