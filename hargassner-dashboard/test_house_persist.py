"""The room rules across an app restart (house_state.py, app 1.6.6).

Before 1.6.6 the rules kept their state in memory only: after a restart a
lasting no_response needed three new days, so the VM's row ended ("wieder in
Ordnung") and came back days later as a new problem. The scenarios use the
synthetic plant of test_house_scenarios: the app stops (the house and Home
Assistant go on, nothing is evaluated), the saved document goes through JSON as
on disk, and a new engine continues from it.
"""
import copy
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import house_monitor as hm
import house_rules as r
import house_state as hs
from house_test_support import Harness
from test_house_scenarios import DAY, HEATING, settled

BOOT = '0123abcd-0000-4000-8000-000000000001'
OTHER_BOOT = '0123abcd-0000-4000-8000-000000000002'
BEDROOM = 'sensor.schlafzimmer'


def stop(plant, boot=BOOT):
    """The app stops: its saved document, as read back from disk."""
    h = plant.h
    document = json.loads(json.dumps(hs.export(h.engine, mono=h.now, wall=h.wall, boot=boot)))
    h.stopped = True
    return document


def start(plant, document, *, boot=BOOT, mono=None, wall_step=0.0, since_boot=None):
    """The app starts again. mono: a new monotonic frame (the host rebooted);
    wall_step: the wall clock (HA's too) stepped meanwhile."""
    h = plant.h
    if mono is not None:
        h.now = mono
    h.wall += wall_step
    h.engine, outcome = hs.restore(h.options, document, mono=h.now, wall=h.wall, boot=boot, since_boot=since_boot)
    h.stopped = False
    return outcome


def restart(plant, minutes, **kwargs):
    document = stop(plant, kwargs.pop('saved_boot', BOOT))
    plant.run(minutes)
    return start(plant, document, **kwargs)


def row(plant, code, label=None):
    rows = [item for item in plant.h.findings if item['code'] == code and (label is None or item['label'] == label)]
    return rows[0] if rows else None


def since(plant, code, label=None):
    item = row(plant, code, label)
    return None if item is None else plant.h.wall - item['held_s']


def intervals(plant, start_minute):
    """(code, label) -> [first, last] minutes of each run of presence after start_minute."""
    runs = {}
    previous = {}
    for minute, code, label in plant.seen:
        if minute <= start_minute:
            continue
        key = (code, label)
        if key in previous and previous[key] == minute - 1:
            runs[key][-1][1] = minute
        else:
            runs.setdefault(key, []).append([minute, minute])
        previous[key] = minute
    return runs


_CACHE = {}


def cached(key, build):
    """Deep copies of plants that take seconds to run up."""
    if key not in _CACHE:
        _CACHE[key] = build()
    return copy.deepcopy(_CACHE[key])


def settled_plant():
    def build():
        plant = settled()
        # The worker refreshes the labels from the sensor names (as a restore does).
        plant.h.engine.refresh_labels()
        return plant
    return cached('settled', build)


def dead_bedroom_until_no_response():
    def build():
        plant = settled_plant()
        plant.rooms[2].dead = True
        plant.to(4, 9)
        return plant
    plant = cached('dead', build)
    label = plant.h.engine.labels[BEDROOM]
    assert row(plant, 'no_response', label) is not None and row(plant, 'room_cold', label) is not None
    return plant, label


