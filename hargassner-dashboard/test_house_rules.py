"""House rules with an injected clock: every rule fires at its dwell, not earlier."""
import json
import unittest

import house_rules as r
from house_test_support import Harness, circuit, context_document

A, B, C, D = 'sensor.wohnzimmer', 'sensor.kueche', 'sensor.keller', 'sensor.bad'
SETTLE = {'radiator': 120, 'floor': 480}


def hc1(*entities, **extra):
    return {'heizkreis_1_sensoren': list(entities), **extra}


def hc2(*entities, **extra):
    return {'heizkreis_2_sensoren': list(entities), **extra}


class RoomColdTests(unittest.TestCase):
    def test_room_cold_fires_exactly_after_settle_and_dwell_per_type(self):
        for kind, options, dwell in (('radiator', hc1(A), 90), ('floor', hc2(A), 240)):
            with self.subTest(kind=kind):
                h = Harness(options, values={A: 19.0})
                minute = h.first('room_cold', 2000)
                self.assertEqual(minute, 1 + SETTLE[kind] + dwell)
                row = h.rows('room_cold')[0]
                self.assertEqual((row['level'], row['room'], row['target'], row['label']), ('Hinweis', 19.0, 21.0, 'Sensor 1'))
                self.assertEqual(row['circuit'], 'hc1' if kind == 'radiator' else 'hc2')

    def test_normal_and_short_deficits_stay_quiet(self):
        h = Harness(hc1(A), values={A: 19.6})  # D = 1.4 < 1.5
        h.run(600)
        self.assertNotIn('room_cold', h.codes())
        h = Harness(hc1(A), values={A: 20.8})
        h.run(130)
        h.values[A] = 19.0           # deliberate short deficit of 80 min
        h.run(80)
        h.values[A] = 20.8
        h.run(200)
        self.assertNotIn('room_cold', h.codes())

    def test_hysteresis_and_clear(self):
        h = Harness(hc1(A), values={A: 19.0})
        h.run(211)
        self.assertTrue(h.has('room_cold'))
        h.values[A] = 19.8           # D = 1.2: keeps the finding
        h.run(20)
        self.assertTrue(h.has('room_cold'))
        self.assertEqual(h.rows('room_cold')[0]['room'], 19.0)  # onset frozen
        h.values[A] = 20.2           # D = 0.8 clears
        h.run(16)
        self.assertFalse(h.has('room_cold'))

    def test_cooler_rooms_get_two_kelvin(self):
        h = Harness(hc1(A, kuehlere_raeume=[A]), values={A: 18.0})   # D = 21-2-18 = 1.0
        h.run(400)
        self.assertNotIn('room_cold', h.codes())
        h = Harness(hc1(A, kuehlere_raeume=[A]), values={A: 17.5})   # D = 1.5
        self.assertIsNotNone(h.first('room_cold', 400))
        self.assertEqual(h.rows('room_cold')[0]['target'], 21.0)

    def test_settle_restarts_on_target_phase_and_mode_change(self):
        h = Harness(hc1(A), values={A: 19.0})
        h.run(200)
        h.set_circuit('hc1', target=21.5)  # a correction tap restarts settle
        h.run(200)
        self.assertNotIn('room_cold', h.codes())
        self.assertIsNotNone(h.first('room_cold', 30))
        h = Harness(hc1(A), values={A: 19.0})
        h.run(200)
        h.set_circuit('hc1', mode='day')
        self.assertEqual(h.first('room_cold', 400), SETTLE['radiator'] + 90 + 1)

    def test_status_class_table(self):
        # 2 Absenken Rampe pauses: the timer neither resets nor counts.
        h = Harness(hc1(A), values={A: 19.0})
        h.run(121 + 60)
        h.set_circuit('hc1', status=2)
        h.run(60)
        self.assertNotIn('room_cold', h.codes())
        h.set_circuit('hc1', status=1)
        self.assertEqual(h.first('room_cold', 60), 30)
        # 4/5 heating limit and 0/6-10 inactive reset the cold rule.
        for status in (4, 5, 0, 6, 7, 8, 9, 10):
            with self.subTest(status=status):
                h = Harness(hc1(A), values={A: 19.0})
                h.run(180)
                h.set_circuit('hc1', status=status)
                h.run(5)
                h.set_circuit('hc1', status=1)
                self.assertEqual(h.first('room_cold', 400), 90 + 1, status)
        for status in (None, 11, 99):
            with self.subTest(status=status):
                h = Harness(hc1(A), values={A: 19.0})
                h.set_circuit('hc1', status=status)
                h.run(600)
                self.assertNotIn('room_cold', h.codes())
                self.assertEqual(h.engine.circuits['hc1'].judged, 'unknown')
        self.assertEqual(r.STATUS_CLASS[2], r.TRANSITION)

    def test_supplying_without_phase_or_target_is_unknown(self):
        h = Harness(hc1(A), values={A: 19.0})
        h.set_circuit('hc1', phase=None)
        h.run(600)
        self.assertNotIn('room_cold', h.codes())
        report = h.engine.circuits_report()['hc1']
        self.assertEqual((report['judged'], report['why']), ('unknown', 'phase'))
        self.assertGreater(h.engine.basis_unknown_minutes(h.now)['hc1']['24h'], 500)

    def test_airing_pauses_cold_and_response(self):
        h = Harness(hc1(A), values={A: 20.8})
        h.run(130)
        h.values[A] = 19.6
        h.step()
        self.assertTrue(h.engine.sensors[A].airing(h.now, 3600))
        h.values[A] = 19.0           # stays cold, but the airing pause holds
        # The last drop is the last update within 15 min of the 20.8 before the
        # window opened (14 min later); the pause ends 60 min after it.
        minute = h.first('room_cold', 400)
        self.assertEqual(minute, 14 + 60 + 90)

    def test_circuit_cold_needs_all_usable_sensors(self):
        h = Harness(hc1(A, B), values={A: 17.5, B: 17.8})
        self.assertEqual(h.first('circuit_cold', 600), 1 + 120 + 180)
        row = h.rows('circuit_cold')[0]
        self.assertEqual((row['level'], row['count'], row['room'], row['target'], row['label']),
                         ('Störung', 2, 17.5, 21.0, None))
        h.values[B] = 18.6           # D = 2.4 < 2.5 clears
        h.run(16)
        self.assertFalse(h.has('circuit_cold'))
        h = Harness(hc1(A, B), values={A: 17.5, B: 19.0})
        h.run(700)
        self.assertFalse(h.has('circuit_cold'))
        self.assertTrue(h.has('room_cold'))
        h = Harness(hc2(A, B), values={A: 17.5, B: 17.0})
        self.assertEqual(h.first('circuit_cold', 2000), 1 + 480 + 480)


