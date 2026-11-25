"""Per-room allowance (app 1.6.8, owner request 2026-11-19; the 1.6.8 lists stay
readable and written as a downgrade mirror by 1.6.9, see test_room_settings).

The owner marked the Wintergarten "darf kühler sein" and still got "zu kalt
(16,5 °C statt 23,0 °C)": the allowance was a fixed 2 K. Each room on a circuit
now has a choice: normal, 2, 4 or 6 K cooler, or frost and sensor only. The
stored options stay readable by 1.6.7, the saved room state of 1.6.7 continues,
and the allowance reaches the VM only when its context says it reads it.
Synthetic data only.
"""
import json
from pathlib import Path
import unittest

import house_rules as r
import house_state as hs
import room_picker as rp
from house_test_support import Harness, circuit, context_document
from test_house_rules import PERSIST
from test_house_transport import World, check_report
from test_room_picker import PickerWorld, row

HERE = Path(__file__).resolve().parent
W, A, B, C = 'sensor.wintergarten', 'sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad'
BOOT = '0123abcd-0000-4000-8000-000000000001'
# The 1.6.7 rules digest: 1.6.8 adds no rule constant, so a saved state of
# 1.6.7 is not "state_incompatible" after the update.
DIGEST_167 = 'a844965c1e50f0dffc0f23ab0aa6bdf61e6b87e16d273aa275e353cf9df41086'
OLD_FINDING_KEYS = {'code', 'level', 'label', 'circuit', 'held_s', 'room', 'target', 'count'}
LEVELS = {'0': {}, '2': {'kuehlere_raeume': [W]},
          '4': {'kuehlere_raeume': [W], 'kuehler_4_raeume': [W]},
          '6': {'kuehlere_raeume': [W], 'kuehler_6_raeume': [W]},
          'frost': {'kuehlere_raeume': [W], 'nur_frost_raeume': [W]}}


def wintergarten(level, value, *others):
    """The owner's case: a radiator circuit with a 23 °C target."""
    options = {'heizkreis_1_sensoren': [W, *others], **LEVELS[level]}
    target = circuit(target=23.0, day_target=23.0)
    return Harness(options, hc1=target, values={W: value, **{e: 23.0 for e in others}})


def cold_after(h, limit=400 + PERSIST):
    return h.first('room_cold', limit)


class WintergartenTests(unittest.TestCase):
    """6.5 K below a 23 °C target."""

    def test_normal_fires(self):
        h = wintergarten('0', 16.5)
        self.assertIsNotNone(cold_after(h))
        self.assertNotIn('allowance', h.rows('room_cold')[0])

    def test_two_kelvin_still_fires_and_reports_its_allowance(self):
        h = wintergarten('2', 16.5)
        self.assertIsNotNone(cold_after(h))
        row = h.rows('room_cold')[0]
        self.assertEqual((row['room'], row['target'], row['allowance']), (16.5, 23.0, 2.0))

    def test_six_kelvin_fires_only_at_or_below_15_5(self):
        for value in (16.5, 15.6):
            with self.subTest(value=value):
                h = wintergarten('6', value)
                h.run(2 * (400 + PERSIST))
                self.assertFalse(h.codes() & {'room_cold', 'limit_cold', 'no_response'})
        h = wintergarten('6', 15.0)
        self.assertIsNotNone(cold_after(h))
        row = h.rows('room_cold')[0]
        self.assertEqual((row['room'], row['target'], row['allowance']), (15.0, 23.0, 6.0))

    def test_four_kelvin(self):
        h = wintergarten('4', 17.6)    # 23 - 4 - 17.6 = 1.4
        h.run(2 * (400 + PERSIST))
        self.assertNotIn('room_cold', h.codes())
        h = wintergarten('4', 17.5)
        self.assertIsNotNone(cold_after(h))
        self.assertEqual(h.rows('room_cold')[0]['allowance'], 4.0)

    def test_frost_only_never_raises_a_heating_rule_but_keeps_frost_and_sensor(self):
        h = wintergarten('frost', 10.0)
        h.run(3 * 1440)
        self.assertEqual(h.codes(), set())
        self.assertNotIn(W, h.engine.cold_dwell)
        self.assertNotIn(W, h.engine.windows)
        # Overheated by the sun while the circuit heats: no room_hot either.
        for minute in range(180):
            h.values[W] = round(10.0 + minute / 9, 2)
            h.step()
        h.run(240)
        self.assertEqual(h.codes(), set())
        # Frost and the sensor are still checked.
        for minute in range(300):
            h.values[W] = round(30.0 - minute / 11.5, 2)
            h.step()
        self.assertIsNotNone(h.first('room_frost', 30))
        h.values[W] = 'unavailable'
        self.assertIsNotNone(h.first('sensor_unavailable', 60))

    def test_limit_cold_uses_the_allowance(self):
        # Heating limit off in the day phase, outdoor mean 3 °C: 2.5 K below the
        # allowed limit is needed.
        for level, value, fires in (('0', 20.5, True), ('2', 20.5, False), ('2', 18.5, True),
                                    ('6', 16.5, False), ('frost', 5.5, False)):
            with self.subTest(level=level, value=value):
                h = wintergarten(level, value)
                h.set_circuit('hc1', status=4, pump=False)
                found = h.first('limit_cold', 600) is not None
                self.assertEqual(found, fires)
                if fires and level != '0':
                    self.assertEqual(h.rows('limit_cold')[0]['allowance'], float(level))