class ContinueTests(unittest.TestCase):
    def test_a_lasting_no_response_continues_with_its_onset(self):
        plant, label = dead_bedroom_until_no_response()
        response, cold = since(plant, 'no_response', label), since(plant, 'room_cold', label)
        self.assertEqual(restart(plant, 5), 'state_restored')
        # Every evaluation after the restart still has both rows with the same
        # onset: the VM keeps its row (same since), nothing is "wieder in Ordnung".
        for _minute in range(DAY + 3 * 60):
            plant.tick()
            self.assertEqual(since(plant, 'no_response', label), response)
            self.assertEqual(since(plant, 'room_cold', label), cold)
        self.assertNotIn('circuit_no_response', plant.h.codes())

    def test_without_a_saved_state_the_pattern_is_learned_again(self):
        # What happened before 1.6.6 (and still happens on a first start or
        # after a reset): the finding is gone for days.
        plant, label = dead_bedroom_until_no_response()
        stop(plant)
        plant.run(5)
        plant.h.engine, plant.h.stopped = r.HouseEngine(plant.h.options), False
        absent = 0
        for _minute in range(DAY + 3 * 60):
            plant.tick()
            absent += row(plant, 'no_response', label) is None
        self.assertEqual(absent, DAY + 3 * 60)

    def test_a_circuit_fault_continues(self):
        plant = settled_plant()
        plant.effect = False
        plant.to(3, 9)
        fault = since(plant, 'circuit_no_response')
        self.assertIsNotNone(fault)
        self.assertEqual(restart(plant, 10), 'state_restored')
        for _minute in range(12 * 60):
            plant.tick()
            self.assertEqual(since(plant, 'circuit_no_response'), fault)
        self.assertEqual(row(plant, 'circuit_no_response')['level'], 'Störung')

    def test_a_restart_is_an_outage_of_the_same_length(self):
        # The same plant (same seed, same physics) once with the app stopped
        # and once with Home Assistant unavailable for the same minutes: the
        # same findings at the same times (one minute of tolerance, the first
        # evaluation after a restart credits nothing). The bedroom is repaired
        # at day 5 00:00: the fixed problem ends on the same schedule.
        cases = ((3, 5, 50, 30),        # across the 06:00 heating start (windows, triggers)
                 (4, 12, 0, 180),       # longer than the radiator pause limit (2 h)
                 (4, 12, 0, 20 * 60))   # most of a day
        for day, hour, minute, down in cases:
            with self.subTest(day=day, hour=hour, down=down):
                plants = []
                for restarted in (True, False):
                    plant = settled_plant()
                    plant.rooms[2].dead = True
                    plant.to(day, hour, minute)
                    if restarted:
                        self.assertEqual(restart(plant, down), 'state_restored')
                    else:
                        plant.h.ha = 'unavailable'
                        plant.run(down)
                        plant.h.ha = 'running'
                    plant.to(5, 0)
                    plant.rooms[2].dead = False
                    plant.to(6, 12)
                    plants.append(plant)
                origin = day * DAY + hour * 60 + minute + down + 1
                restarted, outage = (intervals(plant, origin) for plant in plants)
                self.assertEqual(set(restarted), set(outage))
                for key, runs in outage.items():
                    self.assertEqual(len(restarted[key]), len(runs), key)
                    for (first, last), (first2, last2) in zip(restarted[key], runs):
                        self.assertLessEqual(abs(first - first2), 1, key)
                        self.assertLessEqual(abs(last - last2), 1, key)
                # Repaired: nothing of the heating rules is left at the end.
                self.assertFalse(plants[0].h.codes() & HEATING)
                self.assertFalse(plants[1].h.codes() & HEATING)

    def test_a_crash_after_the_last_save_is_an_outage_from_that_save(self):
        # Owner decision 2026-11-18: the worker saves every 5 minutes. A stop
        # without the final save (a crash, a power cut) restores the last
        # periodic save: what the app saw after it is lost and the whole time
        # from that save counts as stopped, exactly as an HA outage from the
        # save on. `ran`: minutes the app still evaluated after the save.
        self.assertEqual(hs.SAVE_PERIOD_S, 300)
        cases = ((3, 5, 50, 5, 10),     # saved 05:50, ran over the 06:00 start until 05:55, back 06:05
                 (4, 6, 55, 4, 0),      # crash at 06:59 in a heating watch, restarted at once
                 (4, 12, 0, 5, 1))
        for day, hour, minute, ran, down in cases:
            with self.subTest(day=day, hour=hour, minute=minute, ran=ran, down=down):
                plants = []
                for crashed in (True, False):
                    plant = settled_plant()
                    plant.rooms[2].dead = True
                    plant.to(day, hour, minute)
                    if crashed:
                        document = json.loads(json.dumps(
                            hs.export(plant.h.engine, mono=plant.h.now, wall=plant.h.wall, boot=BOOT)))
                        plant.run(ran)                  # still evaluating, not saved again
                        plant.h.stopped = True
                        plant.run(down)
                        self.assertEqual(start(plant, document), 'state_restored')
                    else:
                        plant.h.ha = 'unavailable'
                        plant.run(ran + down)
                        plant.h.ha = 'running'
                    plant.to(5, 0)
                    plant.rooms[2].dead = False
                    plant.to(6, 12)
                    plants.append(plant)
                origin = day * DAY + hour * 60 + minute + ran + down + 1
                crashed, outage = (intervals(plant, origin) for plant in plants)
                self.assertEqual(set(crashed), set(outage))
                for key, runs in outage.items():
                    self.assertEqual(len(crashed[key]), len(runs), key)
                    for (first, last), (first2, last2) in zip(crashed[key], runs):
                        self.assertLessEqual(abs(first - first2), 1, key)
                        self.assertLessEqual(abs(last - last2), 1, key)
                self.assertFalse(plants[0].h.codes() & HEATING)

    def test_a_crash_keeps_the_onset_of_a_lasting_finding(self):
        plant, label = dead_bedroom_until_no_response()
        response = since(plant, 'no_response', label)
        document = json.loads(json.dumps(hs.export(plant.h.engine, mono=plant.h.now, wall=plant.h.wall, boot=BOOT)))
        plant.run(5)
        plant.h.stopped = True
        plant.run(2)
        self.assertEqual(start(plant, document), 'state_restored')
        for _minute in range(60):
            plant.tick()
            self.assertEqual(since(plant, 'no_response', label), response)

    def test_a_finding_still_ends_on_schedule_after_a_restart(self):
        # no_response ends 48 h after the last start without effect, the stop
        # counted: from day 4 09:00 the circuit is off (no start is judged any
        # more) and the app is stopped for 6 hours meanwhile.
        plant, label = dead_bedroom_until_no_response()
        h = plant.h
        last_wall = h.wall - (h.now - h.engine.no_response[BEDROOM].last)
        plant.schedule = lambda _minute: ('setback', 17.0, 0)
        self.assertEqual(restart(plant, 6 * 60), 'state_restored')
        plant.tick()
        self.assertIsNotNone(row(plant, 'no_response', label))
        while row(plant, 'no_response', label) is not None:
            plant.tick()
            self.assertLess(h.wall, last_wall + 3 * 86400)
        self.assertEqual(h.wall, last_wall + r.NO_RESPONSE_EXPIRY_S)

    def test_too_long_a_stop_starts_fresh(self):
        plant, label = dead_bedroom_until_no_response()
        self.assertEqual(restart(plant, 24 * 60 + 1), 'state_too_old')
        plant.tick()
        self.assertIsNone(row(plant, 'no_response', label))
        self.assertEqual(plant.h.engine.no_response, {})


