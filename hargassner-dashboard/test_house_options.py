"""House-sensor options: strict parser, labels, invalid configuration (synthetic)."""
import json
from pathlib import Path
import tempfile
import unittest

import house_monitor as hm
import house_rules as r


def options(**values):
    return {'upstream_url': 'https://dashboard.example', 'ingress_token': 'i' * 43, **values}


class ParserTests(unittest.TestCase):
    def test_defaults_are_off_and_valid(self):
        parsed = r.parse_house_options(options())
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.mode, 'beobachten')
        self.assertEqual(parsed.option_types, (None, None))
        self.assertEqual(parsed.configured, 0)
        self.assertEqual(parsed.entities, ())

    def test_full_valid_configuration(self):
        parsed = r.parse_house_options(options(
            raumueberwachung_modus='melden', heizkreis_1_art='heizkoerper', heizkreis_2_art='fussboden',
            heizkreis_1_sensoren=['sensor.wohnzimmer', 'sensor.schlafzimmer'], heizkreis_2_sensoren=['sensor.bad'],
            haus_sensoren=['sensor.keller'], kuehlere_raeume=['sensor.schlafzimmer'],
            ohne_ueberwaermung=['sensor.wohnzimmer'], haus_frost_sensoren=['sensor.keller']))
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.mode, 'melden')
        self.assertEqual(parsed.option_types, ('radiator', 'floor'))
        self.assertEqual(parsed.entities, ('sensor.wohnzimmer', 'sensor.schlafzimmer', 'sensor.bad', 'sensor.keller'))
        self.assertEqual(parsed.configured, 4)
        self.assertEqual(parsed.cooler, {'sensor.schlafzimmer'})
        self.assertEqual(parsed.frost, {'sensor.keller'})

    def test_mode_and_type_enums(self):
        for key, value in [('raumueberwachung_modus', 'aus'), ('raumueberwachung_modus', None),
                           ('heizkreis_1_art', 'radiator'), ('heizkreis_2_art', ''), ('heizkreis_1_art', 1)]:
            with self.subTest(key=key, value=value):
                self.assertFalse(r.parse_house_options(options(**{key: value})).valid)
        for value in ('automatisch', 'heizkoerper', 'fussboden'):
            self.assertTrue(r.parse_house_options(options(heizkreis_2_art=value)).valid)

    def test_list_shape_and_entity_pattern(self):
        for value in ['sensor.a', [1], ['light.kitchen'], ['sensor.A'], ['sensor.'], ['sensor.a\n'],
                      ['sensor.' + 'a' * 201], [None], [['sensor.a']], {'sensor.a': 1}]:
            with self.subTest(value=value):
                self.assertFalse(r.parse_house_options(options(heizkreis_1_sensoren=value)).valid)
        self.assertTrue(r.parse_house_options(options(heizkreis_1_sensoren=['sensor.' + 'a' * 200])).valid)

    def test_duplicates_within_and_across_lists(self):
        self.assertFalse(r.parse_house_options(options(heizkreis_1_sensoren=['sensor.a', 'sensor.a'])).valid)
        for other in ('heizkreis_2_sensoren', 'haus_sensoren'):
            with self.subTest(other=other):
                self.assertFalse(r.parse_house_options(options(heizkreis_1_sensoren=['sensor.a'], **{other: ['sensor.a']})).valid)
        self.assertFalse(r.parse_house_options(options(heizkreis_1_sensoren=['sensor.a'], kuehlere_raeume=['sensor.a', 'sensor.a'])).valid)

    def test_total_of_24_entities(self):
        hc1 = [f'sensor.a{i}' for i in range(10)]
        hc2 = [f'sensor.b{i}' for i in range(10)]
        self.assertTrue(r.parse_house_options(options(heizkreis_1_sensoren=hc1, heizkreis_2_sensoren=hc2,
                                                      haus_sensoren=[f'sensor.c{i}' for i in range(4)])).valid)
        parsed = r.parse_house_options(options(heizkreis_1_sensoren=hc1, heizkreis_2_sensoren=hc2,
                                               haus_sensoren=[f'sensor.c{i}' for i in range(5)]))
        self.assertFalse(parsed.valid)
        self.assertEqual(parsed.configured, 24)  # bounded for the report

    def test_subset_rules(self):
        base = dict(heizkreis_1_sensoren=['sensor.a'], heizkreis_2_sensoren=['sensor.b'], haus_sensoren=['sensor.c'])
        self.assertTrue(r.parse_house_options(options(**base, kuehlere_raeume=['sensor.b'], ohne_ueberwaermung=['sensor.a'],
                                                      haus_frost_sensoren=['sensor.c'])).valid)
        for key, value in [('kuehlere_raeume', ['sensor.c']), ('ohne_ueberwaermung', ['sensor.c']),
                           ('kuehlere_raeume', ['sensor.x']), ('haus_frost_sensoren', ['sensor.a']),
                           ('haus_frost_sensoren', ['sensor.x'])]:
            with self.subTest(key=key):
                self.assertFalse(r.parse_house_options(options(**base, **{key: value})).valid)

    def test_invalid_keeps_a_visible_house_part(self):
        parsed = r.parse_house_options(options(raumueberwachung_modus='melden', heizkreis_1_art='x'))
        self.assertFalse(parsed.valid)
        self.assertEqual(parsed.mode, 'melden')
        self.assertEqual(parsed.configured, 1)
        self.assertEqual(r.parse_house_options('not an object').configured, 1)
        with self.assertRaises(ValueError):
            r.HouseEngine(parsed)

    def test_unreadable_options_file_is_invalid_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'options.json'
            path.write_text('{"haus_sensoren": [], "haus_sensoren": []}')
            self.assertFalse(hm.load_house_options(path).valid)
            self.assertFalse(hm.load_house_options(Path(tmp) / 'absent.json').valid)
            path.write_text(json.dumps(options(haus_sensoren=['sensor.keller'])))
            self.assertTrue(hm.load_house_options(path).valid)