class ResponseTests(unittest.TestCase):
    def heat_up(self, options, kind='radiator', rise_per_hour=0.0, values=None, **day):
        circuit_name = 'hc1' if 'heizkreis_1_sensoren' in options else 'hc2'
        h = Harness(options, values=values or {A: 18.5, B: 18.5})
        h.set_circuit(circuit_name, phase='setback', target=18.0, status=3, type=kind)
        h.run(30)
        h.set_circuit(circuit_name, phase='day', target=21.0, status=1, **day)
        start = dict(h.values)
        minutes = 0

        def advance(harness):
            nonlocal minutes
            minutes += 1
            for entity in options.get('heizkreis_1_sensoren', []) + options.get('heizkreis_2_sensoren', []):
                if isinstance(start.get(entity), float):
                    harness.values[entity] = round(start[entity] + rise_per_hour * minutes / 60, 1)
            return False
        return h, advance

    def test_no_response_fires_at_window_end_not_earlier(self):
        for kind, window in (('radiator', 60), ('floor', 360)):
            with self.subTest(kind=kind):
                options = hc1(A, B) if kind == 'radiator' else hc2(A, B)
                h, _advance = self.heat_up(options, kind)
                h.run(window - 1)
                self.assertNotIn('no_response', h.codes())
                h.step()
                self.assertEqual(len(h.rows('no_response')), 2)
                circuit_rows = h.rows('circuit_no_response')
                self.assertEqual(len(circuit_rows), 1)
                self.assertEqual((circuit_rows[0]['level'], circuit_rows[0]['count']), ('Störung', 2))
                self.assertIsNone(h.rows('no_response')[0]['room'])

    def test_normal_heat_up_is_quiet(self):
        for kind, rise in (('radiator', 1.2), ('floor', 0.4)):
            with self.subTest(kind=kind):
                options = hc1(A, B) if kind == 'radiator' else hc2(A, B)
                h, advance = self.heat_up(options, kind, rise)
                h.run(400, until=advance)
                self.assertFalse(h.codes() & {'no_response', 'circuit_no_response'})

    def test_pump_off_or_cold_flow_disarms(self):
        h, _ = self.heat_up(hc1(A), pump=False)
        h.run(120)
        self.assertNotIn('no_response', h.codes())
        h, _ = self.heat_up(hc1(A), flow=25.0)   # flow < R + 8
        h.run(120)
        self.assertNotIn('no_response', h.codes())
        h, _ = self.heat_up(hc1(A), pump=None)   # unknown pump pauses, never fires
        h.run(100)
        self.assertNotIn('no_response', h.codes())

    def test_small_deficit_is_not_armed(self):
        h, _ = self.heat_up(hc1(A), values={A: 20.2})
        h.run(120)
        self.assertNotIn('no_response', h.codes())

    def test_min_rise_follows_sensor_resolution(self):
        def coarse(rise):
            h = Harness(hc1(A), values={A: 18.0})
            h.set_circuit('hc1', phase='setback', target=18.0, status=3)
            for index in range(30):        # learn a 0.5 K step
                h.values[A] = 18.0 if index % 2 else 18.5
                h.step()
            self.assertEqual(h.engine.sensors[A].step_size(), 0.5)
            h.values[A] = 18.5
            h.run(20)
            h.set_circuit('hc1', phase='day', target=21.0, status=1)
            h.run(30)
            h.values[A] = 18.5 + rise
            h.run(31)
            return h.codes()
        self.assertIn('no_response', coarse(0.5))      # 0.5 < 0.75
        self.assertNotIn('no_response', coarse(1.0))

    def test_no_response_clears_and_expires(self):
        h, _ = self.heat_up(hc1(A))
        h.run(60)
        self.assertTrue(h.has('no_response'))
        h.values[A] = 20.5             # D < 1.0
        h.run(16)
        self.assertFalse(h.has('no_response'))
        h, _ = self.heat_up(hc1(A))
        h.run(60)
        self.assertTrue(h.has('no_response'))
        h.set_circuit('hc1', status=0)  # nothing else clears it
        h.run(24 * 60 - 61)
        self.assertTrue(h.has('no_response'))
        h.run(2)
        self.assertFalse(h.has('no_response'))

    def test_later_trigger_with_a_rise_clears(self):
        h, _ = self.heat_up(hc1(A))
        h.run(60)
        self.assertTrue(h.has('no_response'))
        h.set_circuit('hc1', target=22.0)          # trigger (b): +1.0 K
        for minute in range(60):
            h.values[A] = round(18.5 + minute / 60, 2)
            h.step()
        h.step()
        self.assertFalse(h.has('no_response'))

    def test_trigger_after_an_hour_without_supplying(self):
        h = Harness(hc1(A), values={A: 18.5})
        h.set_circuit('hc1', status=4)
        h.run(90)
        h.set_circuit('hc1', status=1)
        h.run(61)
        self.assertTrue(h.has('no_response'))
        h = Harness(hc1(A), values={A: 18.5})   # 30 min off: no trigger
        h.run(20)
        h.set_circuit('hc1', status=4)
        h.run(30)
        h.set_circuit('hc1', status=1)
        h.run(61)
        self.assertFalse(h.has('no_response'))