class ClockTests(unittest.TestCase):
    def continues(self, plant, label, stopped_seconds, before):
        plant.tick()
        held = row(plant, 'no_response', label)['held_s']
        self.assertEqual(held, before + stopped_seconds + 60)

    def test_wall_clock_steps_during_the_stop_change_nothing_on_the_same_boot(self):
        for step in (-DAY * 60.0, 3 * DAY * 60.0):
            with self.subTest(step=step):
                plant, label = dead_bedroom_until_no_response()
                before = row(plant, 'no_response', label)['held_s']
                self.assertEqual(restart(plant, 10, wall_step=step), 'state_restored')
                self.continues(plant, label, 600, before)

    def test_after_a_reboot_the_wall_clock_gives_the_stopped_time(self):
        plant, label = dead_bedroom_until_no_response()
        before = row(plant, 'no_response', label)['held_s']
        self.assertEqual(restart(plant, 10, boot=OTHER_BOOT, mono=50.0), 'state_restored')
        self.continues(plant, label, 600, before)

    def test_after_a_reboot_a_small_step_back_counts_as_no_stop(self):
        plant, label = dead_bedroom_until_no_response()
        before = row(plant, 'no_response', label)['held_s']
        self.assertEqual(restart(plant, 0, boot=OTHER_BOOT, mono=50.0, wall_step=-120.0), 'state_restored')
        self.continues(plant, label, 0, before)

    def test_after_a_reboot_a_clock_behind_the_save_starts_fresh(self):
        plant, label = dead_bedroom_until_no_response()
        self.assertEqual(restart(plant, 10, boot=OTHER_BOOT, mono=50.0, wall_step=-3600.0), 'state_clock')
        plant.tick()
        self.assertIsNone(row(plant, 'no_response', label))

    def test_after_a_reboot_a_clock_far_ahead_starts_fresh(self):
        plant, label = dead_bedroom_until_no_response()
        self.assertEqual(restart(plant, 10, boot=OTHER_BOOT, mono=50.0, wall_step=2 * DAY * 60.0), 'state_too_old')
        plant.tick()
        self.assertFalse(plant.h.codes())

    def test_after_a_reboot_at_least_the_time_since_the_boot_has_passed(self):
        # A host without a battery clock starts near its last shutdown time:
        # the wall clock shows no gap, but the host has been up for 3 hours.
        plant, label = dead_bedroom_until_no_response()
        before = row(plant, 'no_response', label)['held_s']
        self.assertEqual(restart(plant, 0, boot=OTHER_BOOT, mono=50.0, since_boot=3 * 3600.0), 'state_restored')
        self.continues(plant, label, 3 * 3600, before)
        # The uptime counts only after a reboot, never on the same boot.
        plant, label = dead_bedroom_until_no_response()
        before = row(plant, 'no_response', label)['held_s']
        self.assertEqual(restart(plant, 10, since_boot=3 * 3600.0), 'state_restored')
        self.continues(plant, label, 600, before)

    def test_unknown_boot_uses_the_wall_clock(self):
        plant, label = dead_bedroom_until_no_response()
        before = row(plant, 'no_response', label)['held_s']
        self.assertEqual(restart(plant, 10, saved_boot=None, boot=None), 'state_restored')
        self.continues(plant, label, 600, before)

    def test_a_monotonic_clock_behind_the_save_on_the_same_boot_starts_fresh(self):
        plant, label = dead_bedroom_until_no_response()
        self.assertEqual(restart(plant, 10, mono=50.0), 'state_clock')
        plant.tick()
        self.assertIsNone(row(plant, 'no_response', label))


TWO_CIRCUITS = {
    'heizkreis_1_sensoren': ['sensor.wohnzimmer', 'sensor.kueche', 'sensor.schlafzimmer', 'sensor.bad'],
    'heizkreis_2_sensoren': ['sensor.flur', 'sensor.buero'],
    'haus_sensoren': ['sensor.keller'], 'haus_frost_sensoren': ['sensor.keller'],
    'raumueberwachung_modus': 'melden',
}


