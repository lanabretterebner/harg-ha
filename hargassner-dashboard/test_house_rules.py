"""House rules with an injected clock: every rule fires at its dwell, not earlier."""
import json
import unittest

import house_rules as r
from house_test_support import Harness, circuit, context_document

A, B, C, D = 'sensor.wohnzimmer', 'sensor.kueche', 'sensor.keller', 'sensor.bad'
SETTLE = {'radiator': 120, 'floor': 480}
COLD_SETTLE = {'radiator': 120, 'floor': 240}   # floor room_cold: day phase, cold_settle 4 h
PERSIST = r.ROOM_COLD_PERSIST_S // 60   # a cold occasion becomes room_cold 12 h later


def first_dwell(h, entity, limit):
    """Minutes until the room_cold dwell (one cold occasion) completes, or None."""
    for minute in range(1, limit + 1):
        h.step()
        dwell = h.engine.cold_dwell.get(entity)
        if dwell is not None and dwell.active:
            return minute
    return None


def hc1(*entities, **extra):
    return {'heizkreis_1_sensoren': list(entities), **extra}


def hc2(*entities, **extra):
    return {'heizkreis_2_sensoren': list(entities), **extra}


class RoomColdTests(unittest.TestCase):
    def test_room_cold_fires_exactly_after_settle_dwell_and_persistence_per_type(self):
        for kind, options, dwell in (('radiator', hc1(A), 90), ('floor', hc2(A), 240)):
            with self.subTest(kind=kind):
                h = Harness(options, values={A: 19.0})
                self.assertEqual(first_dwell(h, A, 2000), 1 + COLD_SETTLE[kind] + dwell)
                self.assertFalse(h.has('room_cold'))
                minute = h.first('room_cold', 2000)
                self.assertEqual(minute, PERSIST)
                row = h.rows('room_cold')[0]
                self.assertEqual((row['level'], row['room'], row['target'], row['label']), ('Hinweis', 19.0, 21.0, 'Sensor 1'))
                self.assertEqual(row['circuit'], 'hc1' if kind == 'radiator' else 'hc2')

    def test_normal_and_short_deficits_stay_quiet(self):
        h = Harness(hc1(A), values={A: 19.6})  # D = 1.4 < 1.5
        h.run(1200)
        self.assertNotIn('room_cold', h.codes())
        h = Harness(hc1(A), values={A: 20.8})
        h.run(130)
        h.values[A] = 19.0           # deliberate short deficit of 80 min
        h.run(80)
        h.values[A] = 20.8
        h.run(200)
        self.assertNotIn('room_cold', h.codes())

    def test_a_cold_afternoon_that_ends_at_the_day_level_is_forgotten(self):
        # A valve turned down for 6 h: one cold occasion, then the room is back
        # within 1 K of its day target. A second cold afternoon starts afresh.
        h = Harness(hc1(A), values={A: 21.0})
        h.run(130)
        for _day in range(2):
            for minute in range(1, 61):  # slowly, no airing signature
                h.values[A] = round(21.0 - minute / 30, 2)
                h.step()
            self.assertIsNotNone(first_dwell(h, A, 120))
            h.run(240)
            h.values[A] = 20.6
            h.run(20)
            self.assertNotIn(A, h.engine.cold_since)
            h.run(600)
        self.assertNotIn('room_cold', {code for code in h.codes()})

    def test_a_room_that_warms_is_not_staying_cold(self):
        # 4 K below, but warming 0.4 K/h (a slow recovery): no occasion.
        h = Harness(hc1(A), values={A: 17.0})
        h.run(121)
        for minute in range(300):
            h.values[A] = round(17.0 + 0.4 * minute / 60, 2)
            h.step()
            self.assertFalse(h.engine.cold_dwell[A].active, minute)
        # It stops warming 1.5 K or more below: the dwell runs once the trend is flat.
        minute = first_dwell(h, A, 300)
        self.assertTrue(90 < minute <= 90 + 60, minute)

    def test_hysteresis_and_clear(self):
        h = Harness(hc1(A), values={A: 19.0})
        h.run(211 + PERSIST)
        self.assertTrue(h.has('room_cold'))
        h.values[A] = 19.8           # D = 1.2: keeps the finding
        h.run(20)
        self.assertTrue(h.has('room_cold'))
        self.assertEqual(h.rows('room_cold')[0]['room'], 19.0)  # onset frozen
        h.values[A] = 20.2           # D = 0.8: back at the day level, clears
        h.run(16)
        self.assertFalse(h.has('room_cold'))
        self.assertNotIn(A, h.engine.cold_since)

    def test_cooler_rooms_get_two_kelvin(self):
        h = Harness(hc1(A, kuehlere_raeume=[A]), values={A: 18.0})   # D = 21-2-18 = 1.0
        h.run(400 + PERSIST)
        self.assertNotIn('room_cold', h.codes())
        h = Harness(hc1(A, kuehlere_raeume=[A]), values={A: 17.5})   # D = 1.5
        self.assertIsNotNone(h.first('room_cold', 400 + PERSIST))
        self.assertEqual(h.rows('room_cold')[0]['target'], 21.0)

    def test_settle_restarts_on_target_phase_and_mode_change(self):
        h = Harness(hc1(A), values={A: 19.0})
        h.run(200)
        h.set_circuit('hc1', target=21.5)  # a correction tap restarts settle
        h.run(200)
        self.assertFalse(h.engine.cold_dwell[A].active)
        self.assertIsNotNone(first_dwell(h, A, 30))
        h = Harness(hc1(A), values={A: 19.0})
        h.run(200)
        h.set_circuit('hc1', mode='day')
        self.assertEqual(first_dwell(h, A, 400), SETTLE['radiator'] + 90 + 1)

    def test_room_cold_lasts_across_a_night_off(self):
        # Nights with the heating limit switching the circuit off (status 4):
        # the finding stays, it is not new every morning.
        h = Harness(hc1(A), values={A: 17.0})
        self.assertIsNotNone(h.first('room_cold', 211 + PERSIST))
        for _night in range(2):
            h.set_circuit('hc1', phase='setback', target=17.0, status=4)
            h.run(8 * 60)
            h.set_circuit('hc1', phase='day', target=21.0, status=1)
            held = h.rows('room_cold')[0]['held_s']
            h.run(4 * 60)
            self.assertEqual(h.rows('room_cold')[0]['held_s'], held + 4 * 3600)

    def test_room_cold_lasts_across_the_settle_of_a_phase_change(self):
        # A dead radiator: the finding stays through the 22:00 setback and the
        # next morning's settle (no new notice every day).
        h = Harness(hc1(A), values={A: 17.0})
        self.assertIsNotNone(h.first('room_cold', 211 + PERSIST))
        h.set_circuit('hc1', phase='setback', target=17.0, status=3)
        h.run(8 * 60)
        h.set_circuit('hc1', phase='day', target=21.0, status=1)
        held = h.rows('room_cold')[0]['held_s']
        h.run(4 * 60)
        self.assertEqual(h.rows('room_cold')[0]['held_s'], held + 4 * 3600)

    def test_status_class_table(self):
        # 2 Absenken Rampe pauses: the timer neither resets nor counts.
        h = Harness(hc1(A), values={A: 19.0})
        h.run(121 + 60)
        h.set_circuit('hc1', status=2)
        h.run(60)
        self.assertFalse(h.engine.cold_dwell[A].active)
        h.set_circuit('hc1', status=1)
        self.assertEqual(first_dwell(h, A, 60), 30)
        # 4/5 heating limit and 0/6-10 inactive reset the cold rule.
        for status in (4, 5, 0, 6, 7, 8, 9, 10):
            with self.subTest(status=status):
                h = Harness(hc1(A), values={A: 19.0})
                h.run(180)
                h.set_circuit('hc1', status=status)
                h.run(5)
                h.set_circuit('hc1', status=1)
                self.assertEqual(first_dwell(h, A, 400), 90 + 1, status)
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

    def test_airing_pauses_cold_until_the_room_is_back(self):
        h = Harness(hc1(A), values={A: 20.8})
        h.run(130)
        h.values[A] = 19.6
        h.step()
        self.assertTrue(h.engine.sensors[A].airing(h.now, 3600))
        h.values[A] = 19.0           # stays cold (a window left open): the episode holds
        # The episode ends AIRING_MAX_S after its first drop; only then does the
        # cold dwell start, and the finding needs its 12 h on top.
        self.assertEqual(first_dwell(h, A, 2000), r.AIRING_MAX_S // 60 + 90)
        self.assertIsNone(h.engine.sensors[A].airing_start)
        # Airing that ends: back within 0.5 K of the level before, the episode is over.
        h = Harness(hc1(A), values={A: 20.8})
        h.run(130)
        h.values[A] = 18.5
        h.step()
        h.run(70)
        self.assertIsNotNone(h.engine.sensors[A].airing_start)
        h.values[A] = 20.4
        h.run(15)
        self.assertIsNone(h.engine.sensors[A].airing_start)
        self.assertFalse(h.engine.sensors[A].airing_episode(h.now, 3600, False))

    def test_circuit_cold_waits_while_rooms_warm(self):
        # Back from a holiday: every room 4 K below and warming 0.5 K/h. No
        # Störung while they warm; it fires once they stop 3 K below.
        h = Harness(hc1(A, B), values={A: 15.0, B: 15.0})
        h.run(121)
        for minute in range(240):
            h.values[A] = h.values[B] = round(15.0 + 0.5 * minute / 60, 2)
            h.step()
            self.assertFalse(h.has('circuit_cold'), minute)
        # Stuck 4 K below: the Störung follows its 3 h once the trend is flat.
        minute = h.first('circuit_cold', 300)
        self.assertTrue(180 < minute <= 180 + 60, minute)

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
        h.run(700 + PERSIST)
        self.assertFalse(h.has('circuit_cold'))
        self.assertTrue(h.has('room_cold'))
        h = Harness(hc2(A, B), values={A: 17.5, B: 17.0})
        self.assertEqual(h.first('circuit_cold', 2000), 1 + 240 + 180)  # underfloor: 4 h heat-up, 3 h

    def test_radiator_circuit_cold_is_judged_in_the_day_phase_only(self):
        # Owner decision 2026-11-23: radiators as underfloor. A failed pump's
        # Störung stays raised over a night whose setback target the rooms are
        # within 2.5 K of, instead of ending and returning the next day.
        h = Harness(hc1(A, B), values={A: 17.5, B: 17.8})
        self.assertIsNotNone(h.first('circuit_cold', 600))
        h.set_circuit('hc1', phase='setback', target=18.0, status=3)
        for minute in range(10 * 60):
            # Short gaps late in the night: HA away, Absenken-Rampe (status 2).
            h.ha = 'unavailable' if minute in (180, 181) else 'running'
            h.set_circuit('hc1', status=2 if minute in (240, 241) else 3)
            h.step()
            self.assertTrue(h.engine.circuits['hc1'].circuit_cold.active, minute)
        h.ha = 'running'
        h.step()
        self.assertTrue(h.has('circuit_cold'))
        # The day phase still judges: heat-up first, then the day target.
        h.set_circuit('hc1', phase='day', target=21.0, status=1)
        for minute in range(5 * 60):
            h.step()
            self.assertTrue(h.has('circuit_cold'), minute)      # still 3 K below the day target
        h.values[A] = h.values[B] = 19.5
        self.assertTrue(h.run(15, until=lambda x: not x.has('circuit_cold')))

    def test_radiator_circuit_cold_never_fires_outside_a_day_phase(self):
        # Setback: rooms 3.5 K below the setback target for 10 h raise nothing.
        h = Harness(hc1(A, B), hc1=circuit(phase='setback', target=18.0, status=3), values={A: 14.5, B: 14.5})
        h.run(10 * 60)
        self.assertNotIn('circuit_cold', h.codes())
        # No known phase: never judged, a basis is never invented.
        h = Harness(hc1(A, B), hc1=circuit(phase=None), values={A: 17.5, B: 17.8})
        h.run(10 * 60)
        self.assertNotIn('circuit_cold', h.codes())
        # An existing finding holds while the phase is unknown.
        h = Harness(hc1(A, B), values={A: 17.5, B: 17.8})
        self.assertIsNotNone(h.first('circuit_cold', 600))
        h.set_circuit('hc1', phase=None)
        h.run(5 * 60)
        self.assertTrue(h.has('circuit_cold'))

    def test_radiator_circuit_cold_holds_through_a_settle(self):
        # A failed radiator circuit (review 2026-11-23): the Störung neither ends
        # at the change to setback nor at a dial tap and then returns 5 h later;
        # it holds through the settle and is judged again after it.
        h = Harness(hc1(A, B), values={A: 17.5, B: 17.8})
        self.assertIsNotNone(h.first('circuit_cold', 600))
        h.set_circuit('hc1', phase='setback', target=20.5, status=3)   # still 2.7 K or more below
        for minute in range(300):
            h.step()
            self.assertTrue(h.has('circuit_cold'), minute)
        h.set_circuit('hc1', target=21.0)                              # a correction tap restarts settle
        for minute in range(300):
            h.step()
            self.assertTrue(h.has('circuit_cold'), minute)
        # A reference sensor that drops out across the end of the settle pauses
        # the check; the settle's paused time does not end the finding at once.
        h.set_circuit('hc1', target=20.5)
        h.run(SETTLE['radiator'] - 5)
        h.values[B] = 'unavailable'
        h.run(30)
        self.assertTrue(h.has('circuit_cold'))
        h.values[B] = 17.8
        h.run(2)
        self.assertTrue(h.has('circuit_cold'))
        # Rooms back within 2.5 K: it ends once the settle is over, not before.
        h.set_circuit('hc1', phase='day', target=21.0, status=1)
        h.values[A] = h.values[B] = 19.5
        h.run(SETTLE['radiator'] - 5)
        self.assertTrue(h.has('circuit_cold'))
        self.assertTrue(h.run(30, until=lambda x: not x.has('circuit_cold')))


class FloorDailyProgrammeTests(unittest.TestCase):
    """Underfloor circuit_cold with a split daily programme (owner decision 2026-11-18):
    day 06:00-09:00 and 15:00-23:00, setback between. Judged only in a day phase,
    against the day target, after 4 h of heat-up, for 3 h (TYPE_CONSTANTS)."""

    PROGRAMME = ((6 * 60, 'day', 21.0), (9 * 60, 'setback', 18.0), (15 * 60, 'day', 21.0), (23 * 60, 'setback', 18.0))

    def phase_at(self, minute):
        phase = self.PROGRAMME[-1]
        for start, name, target in self.PROGRAMME:
            if minute >= start:
                phase = (start, name, target)
        return phase[1], phase[2]

    def run_days(self, h, days, temperature=None):
        """Minute by minute from 00:00; returns {(day, minute): codes}."""
        seen = {}
        for day in range(days):
            for minute in range(24 * 60):
                phase, target = self.phase_at(minute)
                if h.context['circuits']['hc2']['phase'] != phase:
                    h.set_circuit('hc2', phase=phase, target=target)
                if temperature is not None:
                    for entity, value in temperature(day, minute, phase).items():
                        h.values[entity] = value
                h.step()
                seen[(day, minute)] = set(h.codes())
        return seen

    def harness(self, value):
        h = Harness(hc2(A, B), local_minute=0, values={A: value, B: value})
        h.set_circuit('hc2', phase='setback', target=18.0, day_target=21.0)
        return h

    def test_fires_within_the_evening_phase_when_the_rooms_stay_cold(self):
        h = self.harness(17.0)                       # 4 K below the day target all day
        seen = self.run_days(h, 3)
        first = min(key for key, codes in seen.items() if 'circuit_cold' in codes)
        # Day 1 evening: 15:00 + 4 h heat-up + 3 h = 22:00 (the first 15:00 phase start
        # is the first known one, so the very first evening may also be day 1).
        self.assertLessEqual(first[0], 1)
        self.assertTrue(15 * 60 <= first[1] < 23 * 60, first)
        # Once active it holds through setback and the next heat-up: no nightly end and return.
        later = [codes for key, codes in seen.items() if key > first]
        self.assertTrue(all('circuit_cold' in codes for codes in later))

    def test_normal_heat_up_after_setback_never_fires(self):
        def curve(day, minute, phase):
            # Setback 18 °C; in the evening the floor brings the rooms up 0.5 K/h from 15:00.
            if phase == 'day' and minute >= 15 * 60:
                value = min(21.0, 18.0 + 0.5 * (minute - 15 * 60) / 60)
            elif phase == 'day':
                value = min(21.0, 18.0 + 0.5 * (minute - 6 * 60) / 60)
            else:
                value = 18.0
            return {A: value, B: value}
        seen = self.run_days(self.harness(18.0), 3, curve)
        self.assertFalse(any('circuit_cold' in codes for codes in seen.values()))

    def test_never_judged_during_setback(self):
        h = self.harness(14.0)                      # 4 K below even the setback target
        h.set_circuit('hc2', phase='setback', target=18.0)
        h.run(20 * 60)
        self.assertFalse(h.has('circuit_cold'))

    def test_cold_only_in_the_morning_window_never_fires(self):
        def morning_cold(day, minute, phase):
            value = 17.0 if 6 * 60 <= minute < 9 * 60 else 20.8 if phase == 'day' else 18.0
            return {A: value, B: value}
        seen = self.run_days(self.harness(18.0), 3, morning_cold)
        self.assertFalse(any('circuit_cold' in codes for codes in seen.values()))

    def test_cleared_when_the_next_evening_is_warm(self):
        def recovers(day, minute, phase):
            value = 17.0 if day == 0 else (20.8 if phase == 'day' else 18.0)
            return {A: value, B: value}
        seen = self.run_days(self.harness(17.0), 3, recovers)
        self.assertTrue(any('circuit_cold' in codes for (day, _m), codes in seen.items() if day == 0))
        self.assertFalse(seen[(2, 23 * 60 - 1)] & {'circuit_cold'})


WINDOW = {'radiator': 60, 'floor': 360}


def setback(h, name, kind='radiator'):
    h.set_circuit(name, phase='setback', target=18.0, status=3, type=kind)


def day(h, name, kind='radiator', **extra):
    h.set_circuit(name, phase='day', target=21.0, status=1, type=kind, **extra)


def flat(h, minutes, levels):
    """Run with rooms flat at `levels` (entity -> °C) plus a real sensor's 0.1 K jitter."""
    for _ in range(minutes):
        for entity, level in levels.items():
            h.values[entity] = round(level + (0.1 if h.steps % 2 else 0.0), 1)
        h.step()


class ResponseTests(unittest.TestCase):
    """no_response: failed heating starts on three days; circuit_no_response: on two."""

    def heat_up(self, options, kind='radiator', rise_per_hour=0.0, values=None, **day_values):
        circuit_name = 'hc1' if 'heizkreis_1_sensoren' in options else 'hc2'
        h = Harness(options, values=values or {A: 18.5, B: 18.5})
        setback(h, circuit_name, kind)
        h.run(30)
        day(h, circuit_name, kind, **day_values)
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

    def failing_days(self, h, name, kind, days, levels, check=None, fresh=True):
        """Daily starts (8 h setback, then the day phase) with rooms flat at
        `levels`. check(number, end) runs just before and at each window end;
        returns at the last day's window end. fresh=False continues a day."""
        window = WINDOW[kind]
        if fresh:
            setback(h, name, kind)
            flat(h, 30, levels)
        for number in range(1, days + 1):
            if number > 1 or not fresh:
                flat(h, 16 * 60 - window, levels)
                setback(h, name, kind)
                flat(h, 8 * 60, levels)
            day(h, name, kind)
            flat(h, window - 1, levels)
            if check:
                check(number, False)
            flat(h, 1, levels)
            if check:
                check(number, True)

    def test_no_response_needs_failed_starts_on_three_days(self):
        for kind in ('radiator', 'floor'):
            with self.subTest(kind=kind):
                options, name = (hc1(A, B), 'hc1') if kind == 'radiator' else (hc2(A, B), 'hc2')
                h = Harness(options, values={A: 18.5, B: 18.5})
                onsets = []

                def check(number, end):
                    if not end:
                        self.assertNotIn('no_response', h.codes())
                        if number < 3:
                            self.assertNotIn('circuit_no_response', h.codes())
                        return
                    self.assertEqual(len(h.engine.response_fails[A]), number)
                    if number == 1:
                        self.assertFalse(h.codes() & {'no_response', 'circuit_no_response'})
                    if number >= 2:
                        row = h.rows('circuit_no_response')[0]
                        self.assertEqual((row['level'], row['count'], row['circuit']), ('Störung', 2, name))
                        onsets.append(h.now - row['held_s'])
                    if number == 2:
                        self.assertNotIn('no_response', h.codes())
                    if number == 3:
                        rows = h.rows('no_response')
                        self.assertEqual(len(rows), 2)
                        self.assertEqual((rows[0]['level'], rows[0]['room'], rows[0]['held_s']), ('Hinweis', None, 0))
                self.failing_days(h, name, kind, 3, {A: 18.5, B: 18.5}, check)
                self.assertEqual(onsets[0], onsets[1])   # the Störung lasted from day 2 to day 3

    def test_normal_heat_up_is_quiet(self):
        for kind, rise in (('radiator', 1.2), ('floor', 0.4)):
            with self.subTest(kind=kind):
                options = hc1(A, B) if kind == 'radiator' else hc2(A, B)
                h, advance = self.heat_up(options, kind, rise)
                h.run(400, until=advance)
                self.assertFalse(h.codes() & {'no_response', 'circuit_no_response'})
                self.assertFalse(h.engine.response_fails.get(A))

    def test_pump_off_or_cold_flow_disarms(self):
        for extra in ({'pump': False}, {'flow': 25.0}):   # flow < R + 8
            with self.subTest(extra=extra):
                h, _ = self.heat_up(hc1(A), **extra)
                h.run(120)
                self.assertNotIn(A, h.engine.windows)
                self.assertFalse(h.engine.response_fails.get(A))
        h, _ = self.heat_up(hc1(A), pump=None)   # unknown pump pauses, never judges
        h.run(100)
        self.assertIn(A, h.engine.windows)
        self.assertFalse(h.engine.response_fails.get(A))

    def test_a_room_near_its_target_is_not_judged(self):
        for value in (20.2, 19.6):        # 0.8 and 1.4 K below: below ARM_DEFICIT
            with self.subTest(value=value):
                h, _ = self.heat_up(hc1(A), values={A: value})
                h.step()
                self.assertNotIn(A, h.engine.windows)
                h.run(120)
                self.assertFalse(h.engine.response_fails.get(A))

    def test_min_rise_follows_sensor_resolution(self):
        def coarse(rise):
            h = Harness(hc1(A), values={A: 18.0})
            setback(h, 'hc1')
            for index in range(30):        # learn a 0.5 K step
                h.values[A] = 18.0 if index % 2 else 18.5
                h.step()
            self.assertEqual(h.engine.sensors[A].step_size(), 0.5)
            h.values[A] = 18.5
            h.run(20)
            day(h, 'hc1')
            h.run(30)
            h.values[A] = 18.5 + rise
            h.run(31)
            self.assertNotIn(A, h.engine.windows)
            return len(h.engine.response_fails[A])
        self.assertEqual(coarse(0.5), 1)    # 0.5 < 0.75: a failed start
        self.assertEqual(coarse(1.0), 0)    # warmed

    def test_airing_in_the_window_cancels_the_start(self):
        # The bedroom is aired at every start: no start counts, nothing fires.
        h = Harness(hc1(A, B), values={A: 18.5, B: 18.5})
        setback(h, 'hc1')
        flat(h, 30, {A: 18.5, B: 18.5})
        for _day in range(4):
            day(h, 'hc1')
            flat(h, 20, {A: 18.5, B: 18.5})
            self.assertIn(A, h.engine.windows)
            flat(h, 1, {A: 16.5, B: 18.5})      # a fast drop of 2 K
            self.assertNotIn(A, h.engine.windows)
            flat(h, 60, {A: 16.5, B: 18.5})
            flat(h, 16 * 60 - 81, {A: 18.5, B: 18.5})
            setback(h, 'hc1')
            flat(h, 8 * 60, {A: 18.5, B: 18.5})
        self.assertFalse(h.engine.response_fails.get(A))
        self.assertEqual(len(h.engine.response_fails[B]), 4)
        self.assertEqual(len(h.rows('no_response')), 1)            # B, the flat room
        self.assertNotIn('circuit_no_response', h.codes())          # one failing room is not the circuit

    def test_a_start_not_judged_does_not_reset_the_pattern(self):
        # Fails on days 1 and 2; day 3 the room starts near its target (not
        # judged); day 4 fails again: the third failed day, within 4 days.
        h = Harness(hc1(A), values={A: 18.5})
        self.failing_days(h, 'hc1', 'radiator', 2, {A: 18.5})
        self.failing_days(h, 'hc1', 'radiator', 1, {A: 19.6}, fresh=False)
        self.assertEqual(len(h.engine.response_fails[A]), 2)
        self.assertFalse(h.has('no_response'))
        for minute in range(16 * 60):           # back down slowly, no airing signature
            h.values[A] = round(19.6 - 1.1 * minute / (16 * 60), 2)
            h.step()
        setback(h, 'hc1')
        flat(h, 8 * 60, {A: 18.5})
        day(h, 'hc1')
        flat(h, 60, {A: 18.5})
        self.assertEqual(len(h.engine.response_fails[A]), 3)
        self.assertTrue(h.has('no_response'))

    def test_no_response_clears_at_the_day_level_and_expires(self):
        h = Harness(hc1(A), values={A: 18.5})
        self.failing_days(h, 'hc1', 'radiator', 3, {A: 18.5})
        self.assertTrue(h.has('no_response'))
        flat(h, 16, {A: 20.5})         # within 1 K of the day target
        self.assertFalse(h.has('no_response'))
        self.assertNotIn(A, h.engine.response_fails)   # the count starts again
        h = Harness(hc1(A), values={A: 18.5})
        self.failing_days(h, 'hc1', 'radiator', 3, {A: 18.5})
        self.assertTrue(h.has('no_response'))
        # Its window started 59 min before it ended; nothing else ends the
        # finding before 48 h after that start.
        h.set_circuit('hc1', status=0)
        flat(h, 47 * 60, {A: 18.5})
        self.assertTrue(h.has('no_response'))
        flat(h, 1, {A: 18.5})
        self.assertFalse(h.has('no_response'))

    def test_a_later_start_with_a_rise_clears(self):
        h = Harness(hc1(A, B), values={A: 18.5, B: 18.5})
        self.failing_days(h, 'hc1', 'radiator', 3, {A: 18.5, B: 18.5})
        self.assertTrue(h.has('no_response') and h.has('circuit_no_response'))
        flat(h, 16 * 60 - 60, {A: 18.5, B: 18.5})
        setback(h, 'hc1')
        flat(h, 8 * 60, {A: 18.5, B: 18.5})
        day(h, 'hc1')
        for minute in range(60):
            flat(h, 1, {A: 18.5 + minute / 60, B: 18.5})
        self.assertEqual([row['label'] for row in h.rows('no_response')], [h.engine.labels[B]])
        self.assertEqual(h.engine.response_fails[A], [])
        self.assertNotIn('circuit_no_response', h.codes())       # a room warmed: the circuit works

    def test_a_room_that_reaches_its_day_level_each_day_never_reports(self):
        # A door open every morning (or a slow start): flat for the hour after
        # the start, at the day level by noon. Each day forgets the failure.
        h = Harness(hc1(A, B), values={A: 18.5, B: 18.5})
        setback(h, 'hc1')
        flat(h, 30, {A: 18.5, B: 18.5})
        for _day in range(5):
            day(h, 'hc1')
            flat(h, 61, {A: 18.5, B: 18.5})
            self.assertEqual(len(h.engine.response_fails[A]), 1)
            for minute in range(180):
                level = 18.5 + 2.2 * minute / 180
                flat(h, 1, {A: level, B: level})
            flat(h, 16 * 60 - 241, {A: 20.7, B: 20.7})
            self.assertNotIn(A, h.engine.response_fails)
            for minute in range(8 * 60):
                level = 20.7 - 2.2 * minute / (8 * 60)
                if minute == 0:
                    setback(h, 'hc1')
                flat(h, 1, {A: level, B: level})
        self.assertFalse(h.codes() & {'no_response', 'circuit_no_response', 'room_cold'})

    def test_trigger_after_an_hour_without_supplying(self):
        h = Harness(hc1(A), values={A: 18.5})
        h.set_circuit('hc1', status=4)
        h.run(90)
        h.set_circuit('hc1', status=1)
        h.step()
        self.assertIn(A, h.engine.windows)
        h.run(60)
        self.assertEqual(len(h.engine.response_fails[A]), 1)
        h = Harness(hc1(A), values={A: 18.5})   # 30 min off: no trigger
        h.run(20)
        h.set_circuit('hc1', status=4)
        h.run(30)
        h.set_circuit('hc1', status=1)
        h.run(61)
        self.assertNotIn(A, h.engine.windows)
        self.assertFalse(h.engine.response_fails.get(A))


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
        # Setback (review 2026-11-23): the notice holds through the night instead
        # of ending every evening; the next day phase judges it again.
        h.set_circuit('hc1', phase='setback', target=18.0)
        for minute in range(10 * 60):
            h.step()
            self.assertTrue(h.has('limit_cold'), minute)
        h.set_circuit('hc1', phase='day', target=21.0, status=1)     # the circuit heats again
        h.step()
        self.assertFalse(h.has('limit_cold'))
        # No new notice starts during setback.
        h = Harness(hc1(A), values={A: 15.0})
        h.set_circuit('hc1', status=4, phase='setback', target=18.0)
        h.run(800)
        self.assertNotIn('limit_cold', h.codes())
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

    def test_frost_stays_while_a_frozen_room_sensor_is_unusable(self):
        # Audit 2026-11-18: one dropout ended the frost fault (and it returned
        # with a new onset); a sensor that dies in the cold ended it for good.
        h = Harness({'heizkreis_1_sensoren': [A, B]}, values={A: 4.0, B: 4.0})
        h.context_state, h.context = 'none', None
        self.assertIsNotNone(h.first('frost', 30))
        h.run(5)
        held = h.rows('frost')[0]['held_s']
        h.values[B] = 'unavailable'
        h.step()
        self.assertTrue(h.has('room_frost', 'Sensor 2'))
        # The same finding: its onset (held time) continues, no new identity.
        self.assertEqual([(row['count'], row['held_s'] > held) for row in h.rows('frost')], [(2, True)])
        h.run(30)
        self.assertTrue(h.has('frost'))
        # Bounded like its room's latch: after the pause limit both end.
        h.run(r.CONTEXT_FREE_PAUSE_S // 60 + 2)
        self.assertFalse(h.has('room_frost', 'Sensor 2'))
        self.assertFalse(h.has('frost'))

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

    def test_a_failed_read_never_completes_the_dwell(self):
        """HA-app review: a pause passed dwell 0, so one failed read turned a running
        timer into an immediate finding."""
        for pause in ('not_read', 'ha_unavailable'):
            h = Harness(hc1(A), values={A: 'unavailable'})
            h.run(5)
            if pause == 'not_read':
                h.values[A] = None          # this cycle's read failed
            else:
                h.ha = 'unavailable'        # Home Assistant not running: every timer pauses
            h.step()
            self.assertFalse(h.has('sensor_unavailable'), pause)
            h.values[A], h.ha = 'unavailable', 'running'
            minute = h.first('sensor_unavailable', 60)
            self.assertGreaterEqual(minute, 24, pause)  # the full 30 min of dwell, counted only while unavailable
        h = Harness(hc1(A), values={A: 'missing'})
        h.run(5)
        h.values[A] = None
        h.step()
        self.assertFalse(h.has('sensor_missing'))

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
        self.assertEqual(h.engine.sensors[A].unusable_reason(h.now), 'step')
        h.values[A] = 21.0
        h.step()                         # back again is a second step
        self.assertFalse(h.engine.sensors[A].usable(h.now))
        h.run(30)
        self.assertTrue(h.engine.sensors[A].usable(h.now))
        # One restart (two steps) is not a jumping sensor; a third jump within 24 h is.
        self.assertNotIn('sensor_jumping', h.codes())
        h.run(60)
        h.values[A] = 10.0
        h.step()
        self.assertTrue(h.has('sensor_jumping'))
        h.values[A] = 21.0
        h.step()                         # the fourth, which the finding lasts 24 h from
        h.run(24 * 60 - 1)
        self.assertTrue(h.has('sensor_jumping'))
        h.step()
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

    def test_a_sparse_sensor_at_an_open_window_is_not_jumping(self):
        # A sensor reporting every 10 min shows each airing as one 4 K dip that
        # is back 10 min later: discarded as a value, never counted as a jump.
        h = Harness(hc1(A), values={A: 21.0})
        h.reporting[A] = 60
        h.run(10)
        for _ in range(5):
            h.values[A] = 17.0
            h.run(10)
            h.values[A] = 20.8
            h.run(100)
        self.assertEqual(len(h.engine.sensors[A].spikes), 0)
        self.assertNotIn('sensor_jumping', h.codes())

    def test_an_on_change_sensor_that_changes_once_by_3_k(self):
        # Reports only on change: 21.0, then 17.5 once and nothing for 20 min.
        # The held-back value is taken after 15 min; nothing breaks (frost included).
        h = Harness({'heizkreis_1_sensoren': [A], 'haus_sensoren': [C], 'haus_frost_sensoren': [C]},
                    values={A: 21.0, C: 8.0})
        h.reporting[A] = 60
        h.run(10)
        h.values[A] = 17.5
        h.run(20)
        self.assertEqual(h.engine.sensors[A].last_accepted, 17.5)
        h.values[A] = 'unavailable'
        h.values[C] = 4.0
        self.assertIsNotNone(h.first('room_frost', 30))
        h = Harness(hc1(A), values={A: 21.0})
        h.reporting[A] = 60
        h.run(10)
        h.values[A] = 17.5
        h.run(1)
        h.values[A] = 'unavailable'
        h.run(30)
        self.assertEqual(h.engine.sensors[A].last_accepted, 17.5)

    def test_glitches_of_an_on_change_sensor_count(self):
        # Silent for 50 min, then +5 K for one minute and back: a glitch, three times.
        h = Harness(hc1(A), values={A: 21.0})
        h.reporting[A] = 60
        h.run(5)
        for _ in range(3):
            h.run(50)
            h.values[A] = 26.0
            h.step()
            h.values[A] = 21.0
            h.step()
        self.assertTrue(h.has('sensor_jumping'))

    def test_trend_of_an_on_change_sensor(self):
        # Reports every 10 min (on change): the trend still sees it warming.
        h = Harness(hc1(A), values={A: 17.0})
        h.reporting[A] = 60
        for minute in range(240):
            if minute % 10 == 0:
                h.values[A] = round(17.0 + 0.6 * minute / 60, 1)
            h.step()
        sensor = h.engine.sensors[A]
        self.assertGreaterEqual(sensor.trend(h.now, 3600), 0.5)
        self.assertIsNone(r.Sensor('sensor.x', 'hc1', 1).trend(h.now, 3600))

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
        self.assertFalse(h.engine.cold_dwell[A].active)
        self.assertEqual(h.engine.circuits_report()['hc1']['why'], 'type')
        h.set_circuit('hc1', type='radiator')
        self.assertEqual(first_dwell(h, A, 30), 11)   # paused, not reset
        h = Harness(hc1(A, heizkreis_1_art='fussboden'), values={A: 19.0})
        report = h.engine.circuits_report()
        h.step()
        report = h.engine.circuits_report()['hc1']
        self.assertEqual((report['type'], report['type_source']), ('floor', 'option'))
        self.assertEqual(first_dwell(h, A, 600), COLD_SETTLE['floor'] + 240)
        h = Harness(hc1(A, heizkreis_1_art='heizkoerper'), values={A: 19.0})
        h.set_circuit('hc1', type=None)
        self.assertEqual(first_dwell(h, A, 300), 211)

    def test_pause_limits_per_type(self):
        # Floor room_cold pauses up to cold_pause (24 h), as floor circuit_cold.
        for kind, options, limit in (('radiator', hc1(A), 120), ('floor', hc2(A), 24 * 60)):
            for gap, kept in ((limit, True), (limit + 2, False)):
                with self.subTest(kind=kind, gap=gap):
                    h = Harness(options, values={A: 19.0})
                    h.run(COLD_SETTLE[kind] + 1 + 30)
                    h.context_state = 'none'
                    h.run(gap)
                    h.context_state = 'ok'
                    dwell = 90 if kind == 'radiator' else 240
                    minute = first_dwell(h, A, 2000)
                    self.assertEqual(minute, dwell - 30 if kept else dwell + 1)

    def test_ha_outage_pauses_never_resets_or_credits(self):
        for ha, reason in (('starting', 'ha_starting'), ('unavailable', 'ha_api_unavailable')):
            with self.subTest(ha=ha):
                h = Harness(hc1(A), values={A: 19.0})
                h.run(121 + 60)
                h.ha = ha
                h.run(60)
                self.assertEqual((h.engine.monitor, h.engine.reason), ('unknown', reason))
                self.assertFalse(h.engine.cold_dwell[A].active)
                self.assertEqual(h.engine.circuits_report()['hc1']['why'], 'ha')
                h.ha = 'running'
                self.assertEqual(first_dwell(h, A, 60), 30)

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
        h.first('room_cold', 300 + PERSIST)
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
        # 2 is the allowance major of 1.6.8 (keys of 1); 3 is unknown.
        self.assertEqual(r.validate_context(json.dumps({**document, 'schema': 'harg-house-context/2'}))[0], 'ok')
        self.assertEqual(r.validate_context(json.dumps({**document, 'schema': 'harg-house-context/3'}))[0], 'unsupported')
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