class WarmTests(unittest.TestCase):
    def night(self, options, value, **extra):
        h = Harness(options, local_minute=20 * 60 + 50, values={A: value, B: value})
        h.set_circuit('hc1', phase='setback', target=18.0, status=3, **extra)
        return h

    def test_room_warm_only_at_night_after_dwell(self):
        h = self.night(hc1(A), 23.0)        # U = max(18, 21) = 21; R >= 23
        minute = h.first('room_warm', 600)
        self.assertEqual(minute, 1 + 120 + 120)
        row = h.rows('room_warm')[0]
        self.assertEqual((row['room'], row['target']), (23.0, 21.0))
        h.run(400)                          # 06:00 local clears it
        self.assertFalse(h.has('room_warm'))
        h = Harness(hc1(A), local_minute=8 * 60, values={A: 23.5})
        h.run(600)
        self.assertNotIn('room_warm', h.codes())

    def test_room_warm_exemptions_and_pump(self):
        h = self.night(hc1(A, ohne_ueberwaermung=[A]), 24.0)
        h.run(400)
        self.assertFalse(h.codes() & {'room_warm', 'room_hot'})
        h = self.night(hc1(A), 22.9)
        h.run(400)
        self.assertNotIn('room_warm', h.codes())
        h = self.night(hc1(A), 23.0, flow=26.0)  # flow < R + 5
        h.run(400)
        self.assertNotIn('room_warm', h.codes())
        h = self.night(hc1(A), 23.0)
        h.run(241)
        self.assertTrue(h.has('room_warm'))
        h.set_circuit('hc1', pump=False)
        h.run(29)
        self.assertTrue(h.has('room_warm'))
        h.run(2)
        self.assertFalse(h.has('room_warm'))

    def test_circuit_warm_needs_two_sensors_for_the_dwell(self):
        h = Harness(hc1(A, B), values={A: 23.2, B: 23.0})
        self.assertEqual(h.first('circuit_warm', 1000), 1 + 120 + 360)
        row = h.rows('circuit_warm')[0]
        self.assertEqual((row['room'], row['target'], row['level']), (23.0, 21.0, 'Hinweis'))
        h.values[A] = h.values[B] = 22.4
        h.run(16)
        self.assertFalse(h.has('circuit_warm'))
        h = Harness(hc1(A), values={A: 24.0})
        h.run(1000)
        self.assertNotIn('circuit_warm', h.codes())

    def test_room_hot(self):
        h = Harness(hc1(A), values={A: 28.0})
        self.assertEqual(h.first('room_hot', 120), 61)
        self.assertEqual(h.rows('room_hot')[0]['room'], 28.0)
        h.values[A] = 27.2
        h.run(16)
        self.assertTrue(h.has('room_hot'))
        h.values[A] = 26.8
        h.run(16)
        self.assertFalse(h.has('room_hot'))
        h = Harness(hc1(A), values={A: 28.0})
        h.set_circuit('hc1', pump=False)
        h.run(200)
        self.assertNotIn('room_hot', h.codes())