class OptionChangeTests(unittest.TestCase):
    """A changed selection resets exactly the rooms and circuits it changes."""

    def saved(self):
        plant, label = dead_bedroom_until_no_response()
        h = plant.h
        # Carry the radiator plant's state over into the two-circuit selection
        # first (hc2 and the cellar are new there), then work from that.
        options = r.parse_house_options(TWO_CIRCUITS)
        document = hs.export(h.engine, mono=h.now, wall=h.wall, boot=BOOT)
        engine, outcome = hs.restore(options, document, mono=h.now, wall=h.wall, boot=BOOT)
        self.assertEqual(outcome, 'state_partly_restored')
        self.assertIn(BEDROOM, engine.no_response)
        self.assertFalse(engine.sensors['sensor.flur'].history)
        # hc1 has the same rooms and type: it continues. hc2 got rooms: its
        # timing continues (same type), its checks over the rooms start fresh.
        self.assertTrue(engine.circuits['hc1'].seen)
        self.assertTrue(engine.circuits['hc2'].seen)
        self.assertEqual(engine.circuits['hc2'].starts, {})
        self.engine_before = engine
        return hs.export(engine, mono=h.now, wall=h.wall, boot=BOOT), h.now, h.wall

    def changed(self, **changes):
        document, now, wall = self.saved()
        options = dict(TWO_CIRCUITS, **changes)
        engine, outcome = hs.restore(r.parse_house_options(options), json.loads(json.dumps(document)),
                                     mono=now, wall=wall, boot=BOOT)
        return engine, outcome

    def kept(self, engine, entity):
        return len(engine.sensors[entity].history) > 0

    def test_mode_change_keeps_everything(self):
        engine, outcome = self.changed(raumueberwachung_modus='beobachten')
        self.assertEqual(outcome, 'state_restored')
        self.assertIn(BEDROOM, engine.no_response)
        self.assertTrue(engine.circuits['hc1'].seen)

    def test_a_room_marked_cooler_starts_fresh_with_its_circuit_checks(self):
        engine, outcome = self.changed(kuehlere_raeume=['sensor.kueche'])
        self.assertEqual(outcome, 'state_partly_restored')
        self.assertFalse(self.kept(engine, 'sensor.kueche'))
        self.assertEqual([code for code, entity in engine.latches if entity == 'sensor.kueche'], [])
        # The checks over hc1's rooms start fresh, its timing and the other
        # rooms continue.
        hc1 = engine.circuits['hc1']
        self.assertTrue(hc1.seen)
        self.assertIsNotNone(hc1.settle_start)
        self.assertEqual((hc1.starts, hc1.failed_starts, hc1.circuit_no_response), ({}, [], None))
        self.assertIn(BEDROOM, engine.no_response)
        self.assertTrue(engine.response_fails[BEDROOM])
        self.assertTrue(self.kept(engine, 'sensor.wohnzimmer'))
        self.assertTrue(self.kept(engine, 'sensor.bad'))
        # No window of the old circuit's heating starts is left.
        self.assertEqual(engine.windows, {})

    def test_a_changed_circuit_type_starts_its_rooms_fresh(self):
        engine, outcome = self.changed(heizkreis_1_art='fussboden')
        self.assertEqual(outcome, 'state_partly_restored')
        for entity in TWO_CIRCUITS['heizkreis_1_sensoren']:
            self.assertFalse(self.kept(engine, entity), entity)
        self.assertEqual(engine.no_response, {})
        self.assertFalse(engine.circuits['hc1'].seen)

    def test_a_moved_room_starts_fresh_and_only_it(self):
        engine, outcome = self.changed(heizkreis_1_sensoren=['sensor.wohnzimmer', 'sensor.kueche', BEDROOM],
                                       heizkreis_2_sensoren=['sensor.flur', 'sensor.buero', 'sensor.bad'])
        self.assertEqual(outcome, 'state_partly_restored')
        self.assertFalse(self.kept(engine, 'sensor.bad'))
        self.assertTrue(self.kept(engine, 'sensor.wohnzimmer'))
        self.assertIn(BEDROOM, engine.no_response)
        self.assertTrue(engine.circuits['hc1'].seen)
        self.assertEqual(engine.circuits['hc1'].failed_rooms, set())

    def test_a_removed_room_leaves_the_others(self):
        engine, outcome = self.changed(heizkreis_1_sensoren=['sensor.wohnzimmer', 'sensor.kueche', BEDROOM])
        self.assertNotIn('sensor.bad', engine.sensors)
        self.assertIn(BEDROOM, engine.no_response)
        self.assertTrue(self.kept(engine, 'sensor.kueche'))
        self.assertEqual(outcome, 'state_partly_restored')   # hc1 has other rooms now

    def test_an_unchanged_selection_restores_everything_exactly(self):
        document, now, wall = self.saved()
        engine, outcome = hs.restore(r.parse_house_options(TWO_CIRCUITS), json.loads(json.dumps(document)),
                                     mono=now, wall=wall, boot=BOOT)
        self.assertEqual(outcome, 'state_restored')
        again = hs.export(engine, mono=now, wall=wall, boot=BOOT)
        self.assertEqual(again['engine'], document['engine'])


def night_step(engine, now, wall, values):
    """One evaluation at 23:00 with a heating radiator circuit (warm checks)."""
    from house_test_support import circuit, context_document, stamp
    _status, context = r.validate_context(json.dumps(context_document(hc1=circuit(flow=45.0))))
    samples = {entity: {'entity_id': entity, 'state': f'{values[entity]:.2f}', 'last_updated': stamp(wall),
                        'last_reported': stamp(wall), 'attributes': {'unit_of_measurement': '°C', 'friendly_name': entity}}
               for entity in engine.options.entities}
    return engine.step(now, wall=wall, ha='running', samples=samples, context=context, context_state='ok',
                       local_minutes=23 * 60)


