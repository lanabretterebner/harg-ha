"""Free room settings (app 1.6.9, owner request 2026-11-19): an allowance of
0 to 10 K per room and an influence of 0 to 100 % per circuit, so a room can
be on both circuits. Migration from 1.6.8, the allowance math, the influence
semantics including the safety case (a real circuit failure is still found
through the rooms with high influence), saved state and downgrades.
Synthetic data only.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import house_rules as r
import house_state as hs
import room_picker as rp
from house_test_support import Harness, circuit
from test_house_allowance import DIGEST_167
from test_house_rules import PERSIST, WINDOW, day, flat, setback
from test_room_picker import PickerWorld, row

W, A, B, C = 'sensor.wintergarten', 'sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad'


def entry(entity, allowance, hk1, hk2):
    return {'sensor': entity, 'kuehler': allowance, 'einfluss_hk1': hk1, 'einfluss_hk2': hk2}


def parsed(**options):
    return r.parse_house_options(options)


class OptionTests(unittest.TestCase):
    def test_free_allowance_and_influence_are_parsed(self):
        options = parsed(heizkreis_1_sensoren=[W, A], heizkreis_2_sensoren=[B],
                         raum_einstellungen=[entry(W, 3.5, 60, 40), entry(B, 10, 0, 100)])
        self.assertTrue(options.valid)
        self.assertEqual(options.allowance(W), 3.5)
        self.assertEqual((options.influence(W, 'hc1'), options.influence(W, 'hc2')), (60, 40))
        self.assertEqual(options.total(W), 100)
        self.assertEqual(options.allowance(B), 10.0)
        # A room without an entry: 0 K and 100 % on its own circuit, as 1.6.8.
        self.assertEqual((options.allowance(A), options.influence(A, 'hc1'), options.influence(A, 'hc2')), (0.0, 100, 0))
        self.assertTrue(options.default_setting(A))
        self.assertFalse(options.default_setting(W))

    def test_invalid_entries_make_the_configuration_invalid(self):
        good = {'heizkreis_1_sensoren': [W, A], 'haus_sensoren': [C]}
        for bad in ([entry(W, 2.25, 100, 0)], [entry(W, 10.5, 100, 0)], [entry(W, -0.5, 100, 0)],
                    [entry(W, True, 100, 0)], [entry(W, '2', 100, 0)], [entry(W, 2, 101, 0)],
                    [entry(W, 2, 50.5, 0)], [entry(W, 2, 40, 60)],            # own circuit below the other
                    [entry(C, 2, 0, 0)],                                       # not on a circuit
                    [entry('sensor.fremd', 2, 100, 0)], [entry(W, 2, 100, 0), entry(W, 3, 100, 0)],
                    [dict(entry(W, 2, 100, 0), extra=1)], [{'sensor': W}], 'sensor.wintergarten',
                    [entry(W, float('nan'), 100, 0)]):
            with self.subTest(bad=bad):
                self.assertFalse(r.parse_house_options(dict(good, raum_einstellungen=bad)).valid)
        self.assertTrue(r.parse_house_options(dict(good, raum_einstellungen=[entry(W, 2.0, 100.0, 0)])).valid)

    def test_an_entry_overrides_the_168_lists(self):
        options = parsed(heizkreis_1_sensoren=[W], kuehlere_raeume=[W], kuehler_6_raeume=[W],
                         raum_einstellungen=[entry(W, 1.5, 100, 0)])
        self.assertEqual(options.allowance(W), 1.5)
        # The legacy lists are still checked (1.6.8 must read them after a downgrade).
        self.assertFalse(parsed(heizkreis_1_sensoren=[W], kuehler_4_raeume=[W], kuehler_6_raeume=[W],
                                raum_einstellungen=[entry(W, 1.5, 100, 0)]).valid)

    def test_168_options_are_migrated(self):
        stored = {'heizkreis_1_sensoren': [W, A, B], 'heizkreis_2_sensoren': [C],
                  'kuehlere_raeume': [W, A, B, C], 'kuehler_4_raeume': [A], 'kuehler_6_raeume': [B],
                  'nur_frost_raeume': [C]}
        self.assertEqual(rp.room_values(stored), {W: (2.0, 100, 0), A: (4.0, 100, 0), B: (6.0, 100, 0),
                                                  C: (0.0, 0, 0)})
        options = r.parse_house_options(stored)
        self.assertEqual([options.allowance(e) for e in (W, A, B, C)], [2.0, 4.0, 6.0, 0.0])
        self.assertEqual(options.total(C), 0)                 # nur Frost: no influence
        self.assertEqual(options.heated('hc2'), ())
        # A 1.6.7 kuehlere_raeume list alone is still 2 K.
        self.assertEqual(rp.room_values({'heizkreis_1_sensoren': [W], 'kuehlere_raeume': [W]}), {W: (2.0, 100, 0)})


class SaveTests(unittest.TestCase):
    def test_free_values_are_saved_with_168_mirror_lists(self):
        world = PickerWorld()
        status, document = world.save([row('sensor.wohnzimmer', 'heizkreis_1', kuehler=3.5),
                                       row('sensor.kueche', 'heizkreis_1', kuehler=5, hk1=70, hk2=30),
                                       row('sensor.bad', 'heizkreis_2', kuehler=8.5),
                                       row('sensor.keller', 'heizkreis_2', kuehler=1.5, hk1=20, hk2=80)])
        self.assertEqual((status, document['ok']), (200, True), document)
        saved = world.posted[-1]
        self.assertEqual(saved['raum_einstellungen'], [
            entry('sensor.wohnzimmer', 3.5, 100, 0), entry('sensor.kueche', 5.0, 70, 30),
            entry('sensor.bad', 8.5, 0, 100), entry('sensor.keller', 1.5, 20, 80)])
        # Downgrade mirror: the allowance rounded down to 0/2/4/6 K.
        self.assertEqual(saved['kuehlere_raeume'], ['sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad'])
        self.assertEqual(saved['kuehler_4_raeume'], ['sensor.kueche'])
        self.assertEqual(saved['kuehler_6_raeume'], ['sensor.bad'])
        self.assertEqual(saved['nur_frost_raeume'], [])
        options = r.parse_house_options(saved)
        self.assertTrue(options.valid)
        self.assertEqual(options.allowance('sensor.keller'), 1.5)
        self.assertEqual(options.cross('hc2'), ())                                   # 30 % < member
        self.assertEqual(options.cross('hc1'), ())                                   # 20 %
        # What 1.6.8 reads after a downgrade (unknown keys dropped).
        old = {key: value for key, value in saved.items() if key != 'raum_einstellungen'}
        self.assertEqual([r.parse_house_options(old).level(e) for e in
                          ('sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad', 'sensor.keller')],
                         [2.0, 4.0, 6.0, 0.0])

    def test_168_choices_need_no_entry(self):
        world = PickerWorld()
        status, _document = world.save([row('sensor.wohnzimmer', 'heizkreis_1', kuehler='6'),
                                         row('sensor.kueche', 'heizkreis_1', kuehler='frost'),
                                         row('sensor.bad', 'heizkreis_2')])
        self.assertEqual(status, 200)
        saved = world.posted[-1]
        self.assertEqual(saved['raum_einstellungen'], [])
        self.assertEqual((saved['kuehler_6_raeume'], saved['nur_frost_raeume']), (['sensor.wohnzimmer'], ['sensor.kueche']))

    def test_refused_values(self):
        world = PickerWorld()
        for bad, text in ((row('sensor.kueche', 'heizkreis_1', kuehler=10.5), 'zwischen 0 und 10 °C'),
                          (row('sensor.kueche', 'heizkreis_1', kuehler=2.3), 'zwischen 0 und 10 °C'),
                          (row('sensor.kueche', 'heizkreis_1', kuehler='3'), 'zwischen 0 und 10 °C'),
                          (row('sensor.kueche', 'heizkreis_1', kuehler=None), 'zwischen 0 und 10 °C'),
                          (row('sensor.kueche', 'heizkreis_1', hk1=120), 'ganze Zahl von 0 bis 100'),
                          (row('sensor.kueche', 'heizkreis_1', hk1=None), 'ganze Zahl von 0 bis 100'),
                          (row('sensor.kueche', 'heizkreis_1', hk1=30, hk2=60), 'eigenen Heizkreises'),
                          (row('sensor.keller', 'weitere', kuehler=2), 'nur für Räume an einem Heizkreis'),
                          (row('sensor.keller', 'weitere', hk1=50), 'nur für Räume an einem Heizkreis')):
            with self.subTest(bad=bad):
                status, document = world.save([bad])
                self.assertEqual(status, 400, document)
                self.assertIn(text, document['message'])
        self.assertEqual(world.posted, [])

    def test_page_shows_number_fields_with_units_and_defaults(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.wohnzimmer'], 'heizkreis_2_sensoren': ['sensor.bad'],
                             'raum_einstellungen': [entry('sensor.wohnzimmer', 3.5, 80, 20)]})
        page = world.page()

        def block(entity):
            part = page[page.index(f'data-entity="{entity}"'):]
            return part[:part.index('</fieldset>')]
        living = block('sensor.wohnzimmer')
        self.assertIn('Darf kühler sein um <input type="number" name="kuehler" min="0" max="10" step="0.5" '
                      'inputmode="decimal" value="3.5"> °C', living)
        self.assertIn('Einfluss Heizkreis 1 <input type="number" name="einfluss_hk1" min="0" max="100" step="5" '
                      'inputmode="numeric" value="80"> %', living)
        self.assertIn('name="einfluss_hk2" min="0" max="100" step="5" inputmode="numeric" value="20"> %', living)
        self.assertIn('name="einfluss_hk2" min="0" max="100" step="5" inputmode="numeric" value="100"', block('sensor.bad'))
        self.assertIn('Unter 30 % wird der Raum', page)
        self.assertNotRegex(page, '[‐-―]')


class AllowanceTests(unittest.TestCase):
    """room_cold with a free allowance: limit = target - allowance."""

    def wintergarten(self, allowance, value, **settings):
        options = {'heizkreis_1_sensoren': [W, A],
                   'raum_einstellungen': [entry(W, allowance, settings.get('hk1', 100), settings.get('hk2', 0))]}
        return Harness(options, hc1=circuit(target=23.0, day_target=23.0), values={W: value, A: 23.0})

    def test_half_kelvin_steps(self):
        # 23 - 3.5 = 19.5; room_cold needs 1.5 K below the limit: 18.0.
        h = self.wintergarten(3.5, 18.1)
        h.run(2 * (400 + PERSIST))
        self.assertNotIn('room_cold', h.codes())
        h = self.wintergarten(3.5, 18.0)
        self.assertIsNotNone(h.first('room_cold', 400 + PERSIST))
        row = h.rows('room_cold')[0]
        self.assertEqual((row['room'], row['target'], row['allowance']), (18.0, 23.0, 3.5))
        report, _payload = r.build_report(instance='0' * 32, seq=1, context_id='0' * 16 + ':1', mode='melden',
                                          monitor='ok', reason=None, sensors=h.engine.sensors_report(h.now),
                                          circuits=h.engine.circuits_report(), findings=h.findings, context_major=2)
        self.assertEqual([f['allowance'] for f in report['findings'] if f['code'] == 'room_cold'], [3.5])

    def test_ten_kelvin(self):
        h = self.wintergarten(10, 11.6)
        h.run(2 * (400 + PERSIST))
        self.assertNotIn('room_cold', h.codes())
        h = self.wintergarten(10, 11.5)
        self.assertIsNotNone(h.first('room_cold', 400 + PERSIST))

    def test_weak_total_influence_is_frost_and_sensor_only_for_cold(self):
        h = self.wintergarten(0, 12.0, hk1=25)
        h.run(2 * (400 + PERSIST))
        self.assertFalse(h.codes() & {'room_cold', 'limit_cold', 'no_response'})
        h = self.wintergarten(0, 12.0, hk1=30)
        self.assertIsNotNone(h.first('room_cold', 400 + PERSIST))
        h = self.wintergarten(0, 4.0, hk1=0)
        self.assertIsNotNone(h.first('room_frost', 30))


class CircuitInfluenceTests(unittest.TestCase):
    """circuit_cold weighted by influence; the safety case."""

    def harness(self, values, settings, hc2_values=None):
        options = {'heizkreis_1_sensoren': list(values), 'heizkreis_2_sensoren': list(hc2_values or {}),
                   'raum_einstellungen': settings}
        return Harness(options, values={**values, **(hc2_values or {})})

    def test_failure_is_found_through_strong_rooms_while_a_weak_room_stays_warm(self):
        # Pump failed: A and B (100 %) 4 K below; W (60 %) kept warm by the sun.
        h = self.harness({W: 21.5, A: 17.0, B: 17.0}, [entry(W, 0, 60, 0)])
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        self.assertEqual(h.rows('circuit_cold')[0]['count'], 2)

    def test_a_weak_room_alone_never_raises_it(self):
        h = self.harness({W: 12.0, A: 17.0, B: 21.0}, [entry(W, 0, 60, 0)])
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())

    def test_partly_heated_rooms_count_with_scaled_limits(self):
        # No two strong rooms: both 60 % rooms decide, each 3 K x 0.6 = 1.8 K below.
        settings = [entry(A, 0, 60, 0), entry(B, 0, 60, 0)]
        h = self.harness({A: 19.3, B: 19.3}, settings)     # 1.7 K below
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())
        h = self.harness({A: 19.2, B: 19.0}, settings)     # 1.8 and 2.0 K
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        # Below member influence (50 %) a room does not count at all.
        h = self.harness({A: 17.0, B: 17.0}, [entry(A, 0, 45, 0), entry(B, 0, 45, 0)])
        h.run(1440)
        self.assertNotIn('circuit_cold', h.codes())

    def test_a_room_on_both_circuits_counts_for_the_other_one(self):
        # W is in Heizkreis 1's group with 50 % there and 50 % on Heizkreis 2;
        # Heizkreis 2 fails: its room C and W are cold, the fault names 2 rooms.
        h = self.harness({W: 18.0}, [entry(W, 0, 50, 50)], hc2_values={C: 17.0})
        h.set_circuit('hc2', type='radiator')
        h.run(1 + 120 + 180)
        rows = [row for row in h.findings if row['code'] == 'circuit_cold']
        self.assertEqual([(row['circuit'], row['count']) for row in rows], [('hc2', 2)])
        self.assertIn(W, h.options.cross('hc2'))
        self.assertNotIn(W, h.options.cross('hc1'))

    def test_a_room_the_other_circuit_heats_never_holds_off_the_fault(self):
        # Review H1: Heizkreis 1 fails (A and B 4 K below). C is on Heizkreis 2
        # with 80 % from each circuit and stays warm through Heizkreis 2; an own
        # room W with 80/80 stays warm too. Neither may hold off the Störung.
        h = self.harness({A: 17.0, B: 17.0}, [entry(C, 0, 80, 80)], hc2_values={C: 21.0})
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        h = self.harness({A: 17.0, B: 17.0, W: 21.0}, [entry(W, 0, 80, 80)])
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        self.assertEqual(h.rows('circuit_cold')[0]['count'], 2)

    def test_defaults_are_exactly_168(self):
        # Same findings and minutes with and without explicit default entries.
        plain = self.harness({A: 17.0, B: 17.0}, [])
        explicit = self.harness({A: 17.0, B: 17.0}, [entry(A, 0, 100, 0), entry(B, 2, 100, 0)])
        explicit_b = Harness({'heizkreis_1_sensoren': [A, B], 'kuehlere_raeume': [B]}, values={A: 17.0, B: 17.0})
        self.assertEqual(explicit.first('circuit_cold', 600), explicit_b.first('circuit_cold', 600))
        self.assertEqual(plain.first('circuit_cold', 600), 1 + 120 + 180)
        self.assertEqual(hs.rules_digest(), DIGEST_167)


class ResponseInfluenceTests(unittest.TestCase):
    """no_response: the expected rise scales with influence; a weakly heated
    room's rise never clears the circuit's failed starts."""

    def days(self, h, days, levels, rising=None, rise=0.0):
        """Daily starts like test_house_rules.failing_days; `rising` rooms rise
        `rise` K over the response window, the others stay flat."""
        window = WINDOW['radiator']
        setback(h, 'hc1')
        flat(h, 30, levels)
        for number in range(days):
            if number:
                flat(h, 16 * 60 - window, levels)
                setback(h, 'hc1')
                flat(h, 8 * 60, levels)
            day(h, 'hc1')
            for minute in range(window):
                for entity, level in levels.items():
                    extra = rise * minute / window if entity in (rising or ()) else 0.0
                    h.values[entity] = round(level + extra + (0.1 if h.steps % 2 else 0.0), 2)
                h.step()

    def test_expected_rise_is_proportional_to_influence(self):
        sensor = r.Sensor('sensor.x', 'hc1', 1)
        self.assertEqual(r.expected_rise(sensor, 100), sensor.min_rise())
        self.assertEqual(r.expected_rise(sensor, 50), 0.2)       # 0.15 below the noise floor
        self.assertAlmostEqual(r.expected_rise(sensor, 80), 0.24)

    def test_a_half_heated_room_passes_with_half_the_rise(self):
        levels = {A: 18.5, B: 18.5}
        h = Harness({'heizkreis_1_sensoren': [A, B], 'raum_einstellungen': [entry(A, 0, 50, 0)]},
                    values=dict(levels))
        self.days(h, 3, levels, rising=(A,), rise=0.35)
        # Labels are by position in the harness: A 'Sensor 1', B 'Sensor 2'.
        self.assertFalse(h.has('no_response', label='Sensor 1'))
        self.assertTrue(h.has('no_response', label='Sensor 2'))     # flat at 100 %
        h = Harness({'heizkreis_1_sensoren': [A, B]}, values=dict(levels))
        self.days(h, 3, levels, rising=(A,), rise=0.35)
        self.assertTrue(h.has('no_response', label='Sensor 1'))  # about 0.26 K measured: too little at 100 %

    def test_below_30_percent_no_window_is_armed(self):
        levels = {A: 18.5, B: 18.5, C: 18.5}
        h = Harness({'heizkreis_1_sensoren': [A, B, C], 'raum_einstellungen': [entry(C, 0, 25, 0)]},
                    values=dict(levels))
        self.days(h, 3, levels)
        self.assertFalse(h.has('no_response', label='Sensor 3'))
        self.assertTrue(h.has('no_response', label='Sensor 1'))

    def test_a_weak_room_warming_does_not_clear_a_circuit_failure(self):
        # A and B (100 %) stay flat on two days: the pump failed. W (40 %)
        # warms by the sun every morning. The circuit Störung must still come.
        levels = {A: 18.5, B: 18.5, W: 18.5}
        h = Harness({'heizkreis_1_sensoren': [A, B, W], 'raum_einstellungen': [entry(W, 0, 40, 0)]},
                    values=dict(levels))
        self.days(h, 2, levels, rising=(W,), rise=1.0)
        rows = h.rows('circuit_no_response')
        self.assertEqual([(row['level'], row['count']) for row in rows], [('Störung', 2)])


    def test_a_room_on_both_circuits_warming_does_not_clear_a_circuit_failure(self):
        # Review H1 (heating starts): W, 80/80, is warmed by Heizkreis 2 on
        # every start; Heizkreis 1's own rooms stay flat. The Störung must come.
        levels = {A: 18.5, B: 18.5, W: 18.5}
        h = Harness({'heizkreis_1_sensoren': [A, B, W], 'raum_einstellungen': [entry(W, 0, 80, 80)]},
                    values=dict(levels))
        self.days(h, 2, levels, rising=(W,), rise=1.0)
        self.assertEqual([row['count'] for row in h.rows('circuit_no_response')], [2])


class StateTests(unittest.TestCase):
    def test_marks(self):
        options = parsed(heizkreis_1_sensoren=[W, A, B, C], kuehlere_raeume=[A, C], nur_frost_raeume=[C],
                         raum_einstellungen=[entry(W, 3.5, 70, 30), entry(B, 4, 100, 0)])
        self.assertEqual([hs.cooler_mark(options, e) for e in (W, A, B, C)], ['a3.5/70/30', True, 4, 'frost'])
        for mark in ('a3.5/70/30', 'a10/100/0', 'x50', True, 4, 'frost'):
            self.assertEqual(hs._cooler(mark), mark)
        for bad in ('a3.25/1/1', 'a11/1/1', 'x', 'y5', 5, 4.0, None):
            with self.assertRaises(hs.Invalid):
                hs._cooler(bad)

    def test_a_168_state_continues_and_a_changed_room_starts_fresh(self):
        old = {'heizkreis_1_sensoren': [W, A, B], 'kuehlere_raeume': [W], 'kuehler_6_raeume': [W]}
        h = Harness(old, values={W: 21.0, A: 17.0, B: 17.0})
        h.run(400)
        document = json.loads(json.dumps(hs.export(h.engine, mono=h.now, wall=h.wall, boot=None)))
        # Same choice written as a 1.6.9 entry: everything continues.
        same = r.parse_house_options(dict(old, raum_einstellungen=[entry(W, 6, 100, 0)]))
        _engine, outcome = hs.restore(same, document, mono=h.now, wall=h.wall, boot=None)
        self.assertEqual(outcome, 'state_restored')
        changed = r.parse_house_options(dict(old, raum_einstellungen=[entry(W, 5.5, 100, 0)]))
        _engine, outcome = hs.restore(changed, document, mono=h.now, wall=h.wall, boot=None)
        self.assertEqual(outcome, 'state_partly_restored')
        # A room added to the other circuit's checks changes that circuit's fingerprint.
        both = r.parse_house_options(dict(old, heizkreis_2_sensoren=[C], raum_einstellungen=[entry(A, 0, 100, 60)]))
        self.assertIn([A, 'x60', False], hs.circuit_fingerprint(both, 'hc2')[1])


ROOT = Path(__file__).resolve().parents[4]
VM_MODULE = ROOT / 'proxy' / 'house_findings.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main_source(path, ref='main'):
    """A file as on main (the VM as released) or on `ref`, or None outside the source tree."""
    try:
        return subprocess.run(['git', '-C', str(ROOT), 'show', ref + ':' + path], capture_output=True,
                              check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None


@unittest.skipUnless(VM_MODULE.exists(), 'the VM source is not part of the published app package')
class MixedVersionTests(unittest.TestCase):
    """App 1.6.9 against the real modules of main: the VM (house_findings, both
    context majors) and app 1.6.8 after a downgrade (its option parser)."""

    def report(self, major, allowance):
        options = {'heizkreis_1_sensoren': [W, A], 'raum_einstellungen': [entry(W, allowance, 70, 0)]}
        h = Harness(options, hc1=circuit(target=23.0, day_target=23.0), values={W: 23.0 - allowance - 2.0, A: 23.0})
        self.assertIsNotNone(h.first('room_cold', 400 + PERSIST))
        _report, payload = r.build_report(instance='a' * 32, seq=1, context_id=f'{0xABCDEF0123456789:016x}:1',
                                          mode='melden', monitor='ok', reason=None,
                                          sensors=h.engine.sensors_report(h.now),
                                          circuits=h.engine.circuits_report(), findings=h.findings,
                                          context_major=major)
        return payload

    def vms(self):
        """The VM module of this tree and, when it differs, that of main."""
        modules = [load('vm_house_findings', VM_MODULE)]
        source = main_source('proxy/house_findings.py')
        if source is not None and source != VM_MODULE.read_bytes():
            path = Path(tempfile.mkdtemp()) / 'vm_house_findings_main.py'
            path.write_bytes(source)
            modules.append(load('vm_house_findings_main', path))
        return modules

    def test_the_vm_accepts_free_allowances_on_both_majors(self):
        for hf in self.vms():
            with self.subTest(vm=hf.__name__):
                self.check_vm(hf)

    def check_vm(self, hf):
        for major, allowance, text in ((2, 3.5, 'erlaubt ab 19,5 °C, Ziel 23,0 °C'),
                                       (2, 10.0, 'erlaubt ab 13,0 °C, Ziel 23,0 °C'),
                                       (1, 3.5, 'statt 23,0 °C')):
            with self.subTest(major=major, allowance=allowance):
                state = hf.HouseState('hargassner/GW/house/report', save=lambda _d: None,
                                      save_findings=lambda _d: None)
                state.context_published(f'{0xABCDEF0123456789:016x}:1', 5000.0, {'hc1': 'radiator', 'hc2': 'floor'})
                payload = self.report(major, allowance)
                self.assertEqual(state.ingest(payload.encode(), mono=5000.0, wall=1_700_000_000.0), 'accepted')
                texts = [row['text'] for row in state.evaluate(5000.0, 1_700_000_000.0)[1]]
                self.assertTrue(any(text in line for line in texts), texts)
                self.assertNotIn('raum_einstellungen', payload)
                self.assertNotIn('einfluss', payload)

    def test_app_168_reads_options_saved_by_169(self):
        # App 1.6.8 as published (549fb56c); main has moved on to 1.6.9.
        source = main_source('home-assistant/hacs/harg-ha/hargassner-dashboard/house_rules.py', '549fb56c')
        if source is None:
            self.skipTest('no git checkout')
        old_path = Path(tempfile.mkdtemp()) / 'house_rules_168.py'
        old_path.write_bytes(source)
        old = load('house_rules_168', old_path)
        self.assertEqual(old.APP_VERSION, '1.6.8')
        world = PickerWorld()
        world.save([row('sensor.wohnzimmer', 'heizkreis_1', kuehler=7.5, hk1=60, hk2=40),
                    row('sensor.kueche', 'heizkreis_1', kuehler=0, hk1=0, hk2=0),
                    row('sensor.bad', 'heizkreis_2', kuehler=3)])
        saved = world.posted[-1]
        # The Supervisor of an older app drops the unknown key; 1.6.8 reads the mirror.
        downgraded = {key: value for key, value in saved.items() if key != 'raum_einstellungen'}
        options = old.parse_house_options(downgraded)
        self.assertTrue(options.valid)
        self.assertEqual([options.level(e) for e in ('sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad')],
                         [6.0, old.FROST_ONLY, 2.0])


if __name__ == '__main__':
    unittest.main()