class LimitAndFrostTests(unittest.TestCase):
    def test_limit_cold(self):
        h = Harness(hc1(A), values={A: 18.5})
        h.set_circuit('hc1', status=4)
        self.assertEqual(h.first('limit_cold', 600), 1 + 360)
        row = h.rows('limit_cold')[0]
        self.assertEqual((row['room'], row['target']), (18.5, 21.0))
        h.set_circuit('hc1', phase='setback', target=18.0)
        h.step()
        self.assertFalse(h.has('limit_cold'))
        for extra in ({'outdoor_avg': 12.0}, {'outdoor_avg': None}):
            h = Harness(hc1(A), values={A: 18.5}, **extra)
            h.set_circuit('hc1', status=5)
            h.run(800)
            self.assertNotIn('limit_cold', h.codes())
        h = Harness(hc2(A), values={A: 18.5})
        h.set_circuit('hc2', status=5)
        self.assertEqual(h.first('limit_cold', 1000), 1 + 720)

    def test_room_frost_and_frost_are_context_free(self):
        options = {'heizkreis_1_sensoren': [A], 'haus_sensoren': [C, D], 'haus_frost_sensoren': [C]}
        h = Harness(options, values={A: 4.5, C: 4.0, D: 3.0})
        h.context_state, h.context = 'none', None
        self.assertEqual(h.first('frost', 30), 11)
        frost = h.rows('frost')[0]
        self.assertEqual((frost['level'], frost['count'], frost['room'], frost['circuit']), ('Störung', 2, 4.0, None))
        labels = {row['label'] for row in h.rows('room_frost')}
        self.assertEqual(labels, {'Sensor 1', 'Sensor 2'})       # D is not frost-eligible
        circuits = {row['label']: row['circuit'] for row in h.rows('room_frost')}
        self.assertEqual(circuits, {'Sensor 1': 'hc1', 'Sensor 2': None})
        h.values[C] = 5.8               # hysteresis: still <= 6
        h.run(16)
        self.assertTrue(h.has('frost'))
        h.values[C] = 6.5
        h.run(16)                       # the value is a 15-minute median
        self.assertFalse(h.has('frost'))
        self.assertTrue(h.has('room_frost', 'Sensor 1'))

    def test_single_frost_sensor_is_only_a_hint(self):
        h = Harness(hc1(A, B), values={A: 4.0, B: 15.0})
        h.set_circuit('hc1', status=0, mode='off')
        h.run(60)
        self.assertEqual(h.codes(), {'room_frost'})