class ReviewRegressionTests(unittest.TestCase):
    """Cases from the independent review of the first candidate."""

    def test_unchanged_rooms_keep_their_findings_when_a_room_is_added(self):
        # The first candidate restarted the whole circuit, so its warm settle
        # began again and ended the room_warm notices of the unchanged rooms.
        options = r.parse_house_options({'heizkreis_1_sensoren': ['sensor.a', 'sensor.b'],
                                         'heizkreis_1_art': 'heizkoerper'})
        engine = r.HouseEngine(options)
        values = {'sensor.a': 24.0, 'sensor.b': 24.0, 'sensor.c': 21.0}
        now, wall = 100_000.0, 2e9
        for minute in range(6 * 60):
            rows = night_step(engine, now + minute * 60, wall + minute * 60, values)
        self.assertEqual({(row['code'], row['label']) for row in rows if row['code'] == 'room_warm'},
                         {('room_warm', 'Sensor 1'), ('room_warm', 'Sensor 2')})
        now, wall = now + 6 * 3600, wall + 6 * 3600
        document = json.loads(json.dumps(hs.export(engine, mono=now, wall=wall, boot=BOOT)))
        added = r.parse_house_options({'heizkreis_1_sensoren': ['sensor.a', 'sensor.b', 'sensor.c'],
                                       'heizkreis_1_art': 'heizkoerper'})
        restored, outcome = hs.restore(added, document, mono=now + 60, wall=wall + 60, boot=BOOT)
        self.assertEqual(outcome, 'state_partly_restored')
        for minute in range(1, 31):
            rows = night_step(restored, now + 60 * minute, wall + 60 * minute, values)
            self.assertEqual(len([row for row in rows if row['code'] == 'room_warm']), 2, minute)

    def test_every_field_survives_the_round_trip(self):
        # Fills every field of the engine, its sensors and circuits and compares
        # field by field (the documented compact forms by their effect).
        from collections import deque
        options = r.parse_house_options({'heizkreis_1_sensoren': ['sensor.a', 'sensor.b'], 'haus_sensoren': ['sensor.k'],
                                         'haus_frost_sensoren': ['sensor.k'], 'heizkreis_1_art': 'heizkoerper'})
        engine = r.HouseEngine(options)
        now = 500_000.0

        def latch(active, since, onset=None, total=10.0, paused=5.0):
            item = r.Latch()
            item.active, item.timer.since, item.timer.total, item.timer.paused, item.onset = active, since, total, paused, onset
            return item
        for index, sensor in enumerate(engine.sensors.values()):
            for k in range(230):   # within HISTORY_KEEP_S: kept whole
                sensor.ingest(now - 230 * 60 + k * 60, 20 + (k % 3) * 0.1)
            sensor.pending = (now - 100, 25.0, 20.1, now - 160)
            sensor.spikes = deque([now - 5000, now - 4000, now - 3000])
            sensor.jump_first, sensor.jump_last = now - 5000, now - 3000
            sensor.step_until, sensor.airing_drop, sensor.airing_start, sensor.airing_ref = now + 500, now - 50, now - 200, 21.5
            sensor.presence, sensor.numeric, sensor.implausible = 'ok', True, False
            sensor.in_range_since, sensor.battery, sensor.name = now - 7, 9.0, f'Raum {index}'
            sensor.missing, sensor.unavailable = latch(False, now - 30), latch(False, None, total=0.0, paused=0.0)
            sensor.implausible_latch, sensor.battery_latch = latch(True, now - 900), latch(True, now - 4000)
            sensor.stale_active, sensor.last_key, sensor.last_update = True, '2026-11-18T00:00:00+00:00', now - 60
        for entity in ('sensor.a', 'sensor.b', 'sensor.k'):
            engine.latches[('room_frost', entity)] = latch(True, now - 3000, {'room': 4.5})
        engine.latches[('room_cold', 'sensor.a')] = latch(True, now - 3000, {'room': 18.0, 'target': 21.0})
        engine.frost = (now - 2000, {'room': 4.5, 'count': 3})
        hc1 = engine.circuits['hc1']
        hc1.last_phase, hc1.last_target, hc1.last_mode, hc1.seen = 'day', 21.0, 'auto', True
        hc1.settle_start, hc1.last_upper, hc1.warm_settle_start, hc1.off_since = now - 9000, 21.0, now - 8000, now - 30
        hc1.triggers, hc1.judged, hc1.why, hc1.unknown_since = 7, 'unknown', 'phase', now - 120
        hc1.basis_unknown = deque([(now - 7200, 60.0), (now - 3600, 60.0)])
        hc1.circuit_cold = latch(True, now - 500, {'room': 17.0, 'target': 21.0, 'count': 2})
        hc1.circuit_warm = latch(False, now - 500)
        hc1.starts[('hc1', 7)] = {'start': now - 1200, 'armed': {'sensor.a'}, 'fail': {'sensor.b'}, 'pass': set(), 'usable': 2}
        window = r.Window(('hc1', 7), now - 1200, 19.0)
        window.active, window.paused, window.samples = 600.0, 60.0, [(0.0, 19.0), (600.0, 19.2)]
        engine.windows['sensor.a'] = window
        engine.no_response['sensor.b'] = r.NoResponse(('hc1', 5), now - 90000, now - 80000)
        engine.response_fails['sensor.b'] = [now - 300000, now - 200000, now - 100000]
        engine.cold_dwell['sensor.a'] = latch(True, now - 6000)
        engine.cold_since['sensor.a'], engine.cold_seen['sensor.a'], engine.pump_off['sensor.a'] = now - 50000, now - 60, now - 100
        hc1.failed_starts, hc1.failed_rooms, hc1.last_kind = [now - 100000], {'sensor.a', 'sensor.b'}, 'radiator'
        hc1.no_response_last, hc1.absent_since = now - 100000, now - 10
        hc1.circuit_no_response = (('hc1', 6), now - 100000, 2, ('sensor.a', 'sensor.b'))
        engine.all_unavailable = latch(False, now - 100)
        engine.refresh_labels()
        document = json.loads(json.dumps(hs.export(engine, mono=now, wall=2e9, boot=BOOT)))
        # No stop (frames and ages are covered by the clock tests): every field must come back as it was.
        restored, outcome = hs.restore(options, document, mono=now, wall=2e9, boot=OTHER_BOOT)
        self.assertEqual(outcome, 'state_restored')

        def plain(value, shift):
            if isinstance(value, float):
                return round(value + shift, 3)
            if isinstance(value, (list, tuple, deque)):
                return [plain(item, shift) for item in value]
            if isinstance(value, set):
                return sorted(value)
            if isinstance(value, dict):
                return {str(key): plain(item, shift) for key, item in value.items()}
            if isinstance(value, r.Latch):
                return [plain(value.timer.since, shift), value.timer.total, value.timer.paused, value.active, value.onset]
            if isinstance(value, (r.Window, r.NoResponse)):
                return {key: plain(getattr(value, key), shift) for key in value.__slots__}
            return value
        compact = {'deltas', 'delta_counts'}   # compared by step_size/updates below
        derived = {'_types'}                    # set from each circuit's last_kind

        def compare(owner, before, after, names):
            for name in names:
                if name in compact | derived:
                    continue
                a, b = getattr(before, name), getattr(after, name)
                self.assertEqual(plain(a, 0), plain(b, 0), (owner, name))
        skip = {'sensors', 'circuits', 'options'}
        compare('engine', engine, restored, [name for name in vars(engine) if name not in skip])
        for entity, sensor in engine.sensors.items():
            compare(entity, sensor, restored.sensors[entity], vars(sensor))
            self.assertEqual(restored.sensors[entity].step_size(), sensor.step_size())
            self.assertEqual(restored.sensors[entity].updates, sensor.updates)
        for name, circuit in engine.circuits.items():
            compare(name, circuit, restored.circuits[name], vars(circuit))
        self.assertEqual(restored._types, {'hc1': 'radiator', 'hc2': None})

    def test_the_seven_day_basis_count_expires_oldest_first(self):
        options = r.parse_house_options({'heizkreis_1_sensoren': ['sensor.a']})
        engine = r.HouseEngine(options)
        from collections import deque
        engine.circuits['hc1'].basis_unknown = deque([(1000.0 + 3600 * hour, 60.0) for hour in range(5)])
        restored, _outcome = hs.restore(options, hs.export(engine, mono=20_000.0, wall=2e9, boot=BOOT),
                                        mono=20_000.0, wall=2e9, boot=BOOT)
        times = [when for when, _seconds in restored.circuits['hc1'].basis_unknown]
        self.assertEqual(times, sorted(times))

    def test_frost_continues_only_while_two_restored_rooms_are_in_frost(self):
        options = r.parse_house_options({'heizkreis_1_sensoren': ['sensor.a', 'sensor.b'], 'haus_sensoren': ['sensor.k'],
                                         'haus_frost_sensoren': ['sensor.k']})
        engine = r.HouseEngine(options)
        now = 50_000.0
        for entity in ('sensor.a', 'sensor.b'):
            item = engine.latch('room_frost', entity)
            item.active, item.timer.since, item.onset = True, now - 1200, {'room': 4.5}
        engine.frost = (now - 600, {'room': 4.5, 'count': 2})
        document = json.loads(json.dumps(hs.export(engine, mono=now, wall=2e9, boot=BOOT)))
        kept, _outcome = hs.restore(options, document, mono=now, wall=2e9, boot=BOOT)
        self.assertEqual(kept.frost, engine.frost)
        # sensor.b moves to the house list: it starts fresh, one room is left in frost.
        moved = r.parse_house_options({'heizkreis_1_sensoren': ['sensor.a'], 'haus_sensoren': ['sensor.k', 'sensor.b'],
                                       'haus_frost_sensoren': ['sensor.k', 'sensor.b']})
        fresh, outcome = hs.restore(moved, document, mono=now, wall=2e9, boot=BOOT)
        self.assertEqual(outcome, 'state_partly_restored')
        self.assertIsNone(fresh.frost)

    def test_a_start_waiting_for_a_room_without_its_window_is_dropped_unjudged(self):
        options = r.parse_house_options({'heizkreis_1_sensoren': ['sensor.a', 'sensor.b', 'sensor.c']})
        engine = r.HouseEngine(options)
        now = 50_000.0
        engine.circuits['hc1'].starts[('hc1', 3)] = {'start': now - 600, 'armed': {'sensor.c'},
                                                     'fail': {'sensor.a', 'sensor.b'}, 'pass': set(), 'usable': 3}
        restored, _outcome = hs.restore(options, hs.export(engine, mono=now, wall=2e9, boot=BOOT),
                                        mono=now, wall=2e9, boot=BOOT)
        circuit = restored.circuits['hc1']
        self.assertEqual((circuit.starts, circuit.failed_starts, circuit.circuit_no_response), ({}, [], None))


