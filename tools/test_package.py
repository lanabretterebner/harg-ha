"""Check public HA delivery metadata, permissions and client examples."""
from pathlib import Path
import json
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
HOUSE_LISTS = ("heizkreis_1_sensoren", "heizkreis_2_sensoren", "haus_sensoren",
               "kuehlere_raeume", "ohne_ueberwaermung", "haus_frost_sensoren",
               "kuehler_4_raeume", "kuehler_6_raeume", "nur_frost_raeume")


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text())


class PackageTests(unittest.TestCase):
    def test_frontend_1416_versions_and_independent_app_1611(self):
        package = json.loads((ROOT / "package.json").read_text())
        lock = json.loads((ROOT / "package-lock.json").read_text())
        release = json.loads((ROOT / "release.json").read_text())
        validation = json.loads((ROOT / "validation.json").read_text())
        self.assertEqual({package["version"], lock["version"], lock["packages"][""]["version"], release["version"], validation["version"]}, {"1.4.16"})
        self.assertEqual(release["supervisor_app_version"], "1.6.11")
        self.assertEqual(validation["supervisor_app_version"], "1.6.11")
        self.assertEqual(release["component_checkpoint"], validation["component_checkpoint"])
        self.assertEqual(release["component_checkpoint"], "frontend-1.4.16-app-1.6.11")
        self.assertEqual(load("hargassner-dashboard/config.yaml")["version"], "1.6.11")
        self.assertIn("ARG BUILD_VERSION=1.6.11\n", (ROOT / "hargassner-dashboard/Dockerfile").read_text())
        self.assertIn("APP_VERSION = '1.6.11'", (ROOT / "hargassner-dashboard/house_rules.py").read_text())
        self.assertIn('const VERSION = "1.4.16";', (ROOT / "dist/hargassner-thermostat-popup.js").read_text())

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

    def test_app_permissions_are_ha_api_only_without_host_privileges_or_ports(self):
        # From app 1.6.0 the HA API permission serves notifications, the read of
        # the configured room sensors, one Core WebSocket and mqtt.publish. From
        # 1.6.1 it also serves the room page's administrator check and area
        # template. Saving the app's own options uses the Supervisor's
        # /addons/self/ endpoints, open to every app's own token: no hassio_api.
        app = load("hargassner-dashboard/config.yaml")
        self.assertIs(app["ingress"], True)
        self.assertEqual(app["ingress_port"], 8099)
        self.assertIs(app["panel_admin"], False)
        for field in ("host_network", "host_pid", "hassio_api", "hassio_role",
                      "docker_api", "full_access", "privileged", "ports", "map"):
            self.assertFalse(app.get(field), field)
        self.assertIs(app["homeassistant_api"], True)
        sensor_list = ["match(^sensor\\.[a-z0-9_]{1,200}$)"]
        self.assertEqual(app["options"], {"upstream_url": None, "ingress_token": None,
                                           "monitor_enabled": False, "monitor_token": "",
                                           "monitor_poll_interval": 30,
                                           "raumueberwachung_modus": "beobachten",
                                           "heizkreis_1_art": "automatisch", "heizkreis_2_art": "automatisch",
                                           **{key: [] for key in HOUSE_LISTS}, "raum_einstellungen": [],
                                           "notify_watch": False})
        self.assertEqual(app["schema"], {"upstream_url": "url", "upstream_connect_host": "str?", "ingress_token": "password",
                                           "monitor_enabled": "bool", "monitor_token": "password?",
                                           "monitor_poll_interval": "int(15,60)",
                                           "raumueberwachung_modus": "list(beobachten|melden)",
                                           "heizkreis_1_art": "list(automatisch|heizkoerper|fussboden)",
                                           "heizkreis_2_art": "list(automatisch|heizkoerper|fussboden)",
                                           **{key: sensor_list for key in HOUSE_LISTS},
                                           "raum_einstellungen": [{"sensor": sensor_list[0], "kuehler": "float(0,10)",
                                                                   "einfluss_hk1": "int(0,100)", "einfluss_hk2": "int(0,100)"}],
                                           "notify_watch": "bool"})

    def test_house_option_translations_are_complete_and_plain(self):
        keys = {"raumueberwachung_modus", "heizkreis_1_art", "heizkreis_2_art", *HOUSE_LISTS, "raum_einstellungen",
                "notify_watch"}
        for language in ("de", "en"):
            texts = load(f"hargassner-dashboard/translations/{language}.yaml")["configuration"]
            self.assertEqual(set(texts), keys, language)
            for key, entry in texts.items():
                self.assertEqual(set(entry), {"name", "description"}, key)
                for text in entry.values():
                    self.assertTrue(isinstance(text, str) and 0 < len(text) <= 200, key)
                    self.assertNotRegex(text, "[\u2010-\u2015]")   # ordinary hyphens only
        german = load("hargassner-dashboard/translations/de.yaml")["configuration"]
        self.assertEqual(german["heizkreis_1_sensoren"]["name"], "Räume an Heizkreis 1")
        self.assertIn("Nur zur Überwachung", german["heizkreis_1_sensoren"]["description"])
        self.assertIn("Raumsensoren-Seite", german["heizkreis_1_sensoren"]["description"])

    def test_dockerfile_uses_verified_multiarch_index_and_only_monitor_ingress_source(self):
        image = json.loads((ROOT / "validation/base-image.json").read_text())
        dockerfile = (ROOT / "hargassner-dashboard/Dockerfile").read_text()
        self.assertIn(f"FROM {image['repository']}:{image['tag']}@{image['digest']}", dockerfile)
        platforms = {f"{row['os']}/{row['architecture']}" for row in image["platforms"]}
        self.assertTrue({"linux/amd64", "linux/arm64"}.issubset(platforms))
        self.assertEqual(re.findall(r"^COPY\s+(.*)$", dockerfile, re.M),
                         ["ingress_proxy.py app_monitor.py health_alerts.py run_app.py house_rules.py "
                      "house_state.py house_monitor.py ha_websocket.py room_picker.py room_learning.py operation_watchdog.py LICENSE /app/"])
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

    def test_always_published_energy_totals_have_frontend_sections(self):
        groups = load("examples/entity-ui-groups.json")
        extra = groups["always_published"]["entities"]
        self.assertEqual(set(extra), {"sensor:pelletverbrauch_foerderzaehler", "sensor:pelletenergie_schaetzung",
                                      "sensor:brennerstunden", "sensor:brennerstarts", "sensor:heizgradtage"})
        self.assertFalse(set(extra) & set(groups["entities"]))
        script = (ROOT / "dist/hargassner-thermostat-popup.js").read_text()
        block = re.search(r"const DEVICE_ENTITY_GROUPS = Object\.freeze\(\{(.*?)\n\}\);", script, re.S).group(1)
        mapping = dict(re.findall(r'^\s*"([a-z_]+\.[a-z0-9_]+)": "([a-z_0-9]+)",?$', block, re.M))
        for key, value in extra.items():
            self.assertEqual(value["default_entity_id"], key.replace(":", ".", 1))
            self.assertIn(value["group"], groups["groups"])
            self.assertEqual(mapping.get(value["default_entity_id"]), value["group"], key)

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