class SensorHealthTests(unittest.TestCase):
    def test_missing_after_30_minutes_only_while_running(self):
        h = Harness(hc1(A), values={A: 'missing'})
        self.assertEqual(h.first('sensor_missing', 60), 31)
        h = Harness(hc1(A), values={A: 'missing'})
        h.ha = 'starting'
        h.run(120)
        self.assertNotIn('sensor_missing', h.codes())
        h = Harness(hc1(A), values={A: 21.0})
        h.attributes[A] = {'unit_of_measurement': '%', 'device_class': 'humidity'}
        self.assertEqual(h.first('sensor_missing', 60), 31)
        self.assertNotIn('Räume', {r.CODES[c][0] for c in h.codes()})

    def test_unavailable(self):
        h = Harness(hc1(A), values={A: 'unavailable'})
        self.assertEqual(h.first('sensor_unavailable', 60), 31)
        h.values[A] = 21.0
        h.step()
        self.assertFalse(h.has('sensor_unavailable'))
        h.values[A] = 'garbage'
        self.assertEqual(h.first('sensor_unavailable', 60), 31)

    def test_stale_and_on_change_sensor(self):
        h = Harness(hc1(A), values={A: 21.0})
        h.reporting[A] = 12 * 60        # Shelly H&T style: up to 12 h between reports
        h.run(3 * 24 * 60)
        self.assertNotIn('sensor_stale', h.codes())
        self.assertTrue(h.engine.sensors[A].usable(h.now) or h.engine.sensors[A].unusable_reason(h.now) == 'stale')
        h = Harness(hc1(A), values={A: 21.0})
        h.reporting[A] = 10 ** 9         # never reports again
        h.run(361)
        self.assertIsNone(h.engine.sensors[A].unusable_reason(h.now))
        h.step()                         # more than 6 h since the update
        self.assertEqual(h.engine.sensors[A].unusable_reason(h.now), 'stale')
        self.assertNotIn('sensor_stale', h.codes())
        self.assertEqual(h.first('sensor_stale', 24 * 60), 24 * 60 + 1 - 362)
        h.reporting[A] = 'periodic'
        h.step()
        self.assertFalse(h.has('sensor_stale'))
        self.assertTrue(h.engine.sensors[A].usable(h.now))

    def test_implausible(self):
        h = Harness(hc1(A), values={A: 75.0})
        self.assertEqual(h.first('sensor_implausible', 30), 11)
        self.assertFalse(h.engine.sensors[A].usable(h.now))
        h.values[A] = 21.0
        h.run(9)
        self.assertTrue(h.has('sensor_implausible'))
        h.run(2)
        self.assertFalse(h.has('sensor_implausible'))
        h = Harness(hc1(A), values={A: 98.0})
        h.attributes[A] = {'unit_of_measurement': '°F'}      # 36.7 °C is plausible
        h.run(20)
        self.assertNotIn('sensor_implausible', h.codes())

    def test_step_anchored_across_an_unavailability_gap(self):
        h = Harness(hc1(A), values={A: 21.0})
        h.run(10)
        h.values[A] = 'unavailable'
        h.run(120)
        h.values[A] = 0.0               # an HA restart start value
        h.step()
        self.assertTrue(h.has('sensor_jumping'))
        self.assertEqual(h.engine.sensors[A].unusable_reason(h.now), 'step')
        h.values[A] = 21.0
        h.step()                         # back again is a second step
        self.assertFalse(h.engine.sensors[A].usable(h.now))
        h.run(30)
        self.assertTrue(h.engine.sensors[A].usable(h.now))
        self.assertTrue(h.has('sensor_jumping'))
        h.run(24 * 60 - 32)
        self.assertTrue(h.has('sensor_jumping'))
        h.run(2)
        self.assertFalse(h.has('sensor_jumping'))
        self.assertFalse(h.codes() & {code for code, (check, _l) in r.CODES.items() if check == 'Räume'})

    def test_spikes_are_discarded_and_counted(self):
        h = Harness(hc1(A), values={A: 21.0})
        h.run(5)
        h.values[A] = 25.0
        h.step()
        self.assertEqual(h.engine.sensors[A].value(h.now), 21.0)   # held back
        h.values[A] = 21.2
        h.step()
        self.assertEqual(h.engine.sensors[A].last_accepted, 21.2)
        self.assertEqual(h.engine.sensors[A].value(h.now), 21.0)
        self.assertNotIn('sensor_jumping', h.codes())
        for _ in range(2):
            h.run(30)
            h.values[A] = 25.0
            h.step()
            h.values[A] = 21.0
            h.step()
        self.assertTrue(h.has('sensor_jumping'))
        self.assertNotIn(25.0, [v for _t, v in h.engine.sensors[A].history])
        h = Harness(hc1(A), values={A: 21.0})                 # a real 4 K change is accepted later
        h.run(5)
        h.values[A] = 17.0
        h.run(17)
        self.assertEqual(h.engine.sensors[A].value(h.now), 17.0)
        self.assertNotIn('sensor_jumping', h.codes())

    def test_stuck_only_for_fine_sensors(self):
        h = Harness(hc1(A), values={A: 21.0})
        for index in range(30):
            h.values[A] = 21.0 + (index % 2) / 10
            h.step()
        h.values[A] = 21.3
        self.assertEqual(h.first('sensor_stuck', 4400), 72 * 60 + 1)
        self.assertEqual(h.engine.sensors[A].unusable_reason(h.now), 'stuck')
        h.values[A] = 21.4
        h.step()
        self.assertFalse(h.has('sensor_stuck'))
        h = Harness(hc1(A), values={A: 21.0})
        for index in range(30):
            h.values[A] = 21.0 + (index % 2) / 2
            h.step()
        h.run(80 * 60)
        self.assertNotIn('sensor_stuck', h.codes())

    def test_battery(self):
        h = Harness(hc1(A), values={A: 21.0})
        h.attributes[A] = {'battery_level': 9}
        self.assertEqual(h.first('sensor_battery', 120), 61)
        h.attributes[A] = {'battery_level': 14}
        h.step()
        self.assertTrue(h.has('sensor_battery'))
        h.attributes[A] = {'battery': '16'}
        h.step()
        self.assertFalse(h.has('sensor_battery'))

    def test_all_unavailable_replaces_rows(self):
        options = {'heizkreis_1_sensoren': [A, B], 'haus_sensoren': [C]}
        h = Harness(options, values={A: 'unavailable', B: 'missing', C: 'unavailable'})
        h.run(31)
        self.assertEqual(h.codes(), {'sensors_all_unavailable'})
        self.assertEqual(h.rows('sensors_all_unavailable')[0]['count'], 3)
        h.values[C] = 21.0
        h.step()
        self.assertEqual(h.codes(), {'sensor_unavailable', 'sensor_missing'})
        report = h.engine.sensors_report(h.now)
        self.assertEqual(report, {'configured': 3, 'usable': 1, 'unavailable': 2})
        h = Harness(hc1(A), values={A: 'unavailable'})
        h.run(40)
        self.assertEqual(h.codes(), {'sensor_unavailable'})   # one sensor: no aggregate

    def test_sensor_problems_never_produce_heating_rows(self):
        h = Harness(hc1(A, B), values={A: 'unavailable', B: 'unavailable'})
        h.run(600)
        self.assertEqual(h.engine.circuits_report()['hc1']['why'], 'sensors')
        self.assertFalse(h.codes() & {code for code, (check, _l) in r.CODES.items() if check == 'Räume'})