def healthy_step(engine, now=20_000.0, wall=2_000_100_000.0):
    from house_test_support import context_document, stamp
    status, context = r.validate_context(json.dumps(context_document()))
    samples = {entity: {'entity_id': entity, 'state': '21.00', 'last_updated': stamp(wall), 'last_reported': stamp(wall),
                        'attributes': {'unit_of_measurement': '°C', 'friendly_name': entity}}
               for entity in engine.options.entities}
    return engine.step(now, wall=wall, ha='running', samples=samples, context=context, context_state='ok',
                       local_minutes=600)


class CorruptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, 'house_state.json')
        self.options = r.parse_house_options(TWO_CIRCUITS)
        self.lines = []

    def tearDown(self):
        self.directory.cleanup()

    def monitor(self, boot=BOOT, mono=50_000.0, wall=2_000_100_000.0):
        return hm.HouseMonitor(self.options, token_source=lambda: None, clock=lambda: wall, monotonic=lambda: mono,
                               log=self.lines.append, state_path=self.path, boot_id=lambda: boot)

    def document(self):
        """The bedroom plant in the two-circuit selection, saved 100 s before the monitor starts."""
        plant, _label = dead_bedroom_until_no_response()
        h = plant.h
        engine, outcome = hs.restore(self.options, hs.export(h.engine, mono=h.now, wall=h.wall, boot=BOOT),
                                     mono=49_900.0, wall=h.wall, boot=OTHER_BOOT)
        assert outcome == 'state_partly_restored', outcome
        return hs.export(engine, mono=49_900.0, wall=2_000_099_900.0, boot=BOOT)

    def write(self, raw):
        with open(self.path, 'wb') as stream:
            stream.write(raw)
        os.chmod(self.path, 0o600)

    def forged(self, change):
        document = self.document()
        body = {key: value for key, value in document.items() if key != 'sha256'}
        change(body)
        return hs.rules.canonical({**body, 'sha256': hs.checksum(body)}).encode()

    def assertFresh(self, reason):
        monitor = self.monitor()
        self.assertIn(f'House monitor: {reason}.', self.lines)
        self.assertIsNotNone(monitor.engine)
        self.assertEqual(monitor.engine.no_response, {})
        self.assertEqual(healthy_step(monitor.engine), [])

    def test_the_saved_document_is_restored(self):
        self.write(hs.rules.canonical(self.document()).encode())
        monitor = self.monitor()
        self.assertEqual(self.lines, ['House monitor: state_restored.'])
        self.assertIn(BEDROOM, monitor.engine.no_response)

    def test_unreadable_files_start_fresh(self):
        good = hs.rules.canonical(self.document()).encode()
        cases = {'garbage': b'\x00\xff not json', 'truncated': good[:len(good) // 2], 'empty': b'',
                 'list': b'[]', 'nan': good.replace(b'"saved_wall":', b'"saved_wall":NaN,"x":', 1),
                 'checksum': good.replace(b'"room":', b'"room": ', 1).replace(b'"sha256":"', b'"sha256":"0', 1),
                 'bit flip': good.replace(b'"same_count":', b'"same_count":1', 1)}
        for name, raw in cases.items():
            with self.subTest(name):
                self.lines.clear()
                self.write(raw)
                self.assertFresh('state_unreadable')

    def test_documents_of_another_schema_or_rule_set_start_fresh(self):
        for name, change in (('schema', lambda body: body.update(schema='harg-house-state/2')),
                             ('rules', lambda body: body.update(rules='0' * 64))):
            with self.subTest(name):
                self.lines.clear()
                self.write(self.forged(change))
                self.assertFresh('state_incompatible')

    def test_a_consistent_checksum_over_bad_values_starts_fresh(self):
        def onset_list(body):
            room = body['engine']['rooms'][BEDROOM]['room']
            room['latches']['room_cold'][4] = [1, 2]

        def active_without_onset(body):
            body['engine']['circuits']['hc1']['circuit_cold'] = [0.0, 0.0, 0.0, True, None]

        def foreign_member(body):
            body['engine']['circuits']['hc1']['failed_rooms'] = ['sensor.flur']

        def unknown_latch(body):
            body['engine']['rooms'][BEDROOM]['room']['latches']['explode'] = [None, 0.0, 0.0, False, None]

        def huge_history(body):
            body['engine']['rooms'][BEDROOM]['sensor']['history'] = [[0.0, 20.0]] * (hs.HISTORY_KEEP_MAX + 1)

        def string_time(body):
            body['engine']['rooms'][BEDROOM]['room']['no_response']['onset'] = '0'

        for change in (onset_list, active_without_onset, foreign_member, unknown_latch, huge_history, string_time):
            with self.subTest(change.__name__):
                self.lines.clear()
                self.write(self.forged(change))
                self.assertFresh('state_unreadable')

    def test_files_that_are_not_ours_are_not_read(self):
        good = hs.rules.canonical(self.document()).encode()
        other = os.path.join(self.directory.name, 'elsewhere.json')
        with open(other, 'wb') as stream:
            stream.write(good)
        os.symlink(other, self.path)
        self.assertFresh('state_unreadable')
        os.unlink(self.path)
        os.mkdir(self.path)
        self.lines.clear()
        self.assertFresh('state_unreadable')
        os.rmdir(self.path)
        self.write(good)
        os.chmod(self.path, 0o666)
        self.lines.clear()
        self.assertFresh('state_unreadable')

    def test_an_oversized_file_is_not_read(self):
        self.write(b' ' * (hs.STATE_MAX_BYTES + 1))
        self.assertFresh('state_unreadable')

    def test_no_file_starts_fresh_quietly(self):
        monitor = self.monitor()
        self.assertEqual(self.lines, [])
        self.assertEqual(monitor.engine.no_response, {})

    def test_a_failing_restore_never_stops_the_monitor(self):
        self.write(hs.rules.canonical(self.document()).encode())
        with patch.object(hs, 'restore', side_effect=RuntimeError('boom')):
            monitor = self.monitor()
        self.assertIn('House monitor: state_unreadable.', self.lines)
        self.assertEqual(healthy_step(monitor.engine), [])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, 'house_state.json')
        self.options = r.parse_house_options(TWO_CIRCUITS)
        self.mono, self.wall = 50_000.0, 2_000_100_000.0
        self.lines = []

    def tearDown(self):
        self.directory.cleanup()

    def monitor(self, path=None):
        return hm.HouseMonitor(self.options, token_source=lambda: None, clock=lambda: self.wall,
                               monotonic=lambda: self.mono, log=self.lines.append, state_path=path or self.path,
                               boot_id=lambda: BOOT)

    def test_at_most_every_five_minutes_atomic_and_private(self):
        monitor = self.monitor()
        healthy_step(monitor.engine, now=self.mono)
        written = []
        real = hs.StateFile.save

        def counting(state_file, document):
            written.append(document['saved_mono'])
            return real(state_file, document)
        with patch.object(hs.StateFile, 'save', counting):
            for second in range(0, 901):
                self.mono = 50_000.0 + second
                monitor.save_state()
        self.assertEqual(hs.SAVE_PERIOD_S, 300)
        self.assertEqual(written, [50_300.0, 50_600.0, 50_900.0])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(self.directory.name), ['house_state.json'])
        document, problem = hs.StateFile(self.path).load()
        self.assertIsNone(problem)
        self.assertEqual(document['saved_mono'], 50_900.0)

    def test_the_worker_saves_when_it_stops_and_the_next_start_continues(self):
        monitor = self.monitor()
        monitor.engine.no_response[BEDROOM] = r.NoResponse(('hc1', 1), self.mono - 3600, self.mono - 600)
        stop = threading.Event()
        stop.set()
        monitor.run(stop)
        self.assertTrue(os.path.exists(self.path))
        self.mono += 30
        again = self.monitor()
        self.assertEqual(self.lines[-1], 'House monitor: state_restored.')
        self.assertEqual(again.engine.no_response[BEDROOM].onset, self.mono - 3630)

    def test_a_failed_save_is_logged_and_nothing_else(self):
        monitor = self.monitor(path=os.path.join(self.directory.name, 'missing', 'house_state.json'))
        self.assertFalse(monitor.save_state(force=True))
        self.assertIn('House monitor: state_save_failed.', self.lines)

    def test_a_temporary_file_left_by_a_stop_during_a_write_is_removed(self):
        leftover = os.path.join(self.directory.name, hs.TEMP_PREFIX + 'abc')
        with open(leftover, 'wb') as stream:
            stream.write(b'{')
        self.monitor()
        self.assertFalse(os.path.exists(leftover))

    def test_an_oversized_document_is_not_written(self):
        state_file = hs.StateFile(self.path, max_bytes=100)
        engine = r.HouseEngine(self.options)
        self.assertFalse(state_file.save(hs.export(engine, mono=1.0, wall=2.0, boot=BOOT)))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(os.listdir(self.directory.name), [])

    def test_the_app_uses_the_data_directory(self):
        self.assertEqual(hs.STATE_PATH, '/data/house_state.json')
        import run_app
        with patch.object(run_app, 'HouseMonitor') as house, patch.object(run_app, 'load_house_options',
                                                                          return_value=self.options):
            run_app.start_house_monitor()
        house.assert_called_once_with(self.options, state_path='/data/house_state.json')

    def test_without_a_path_nothing_is_read_or_written(self):
        monitor = hm.HouseMonitor(self.options, token_source=lambda: None, log=self.lines.append)
        self.assertIsNone(monitor.state_file)
        self.assertFalse(monitor.save_state(force=True))

    def test_a_new_selection_restarts_only_what_it_changes(self):
        monitor = self.monitor()
        healthy_step(monitor.engine, now=self.mono)
        engine = monitor.engine
        engine.no_response[BEDROOM] = r.NoResponse(('hc1', 1), self.mono - 3600, self.mono - 600)
        engine.no_response['sensor.flur'] = r.NoResponse(('hc2', 1), self.mono - 3600, self.mono - 600)
        options = r.parse_house_options(dict(TWO_CIRCUITS, kuehlere_raeume=['sensor.flur']))
        monitor.request_reconfigure(options)
        self.assertTrue(monitor._apply_pending())
        self.assertIsNot(monitor.engine, engine)
        self.assertEqual(set(monitor.engine.no_response), {BEDROOM})
        self.assertEqual(monitor.engine.no_response[BEDROOM].onset, self.mono - 3600)
        self.assertTrue(monitor.engine.sensors['sensor.wohnzimmer'].history)
        self.assertFalse(monitor.engine.sensors['sensor.flur'].history)


