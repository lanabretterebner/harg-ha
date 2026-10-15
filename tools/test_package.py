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
    def test_frontend_148_versions_and_independent_app_152(self):
        package = json.loads((ROOT / "package.json").read_text())
        lock = json.loads((ROOT / "package-lock.json").read_text())
        release = json.loads((ROOT / "release.json").read_text())
        validation = json.loads((ROOT / "validation.json").read_text())
        self.assertEqual({package["version"], lock["version"], lock["packages"][""]["version"], release["version"], validation["version"]}, {"1.4.8"})
        self.assertEqual(release["supervisor_app_version"], "1.5.2")
        self.assertEqual(load("hargassner-dashboard/config.yaml")["version"], "1.5.2")
        self.assertIn('const VERSION = "1.4.8";', (ROOT / "dist/hargassner-thermostat-popup.js").read_text())

    def test_repository_and_app_are_discoverable_independently_of_hacs(self):
        repository = load("repository.yaml")
        app = load("hargassner-dashboard/config.yaml")
        package = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(repository["url"], "https://github.com/lanabretterebner/harg-ha")
        self.assertTrue(repository["name"])
        for field in ("name", "version", "slug", "description", "arch"):
            self.assertTrue(app[field])
        release = json.loads((ROOT / "release.json").read_text())
        self.assertEqual(app["version"], release["supervisor_app_version"])
        self.assertEqual(package["version"], release["version"])
        self.assertRegex(app["slug"], r"^[a-z0-9_]+$")
        self.assertEqual(set(app["arch"]), {"amd64", "aarch64"})
        self.assertNotIn("image", app, "Supervisor must build the included source")

    def test_app_has_only_notification_ha_permission_and_no_host_privileges_or_ports(self):
        app = load("hargassner-dashboard/config.yaml")
        self.assertIs(app["ingress"], True)
        self.assertEqual(app["ingress_port"], 8099)
        self.assertIs(app["panel_admin"], False)
        for field in ("host_network", "host_pid", "hassio_api",
                      "docker_api", "full_access", "privileged", "ports", "map"):
            self.assertFalse(app.get(field), field)
        self.assertIs(app["homeassistant_api"], True)
        self.assertEqual(app["options"], {"upstream_url": None, "ingress_token": None,
                                           "monitor_enabled": False, "monitor_token": "",
                                           "monitor_poll_interval": 30})
        self.assertEqual(app["schema"], {"upstream_url": "url", "upstream_connect_host": "str?", "ingress_token": "password",
                                           "monitor_enabled": "bool", "monitor_token": "password?",
                                           "monitor_poll_interval": "int(15,60)"})

    def test_dockerfile_uses_verified_multiarch_index_and_only_monitor_ingress_source(self):
        image = json.loads((ROOT / "validation/base-image.json").read_text())
        dockerfile = (ROOT / "hargassner-dashboard/Dockerfile").read_text()
        self.assertIn(f"FROM {image['repository']}:{image['tag']}@{image['digest']}", dockerfile)
        platforms = {f"{row['os']}/{row['architecture']}" for row in image["platforms"]}
        self.assertTrue({"linux/amd64", "linux/arm64"}.issubset(platforms))
        self.assertEqual(re.findall(r"^COPY\s+(.*)$", dockerfile, re.M),
                         ["ingress_proxy.py app_monitor.py health_alerts.py run_app.py LICENSE /app/"])
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

    def test_device_groups_are_frontend_metadata_for_one_unchanged_device(self):
        groups = load("examples/entity-ui-groups.json")
        names = load("examples/entity-name-mapping.json")
        self.assertEqual(len(groups["entities"]), 90)
        self.assertEqual(set(groups["entities"]), {row["settings_selection_id"] for row in names})
        self.assertEqual(len(groups["groups"]), 8)
        for row in names:
            value = groups["entities"][row["settings_selection_id"]]
            self.assertEqual(set(value), {"default_entity_id", "group"})
            self.assertEqual(value["default_entity_id"], row["default_entity_id"])
            self.assertIn(value["group"], groups["groups"])
        self.assertIn("no MQTT metadata or registry changes", groups["scope"])

    def test_complete_dashboard_has_two_custom_cards_and_eight_sections(self):
        dashboard = load("examples/hargassner-dashboard.json")
        cards = dashboard["views"][0]["cards"]
        self.assertEqual(len(cards), 8)
        stacks = [card for card in cards if card["type"] == "vertical-stack"]
        self.assertEqual(len(stacks), 2)
        climates = [stack["cards"][0] for stack in stacks]
        self.assertTrue(all(card["type"] == "custom:hargassner-thermostat-card" for card in climates))
        self.assertEqual({card["entity"] for card in climates}, {"climate.heizkreis_1", "climate.heizkreis_2"})
        allowed = {row["default_entity_id"] for row in load("examples/entity-name-mapping.json")}
        def walk(value):
            if isinstance(value, dict):
                if "entity" in value:
                    self.assertIn(value["entity"], allowed)
                if value.get("type") == "entities":
                    self.assertIs(value.get("show_header_toggle"), False)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(dashboard)
        self.assertNotIn("sensor.aussentemperatur_2", json.dumps(dashboard))


if __name__ == "__main__":
    unittest.main()