class CircuitTypeTests(unittest.TestCase):
    def test_absent_circuit(self):
        h = Harness(hc2(A), values={A: 15.0})
        h.set_circuit('hc2', type='absent')
        h.run(600)
        self.assertEqual(h.codes(), {'config_circuit_absent'})
        row = h.rows('config_circuit_absent')[0]
        self.assertEqual((row['circuit'], row['label']), ('hc2', None))
        self.assertEqual(h.engine.circuits_report()['hc2'], {'type': 'absent', 'type_source': 'controller',
                                                              'sensors': 1, 'judged': 'unknown', 'why': 'absent'})
        h.context_state = 'none'        # a context gap keeps the row
        h.run(5)
        self.assertEqual(h.codes(), {'config_circuit_absent'})
        h.context_state = 'ok'
        h.set_circuit('hc2', type='floor')
        h.step()
        self.assertNotIn('config_circuit_absent', h.codes())

    def test_type_null_pauses_and_option_overrides(self):
        h = Harness(hc1(A), values={A: 19.0})
        h.run(200)                      # 79 of 90 cold minutes
        h.set_circuit('hc1', type=None)
        h.run(100)
        self.assertNotIn('room_cold', h.codes())
        self.assertEqual(h.engine.circuits_report()['hc1']['why'], 'type')
        h.set_circuit('hc1', type='radiator')
        self.assertEqual(h.first('room_cold', 30), 11)   # paused, not reset
        h = Harness(hc1(A, heizkreis_1_art='fussboden'), values={A: 19.0})
        report = h.engine.circuits_report()
        h.step()
        report = h.engine.circuits_report()['hc1']
        self.assertEqual((report['type'], report['type_source']), ('floor', 'option'))
        self.assertIsNone(h.first('room_cold', 600))
        h = Harness(hc1(A, heizkreis_1_art='heizkoerper'), values={A: 19.0})
        h.set_circuit('hc1', type=None)
        self.assertEqual(h.first('room_cold', 300), 211)

    def test_pause_limits_per_type(self):
        for kind, options, limit in (('radiator', hc1(A), 120), ('floor', hc2(A), 360)):
            for gap, kept in ((limit, True), (limit + 2, False)):
                with self.subTest(kind=kind, gap=gap):
                    h = Harness(options, values={A: 19.0})
                    h.run(SETTLE[kind] + 1 + 30)
                    h.context_state = 'none'
                    h.run(gap)
                    h.context_state = 'ok'
                    dwell = 90 if kind == 'radiator' else 240
                    minute = h.first('room_cold', 2000)
                    self.assertEqual(minute, dwell - 30 if kept else dwell + 1)

    def test_ha_outage_pauses_never_resets_or_credits(self):
        for ha, reason in (('starting', 'ha_starting'), ('unavailable', 'ha_api_unavailable')):
            with self.subTest(ha=ha):
                h = Harness(hc1(A), values={A: 19.0})
                h.run(121 + 60)
                h.ha = ha
                h.run(60)
                self.assertEqual((h.engine.monitor, h.engine.reason), ('unknown', reason))
                self.assertNotIn('room_cold', h.codes())
                self.assertEqual(h.engine.circuits_report()['hc1']['why'], 'ha')
                h.ha = 'running'
                self.assertEqual(h.first('room_cold', 60), 30)

    def test_judged_grace_of_15_minutes(self):
        h = Harness(hc1(A), values={A: 21.0})
        h.run(5)
        self.assertEqual(h.engine.circuits['hc1'].judged, 'yes')
        h.set_circuit('hc1', phase=None)  # the target basis is unknown while supplying
        h.run(15)                       # unknown for 14 min: the previous verdict stays
        self.assertEqual(h.engine.circuits_report()['hc1']['judged'], 'yes')
        h.run(1)
        self.assertEqual(h.engine.circuits_report()['hc1'], {'type': 'radiator', 'type_source': 'controller',
                                                              'sensors': 1, 'judged': 'unknown', 'why': 'phase'})
        h.set_circuit('hc1', phase='day')
        h.step()
        self.assertEqual(h.engine.circuits_report()['hc1']['judged'], 'yes')
        h.context_state = 'none'        # no usable context at all: no grace
        h.step()
        self.assertEqual(h.engine.circuits_report()['hc1'], {'type': None, 'type_source': None, 'sensors': 1,
                                                              'judged': 'unknown', 'why': 'context'})
        h.context_state = 'ok'
        h.set_circuit('hc1', status=0)
        h.step()
        self.assertEqual(h.engine.circuits_report()['hc1']['judged'], 'inactive')

    def test_fail_closed_without_context(self):
        h = Harness(hc1(A, B), values={A: 15.0, B: 15.0})
        h.context_state, h.context = 'none', None
        h.run(24 * 60)
        self.assertEqual((h.engine.monitor, h.engine.reason), ('unknown', 'no_context'))
        self.assertEqual(h.codes(), set())
        for name in r.CIRCUITS:
            self.assertEqual(h.engine.circuits_report()[name]['judged'], 'unknown')
        h.context_state = 'unsupported'
        h.step()
        self.assertEqual(h.engine.reason, 'context_version_unsupported')

    def test_offline_or_maintenance_context_is_not_usable(self):
        for top in ({'online': False}, {'maintenance': True}):
            with self.subTest(top=top):
                h = Harness(hc1(A), values={A: 18.0}, **top)
                h.run(600)
                self.assertNotIn('room_cold', h.codes())
                self.assertEqual(h.engine.circuits_report()['hc1']['why'], 'context')