class SizeTests(unittest.TestCase):
    def test_the_largest_selection_stays_bounded(self):
        import random
        noise = random.Random(3)
        entities = [f'sensor.raum_{index:02d}' for index in range(r.MAX_SENSORS)]
        options = r.parse_house_options({'heizkreis_1_sensoren': entities[:12], 'heizkreis_2_sensoren': entities[12:20],
                                         'haus_sensoren': entities[20:]})
        engine = r.HouseEngine(options)
        now = 1_000_000.0
        for sensor in engine.sensors.values():
            value = 20.0
            for minute in range(7 * 1440):
                value = min(25.0, max(15.0, value + noise.uniform(-0.9, 0.9)))   # many different steps
                sensor.ingest(now - 7 * 86400 + minute * 60, round(value, 3))
            sensor.expire(now)
        document = hs.export(engine, mono=now, wall=2e9, boot=BOOT)
        size = len(hs.rules.canonical(document).encode())
        self.assertLess(size, hs.STATE_MAX_BYTES // 2)
        for entry in document['engine']['rooms'].values():
            self.assertLessEqual(len(entry['sensor']['history']), hs.HISTORY_KEEP_S // 60 + 2)
            self.assertLessEqual(len(entry['sensor']['delta_keys']), hs.DELTA_KEYS_MAX)
        restored, outcome = hs.restore(options, json.loads(json.dumps(document)), mono=now, wall=2e9, boot=BOOT)
        self.assertEqual(outcome, 'state_restored')
        for entity, sensor in engine.sensors.items():
            # The step size and the update count survive the compact form.
            self.assertEqual(restored.sensors[entity].step_size(), sensor.step_size())
            self.assertEqual(restored.sensors[entity].updates, sensor.updates)


class HarnessTests(unittest.TestCase):
    def test_a_stopped_harness_evaluates_nothing(self):
        h = Harness({'heizkreis_1_sensoren': ['sensor.a']}, values={'sensor.a': 21.0})
        h.stopped = True
        h.step()
        self.assertIsNone(h.engine.last_now)


if __name__ == '__main__':
    unittest.main()