class CircuitColdTests(unittest.TestCase):
    """circuit_cold over the reference rooms (normal and 2 K)."""

    def harness(self, values, **lists):
        options = {'heizkreis_1_sensoren': list(values), **lists}
        return Harness(options, values=values)

    def test_a_frost_only_room_does_not_hide_a_failed_circuit(self):
        # The sunroom stays warm by the sun; the two heated rooms are 4 K below.
        h = self.harness({W: 22.0, A: 17.0, B: 17.0}, kuehlere_raeume=[W], nur_frost_raeume=[W])
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        row = h.rows('circuit_cold')[0]
        self.assertEqual((row['count'], row['room']), (2, 17.0))

    def test_a_frost_only_room_does_not_raise_it(self):
        # Wintergarten cold on purpose, one room cold, one warm: no Störung.
        h = self.harness({W: 10.0, A: 17.0, B: 21.0}, kuehlere_raeume=[W], nur_frost_raeume=[W])
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())
        # Frost-only plus one heated room: one room is no circuit.
        h = self.harness({W: 10.0, A: 17.0}, kuehlere_raeume=[W], nur_frost_raeume=[W])
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())
        self.assertIn('room_cold', h.codes())

    def test_a_six_kelvin_room_neither_raises_nor_hides_it(self):
        # At its allowed temperature (17 °C at 21 °C) it does not hold off the
        # Störung of the two reference rooms ...
        h = self.harness({W: 21.0, A: 17.0, B: 17.0}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        self.assertEqual(h.rows('circuit_cold')[0]['count'], 2)
        # ... and, far below the target, it does not raise one with a single cold room.
        h = self.harness({W: 12.0, A: 17.0, B: 21.0}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())

    def test_with_fewer_than_two_reference_rooms_allowance_rooms_count_against_their_limit(self):
        # One reference room: the 6 K room counts, against 21 - 6 = 15 °C.
        h = self.harness({W: 14.0, A: 17.0}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())       # 14 is only 1 K below its limit
        h = self.harness({W: 11.5, A: 17.0}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        self.assertEqual(h.rows('circuit_cold')[0]['count'], 2)

    def test_a_reference_sensor_dropping_out_pauses_instead_of_handing_over(self):
        # Raised over A and B; B's sensor fails: the sunny 6 K room must not end it.
        h = self.harness({W: 22.0, A: 17.0, B: 17.0}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        self.assertIsNotNone(h.first('circuit_cold', 600))
        h.values[B] = 'unavailable'
        h.run(120)
        self.assertIn('circuit_cold', h.codes())

    def test_the_onset_names_the_room_furthest_below_its_own_limit(self):
        # One reference room: A at 17 (4 K below 21), W (6 K) at 14 (only 1 K
        # below its limit 15) would be the lowest value but is not the coldest.
        h = self.harness({W: 11.0, A: 17.0}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        self.assertIsNotNone(h.first('circuit_cold', 600))
        self.assertEqual(h.rows("circuit_cold")[0]["room"], 11.0)        # W 4 K below its limit, A 4 K: a tie takes the lower value
        h = self.harness({W: 10.0, A: 17.5}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        self.assertIsNotNone(h.first('circuit_cold', 600))
        self.assertEqual(h.rows('circuit_cold')[0]['room'], 10.0)        # W: 5 K below its limit, A: 3.5 K
        h = self.harness({W: 12.0, A: 15.0}, kuehlere_raeume=[W], kuehler_6_raeume=[W])
        self.assertIsNotNone(h.first('circuit_cold', 600))
        self.assertEqual(h.rows('circuit_cold')[0]['room'], 15.0)        # A: 6 K below, W: 3 K below its limit

    def test_two_kelvin_rooms_stay_reference_rooms_as_in_167(self):
        h = self.harness({A: 17.0, B: 15.0}, kuehlere_raeume=[B])   # B: 21 - 2 - 15 = 4
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        h = self.harness({A: 17.0, B: 17.0}, kuehlere_raeume=[B])   # B only 2 K below its limit
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())


class OptionsTests(unittest.TestCase):
    def test_167_options_load_as_two_kelvin(self):
        stored = {'heizkreis_1_sensoren': [W, A], 'kuehlere_raeume': [W]}   # no new keys
        options = r.parse_house_options(stored)
        self.assertTrue(options.valid)
        self.assertEqual((options.level(W), options.level(A)), (2.0, 0.0))
        self.assertEqual(rp.allowances_of(stored), {W: '2'})

    def test_levels_and_precedence(self):
        for level, expected in (('0', 0.0), ('2', 2.0), ('4', 4.0), ('6', 6.0), ('frost', r.FROST_ONLY)):
            options = r.parse_house_options({'heizkreis_1_sensoren': [W], **LEVELS[level]})
            self.assertTrue(options.valid, level)
            self.assertEqual(options.level(W), expected)
            self.assertEqual(options.allowance(W), 0.0 if level == 'frost' else expected)
            self.assertEqual(options.heated('hc1'), () if level == 'frost' else (W,))
        # A larger choice without kuehlere_raeume (written by hand) still counts.
        self.assertEqual(r.parse_house_options({'heizkreis_1_sensoren': [W], 'kuehler_6_raeume': [W]}).level(W), 6.0)

    def test_invalid_combinations(self):
        for extra in ({'kuehler_4_raeume': [W], 'kuehler_6_raeume': [W]},       # two larger choices
                      {'kuehler_6_raeume': [W], 'nur_frost_raeume': [W]},
                      {'kuehler_4_raeume': [C]},                               # not on a circuit
                      {'nur_frost_raeume': [C]},
                      {'kuehler_6_raeume': [W, W]},
                      {'kuehler_6_raeume': 'sensor.wintergarten'}):
            with self.subTest(extra=extra):
                options = {'heizkreis_1_sensoren': [W], 'haus_sensoren': [C], **extra}
                self.assertFalse(r.parse_house_options(options).valid)

    def test_config_schema_lists_the_new_options_with_empty_defaults(self):
        with open(HERE / 'config.yaml', encoding='utf-8') as stream:
            text = stream.read()
        for key in ('kuehler_4_raeume', 'kuehler_6_raeume', 'nur_frost_raeume'):
            self.assertIn(f'  {key}: []\n', text)
            self.assertIn(f'  {key}: ["match(^sensor\\\\.[a-z0-9_]{{1,200}}$)"]\n', text)
            for language in ('en', 'de'):
                with open(HERE / 'translations' / f'{language}.yaml', encoding='utf-8') as stream:
                    self.assertIn(f'  {key}:\n    name: ', stream.read())


class PickerTests(unittest.TestCase):
    # The five-choice select of 1.6.8 is replaced by number fields in 1.6.9
    # (test_room_settings.SaveTests.test_page_shows_number_fields_with_units_and_defaults).

    def test_each_choice_is_saved_with_kuehlere_raeume_for_older_versions(self):
        world = PickerWorld()
        status, document = world.save([row('sensor.wohnzimmer', 'heizkreis_1', kuehler='6'),
                                       row('sensor.kueche', 'heizkreis_1', kuehler='4'),
                                       row('sensor.bad', 'heizkreis_2', kuehler='frost')])
        self.assertEqual((status, document['ok']), (200, True), document)
        saved = world.posted[-1]
        self.assertEqual(sorted(saved['kuehlere_raeume']), sorted(['sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad']))
        self.assertEqual(saved['kuehler_4_raeume'], ['sensor.kueche'])
        self.assertEqual(saved['kuehler_6_raeume'], ['sensor.wohnzimmer'])
        self.assertEqual(saved['nur_frost_raeume'], ['sensor.bad'])
        options = r.parse_house_options(saved)
        self.assertEqual([options.level(e) for e in ('sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad')],
                         [6.0, 4.0, r.FROST_ONLY])
        # What 1.6.7 reads of it (it drops the unknown keys): every one of them 2 K.
        old = {key: value for key, value in saved.items()
               if key not in ('kuehler_4_raeume', 'kuehler_6_raeume', 'nur_frost_raeume')}
        self.assertEqual(r.parse_house_options(old).cooler,
                         frozenset({'sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad'}))
        # Back to normal clears every list.
        status, _document = world.save([row('sensor.wohnzimmer', 'heizkreis_1'), row('sensor.kueche', 'heizkreis_1'),
                                        row('sensor.bad', 'heizkreis_2')])
        self.assertEqual(status, 200)
        self.assertEqual({key: world.posted[-1][key] for key in ('kuehlere_raeume', 'kuehler_4_raeume',
                                                                 'kuehler_6_raeume', 'nur_frost_raeume')},
                         {'kuehlere_raeume': [], 'kuehler_4_raeume': [], 'kuehler_6_raeume': [],
                          'nur_frost_raeume': []})

    # Refused values of the 1.6.9 fields: test_room_settings.SaveTests.test_refused_values.

    def test_a_167_page_body_is_refused_without_saving(self):
        world = PickerWorld()
        body = row('sensor.kueche', 'heizkreis_1')
        body['kuehler'] = True        # the 1.6.7 checkbox
        status, document = world.save([body])
        self.assertEqual(status, 400)
        self.assertEqual(world.posted, [])


class StateTests(unittest.TestCase):
    def test_rules_digest_is_that_of_167(self):
        self.assertEqual(hs.rules_digest(), DIGEST_167)

    def run_and_export(self, options, values):
        h = Harness(options, values=values)
        h.run(300)
        return h, hs.export(h.engine, mono=h.now, wall=h.wall, boot=BOOT)

    def test_normal_and_two_kelvin_keep_the_167_fingerprints(self):
        options = {'heizkreis_1_sensoren': [W, A], 'kuehlere_raeume': [W]}
        _h, document = self.run_and_export(options, {W: 20.0, A: 21.0})
        engine = document['engine']
        # [role, kind, darf kühler sein, ohne Überwärmung, frost] as in 1.6.7.
        self.assertEqual(engine['rooms'][W]['fingerprint'], ['hc1', None, True, False, False])
        self.assertEqual(engine['rooms'][A]['fingerprint'], ['hc1', None, False, False, False])
        self.assertEqual(engine['circuits']['hc1']['fingerprint'], [None, [[W, True, False], [A, False, False]]])

    def test_a_167_state_continues_and_a_new_choice_restarts_only_its_room(self):
        options = {'heizkreis_1_sensoren': [W, A], 'kuehlere_raeume': [W]}
        h, document = self.run_and_export(options, {W: 20.0, A: 21.0})
        document = json.loads(json.dumps(document))
        engine, outcome = hs.restore(r.parse_house_options(options), document, mono=h.now, wall=h.wall, boot=BOOT)
        self.assertEqual(outcome, 'state_restored')
        changed = dict(options, kuehler_6_raeume=[W])
        engine, outcome = hs.restore(r.parse_house_options(changed), document, mono=h.now, wall=h.wall, boot=BOOT)
        self.assertEqual(outcome, 'state_partly_restored')
        self.assertTrue(engine.sensors[A].history)
        self.assertFalse(engine.sensors[W].history)

    def test_new_choices_round_trip(self):
        for level in ('4', '6', 'frost'):
            with self.subTest(level=level):
                options = {'heizkreis_1_sensoren': [W, A], **LEVELS[level]}
                h, document = self.run_and_export(options, {W: 15.0, A: 21.0})
                mark = {'4': 4, '6': 6, 'frost': 'frost'}[level]
                self.assertEqual(document['engine']['rooms'][W]['fingerprint'][2], mark)
                engine, outcome = hs.restore(r.parse_house_options(options), json.loads(json.dumps(document)),
                                             mono=h.now, wall=h.wall, boot=BOOT)
                self.assertEqual(outcome, 'state_restored')
        # Any other value in a circuit fingerprint is refused: a fresh engine,
        # no crash (what 1.6.7 does with 4, 6 or "frost" after a rollback).
        options = {'heizkreis_1_sensoren': [W, A], **LEVELS['6']}
        h, document = self.run_and_export(options, {W: 15.0, A: 21.0})
        document['engine']['circuits']['hc1']['fingerprint'][1][0][1] = 5
        body = {key: value for key, value in document.items() if key != 'sha256'}
        document['sha256'] = hs.checksum(body)
        _engine, outcome = hs.restore(r.parse_house_options(options), document, mono=h.now, wall=h.wall, boot=BOOT)
        self.assertEqual(outcome, 'state_unreadable')


class WireTests(unittest.TestCase):
    """What reaches the VM: the allowance only with a context major 2."""

    def world(self, major):
        world = World(options={'heizkreis_1_sensoren': [W, A], **LEVELS['2']}, values={W: 16.5, A: 23.0})

        def context(topic=None, **top):
            world.sequence += 1
            document = context_document(world.sequence, hc1=circuit(target=23.0, day_target=23.0), **top)
            document['schema'] = f'harg-house-context/{major}'
            world.inbox.append((topic or 'hargassner/gateway_1/house/context', json.dumps(document), False))
            return document
        world.context = context
        return world

    def cold_report(self, major):
        world = self.world(major)
        for _hour in range(40):
            world.run(3600, step=60)
            reports = [rep for rep in world.reports() if any(f['code'] == 'room_cold' for f in rep['findings'])]
            if reports:
                return world, reports[-1]
        self.fail('no room_cold')

    def test_against_an_older_adapter_the_report_keeps_the_167_keys(self):
        world, report = self.cold_report(1)
        check_report(self, world.published[-1][1])
        self.assertEqual(report['context_majors'], [1, 2, 3])
        for finding in report['findings']:
            self.assertEqual(set(finding), OLD_FINDING_KEYS)

    def test_against_major_2_every_finding_carries_allowance(self):
        world, report = self.cold_report(2)
        check_report(self, world.published[-1][1])
        cold = [f for f in report['findings'] if f['code'] == 'room_cold']
        self.assertEqual([(f['room'], f['target'], f['allowance']) for f in cold], [(16.5, 23.0, 2.0)])
        self.assertTrue(all(set(f) == OLD_FINDING_KEYS | {'allowance'} for f in report['findings']))

    def test_build_report_strips_the_key_without_a_bound_context(self):
        row = {'code': 'room_cold', 'level': 'Hinweis', 'label': 'Wintergarten', 'circuit': 'hc1',
               'held_s': 60, 'room': 16.5, 'target': 23.0, 'count': None, 'allowance': 6.0}
        common = dict(instance='0' * 32, seq=1, mode='melden', monitor='ok', reason=None,
                      sensors={'configured': 1, 'usable': 1, 'unavailable': 0},
                      circuits={name: {'type': None, 'type_source': None, 'sensors': 0, 'judged': 'unknown',
                                       'why': 'context'} for name in r.CIRCUITS}, findings=[row])
        for context_id, major, expected in ((None, 2, None), ('ab' * 8 + ':1', 1, None),
                                            ('ab' * 8 + ':1', 2, 6.0), ('ab' * 8 + ':1', None, None)):
            report, _payload = r.build_report(context_id=context_id, context_major=major, **common)
            finding = report['findings'][0]
            if expected is None:
                self.assertEqual(set(finding), OLD_FINDING_KEYS)
            else:
                self.assertEqual(finding['allowance'], expected)
        self.assertIn('allowance', row)        # the engine's row is not changed


if __name__ == '__main__':
    unittest.main()