class ReportTests(unittest.TestCase):
    def row(self, code, label=None, circuit=None, level=None):
        return {'code': code, 'level': level or r.CODES[code][1], 'label': label, 'circuit': circuit,
                'held_s': 1, 'room': None, 'target': None, 'count': None}

    def test_truncation_order_and_folded(self):
        rows = [self.row('sensor_battery', f'R{i:02d}', 'hc2') for i in range(25)]
        rows += [self.row('room_cold', 'Zimmer', 'hc2'), self.row('room_cold', 'Arbeit', 'hc1'),
                 self.row('frost'), self.row('room_frost', 'Keller'), self.row('sensor_stale', 'A', None)]
        report, payload = r.build_report(instance='0' * 32, seq=1, context_id=None, mode='beobachten',
                                         monitor='unknown', reason='no_context',
                                         sensors={'configured': 24, 'usable': 0, 'unavailable': 0},
                                         circuits={n: {'type': None, 'type_source': None, 'sensors': 0,
                                                       'judged': 'unknown', 'why': 'context'} for n in r.CIRCUITS},
                                         findings=rows)
        order = [(row['code'], row['label']) for row in report['findings']]
        self.assertEqual(order[:4], [('frost', None), ('room_cold', 'Arbeit'), ('room_cold', 'Zimmer'), ('room_frost', 'Keller')])
        self.assertEqual(len(report['findings']), 24)
        self.assertEqual(report['folded'], 6)
        self.assertEqual(order[4:6], [('sensor_battery', 'R00'), ('sensor_battery', 'R01')])
        self.assertEqual(json.loads(payload), report)

    def test_onset_grid_rounding_half_up(self):
        self.assertEqual([r.half(x) for x in (18.24, 18.25, 18.74, 18.75, -0.25, 0.2, 21.0)],
                         [18.0, 18.5, 18.5, 19.0, 0.0, 0.0, 21.0])
        for value in (r.half(18.24), r.half(21)):
            self.assertIsInstance(value, float)
        self.assertIn('"room":18.5', r.canonical({'room': r.half(18.3)}))
        self.assertIn('"room":21.0', r.canonical({'room': r.half(21)}))

    def test_onset_numbers_frozen_while_active(self):
        h = Harness(hc1(A), values={A: 19.1})
        h.first('room_cold', 300)
        self.assertEqual(h.rows('room_cold')[0]['room'], 19.0)
        h.values[A] = 18.2
        h.run(30)
        self.assertEqual(h.rows('room_cold')[0]['room'], 19.0)
        held = h.rows('room_cold')[0]['held_s']
        h.step()
        self.assertEqual(h.rows('room_cold')[0]['held_s'], held + 60)

    def test_context_validation_nulls_unknown_values(self):
        document = context_document(hc1=circuit(mode='turbo', phase='noon', target=40, status=-1, pump='yes',
                                                flow=200, type='heatpump'))
        status, context = r.validate_context(json.dumps(document))
        self.assertEqual(status, 'ok')
        self.assertEqual({k: v for k, v in context['circuits']['hc1'].items() if v is not None},
                         {'day_target': 21.0, 'setback_target': 18.0, 'flow_target': 45.0})
        for broken in ({'schema': 'harg-house-context/x'}, {'context': 'nothex:1'}, {'circuits': []},
                       {'online': 'yes'}, {'outdoor': '2'}, {'report_ack': 'x'}, {'extra': 1},
                       {'circuits': {'hc1': circuit()}}, {'circuits': {'hc1': circuit(), 'hc2': {'mode': 'auto'}}},
                       {'circuits': {'hc1': circuit(), 'hc2': circuit(), 'hc3': circuit()}},
                       {'circuits': {'hc1': circuit(), 'hc2': {**circuit(), 'extra': 1}}},
                       {'report_ack': {'instance': 'a' * 32, 'seq': 1}}):
            with self.subTest(broken=broken):
                self.assertEqual(r.validate_context(json.dumps({**document, **broken}))[0], 'invalid')
        self.assertEqual(r.validate_context(json.dumps({**document, 'schema': 'harg-house-context/2'}))[0], 'unsupported')
        self.assertEqual(r.validate_context('{"a":1,"a":2}')[0], 'invalid')
        missing = dict(document)
        del missing['outdoor']
        self.assertEqual(r.validate_context(json.dumps(missing))[0], 'invalid')
        self.assertEqual(r.validate_context(json.dumps(document).replace('2.0', 'NaN', 1))[0], 'invalid')
        self.assertEqual(r.validate_context(json.dumps({**document, 'pad': 'x' * 5000}))[0], 'invalid')
        bad_ack = {**document, 'report_ack': {'instance': 'a' * 32, 'seq': 1, 'result': 'maybe'}}
        self.assertIsNone(r.validate_context(json.dumps(bad_ack))[1]['report_ack'])
        good_ack = {**document, 'report_ack': {'instance': 'a' * 32, 'seq': 1, 'result': 'accepted'}}
        self.assertEqual(r.validate_context(json.dumps(good_ack))[1]['report_ack']['result'], 'accepted')


if __name__ == '__main__':
    unittest.main()