class LabelTests(unittest.TestCase):
    def test_sanitising_cutting_and_fallback(self):
        self.assertEqual(r.sanitize_label('Wohnzimmer  Temperatur'), 'Wohnzimmer Temperatur')
        self.assertEqual(r.sanitize_label('Küche <b>&"\'\n'), 'Küche b')
        self.assertEqual(r.sanitize_label('Bad/WC (oben) 1.OG +2'), 'Bad/WC (oben) 1.OG +2')
        self.assertEqual(len(r.sanitize_label('x' * 100)), 40)
        self.assertEqual(r.sanitize_label('<<>>'), '')
        self.assertEqual(r.sanitize_label(None), '')
        self.assertEqual(r.sanitize_label('sensor.wohnzimmer'), '')  # never an entity id

    def test_numbering_and_uniqueness(self):
        entities = ('sensor.a', 'sensor.b', 'sensor.c', 'sensor.d', 'sensor.e')
        labels = r.make_labels(entities, {'sensor.a': 'Raum', 'sensor.b': 'Raum', 'sensor.c': None,
                                          'sensor.d': 'R' * 40, 'sensor.e': 'R' * 40})
        self.assertEqual(labels['sensor.a'], 'Raum')
        self.assertEqual(labels['sensor.b'], 'Raum (2)')
        self.assertEqual(labels['sensor.c'], 'Sensor 3')
        self.assertEqual(labels['sensor.d'], 'R' * 40)
        self.assertEqual(labels['sensor.e'], 'R' * 36 + ' (2)')
        for label in labels.values():
            self.assertTrue(r.LABEL_PATTERN.fullmatch(label))
            self.assertFalse(r.ENTITY_LIKE.fullmatch(label))
        self.assertEqual(len(set(labels.values())), len(labels))

    def test_label_order_is_hc1_hc2_haus(self):
        parsed = r.parse_house_options(options(heizkreis_1_sensoren=['sensor.z'], heizkreis_2_sensoren=['sensor.y'],
                                               haus_sensoren=['sensor.x']))
        engine = r.HouseEngine(parsed)
        self.assertEqual(engine.labels, {'sensor.z': 'Sensor 1', 'sensor.y': 'Sensor 2', 'sensor.x': 'Sensor 3'})


if __name__ == '__main__':
    unittest.main()
