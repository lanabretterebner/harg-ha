"""Room learning (app 1.6.9): owner-started, from Home Assistant's history,
recording only as a fallback, nothing applied without acceptance.

Synthetic history fixtures: a normal room, a cold room heated by sun and a
neighbour, a room on both circuits and a failing radiator, plus short and
missing histories. The history API is a fake on loopback or in memory; no
real HA, Supervisor or controller is contacted.
"""
import http.client
import json
import math
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

import house_monitor as hm
import house_rules as r
import ingress_proxy
import room_learning as rl
import room_picker as rp
import run_app
from house_test_support import Harness, circuit
from test_house_rules import PERSIST
from test_room_picker import ADMIN, PAGE, PREFIX, USER, PickerWorld

ROOM = 'sensor.wohnzimmer'
CLIMATE = {'hc1': 'climate.heizkreis_1', 'hc2': 'climate.heizkreis_2'}
DAY0 = math.floor(2_000_000_000 / 86400) * 86400      # a UTC midnight
HOUR = 3600
KINDS = {'hc1': 'radiator', 'hc2': 'radiator'}


def climate_state(circuit_name, day_hours=(6, 22), day_target=21.0, setback_target=17.0):
    """Programme: day from day_hours[0] to day_hours[1] o'clock (UTC)."""
    def state(wall):
        hour = (wall - DAY0) % 86400 / HOUR
        day = day_hours[0] <= hour < day_hours[1]
        return ('Tag', day_target, 1) if day else ('Absenken', setback_target, 3)
    return state


def climate_rows(name, state, start, end):
    """Rows at the start of the window and at every programme change."""
    rows, previous, when = [], None, start
    while when < end:
        current = state(when)
        if current != previous:
            phase, target, code = current
            rows.append({'entity_id': CLIMATE[name], 'state': 'auto', 'last_changed': rl.datetime.fromtimestamp(
                when, rl.timezone.utc).isoformat(), 'attributes': {
                'temperature': target, 'Sollwertphase': phase, 'Heizkreisstatus-Code': code,
                'hargassner_control_schema': 'configured_room_setpoint_v1'}})
            previous = current
        when += 600
    return rows


def room_rows(model, start, end, step=600, gaps=()):
    rows, when = [], start
    while when < end:
        if not any(low <= when < high for low, high in gaps):
            value = model(when)
            rows.append({'entity_id': ROOM, 'state': 'unavailable' if value is None else f'{value:.2f}',
                         'last_changed': rl.datetime.fromtimestamp(when, rl.timezone.utc).isoformat()})
        when += step
    return rows


def hours(wall):
    return (wall - DAY0) % 86400 / HOUR


def jitter(wall):
    return 0.1 if int(wall / 600) % 2 else 0.0


def normal_room(wall):
    """Setback 18 °C; from 06:00 1 K/h up to 21 °C; cools from 22:00."""
    hour = hours(wall)
    if hour < 6:
        value = 18.0
    elif hour < 22:
        value = min(21.0, 18.0 + (hour - 6))
    else:
        value = max(18.0, 21.0 - (hour - 22))
    return value + jitter(wall)


def sun_room(wall):
    """A cool room: barely warms on a heat-up (0.1 K/h), sun on every other afternoon."""
    hour = hours(wall)
    day = int((wall - DAY0) // 86400)
    value = 17.0 + (0.1 * min(8.0, hour - 6) if hour >= 6 else 0.0)
    if day % 2 == 0 and 13 <= hour < 16:
        value = 21.5
    return value + jitter(wall)


def both_room(wall):
    """Heizkreis 2 starts 03:00 (0.4 K/h), Heizkreis 1 06:00 (0.6 K/h)."""
    hour = hours(wall)
    if hour < 3:
        value = 17.0
    elif hour < 5:
        value = 17.0 + 0.4 * (hour - 3)
    elif hour < 6:
        value = 17.8
    elif hour < 9:
        value = min(19.0, 17.8 + 0.6 * (hour - 6))
    elif hour < 22:
        value = 20.5
    else:
        value = 17.0
    return value + jitter(wall)


def failing_room(wall):
    """A dead radiator: the room sits at 16 °C whatever the circuit does."""
    return 16.0 + jitter(wall)


class History:
    """In-memory recorder for one room and the two climate entities."""

    def __init__(self, model, days, *, hc1=None, hc2=None, gaps=(), end=None):
        self.end = end or DAY0 + days * 86400
        self.start = self.end - days * 86400
        self.model, self.gaps = model, gaps
        self.states = {'hc1': hc1 or climate_state('hc1'), 'hc2': hc2 or climate_state('hc2')}
        self.calls = []
        self.fail = set()       # chunk numbers that fail
        self.block = None       # threading.Event: history waits on it

    def climates(self):
        return [{'entity_id': CLIMATE[name], 'state': 'auto', 'attributes': {
            'hargassner_control_schema': 'configured_room_setpoint_v1',
            'hargassner_correction_id': f'hargassner_gw_sys_number_heizkreis_{name[-1]}_raumkorrektur'}}
            for name in ('hc1', 'hc2')] + [{'entity_id': 'climate.fremd', 'state': 'heat', 'attributes': {}}]

    def answer(self, entities, start, end):
        self.calls.append((tuple(entities), start, end))
        if self.block is not None:
            self.block.wait(5)
        if len(self.calls) in self.fail:
            raise hm.ApiError('deadline')
        low, high = max(start, self.start), min(end, self.end)
        series = []
        if ROOM in entities and low < high:
            series.append(room_rows(self.model, low, high, gaps=self.gaps))
        for name, entity in CLIMATE.items():
            if entity in entities:
                series.append(climate_rows(name, self.states[name], max(start, self.start - 86400), max(end, start + 1)))
        return [rows for rows in series if rows]


class FakeLearningApi:
    def __init__(self, history, token, entities):
        self.history_source, self.entities = history, frozenset(entities)

    def states(self):
        return self.history_source.climates()

    def history(self, entities, start, end):
        assert set(entities) <= self.entities, entities
        return json.loads(json.dumps(self.history_source.answer(entities, start, end)))


def analyse(history, home='hc1', current=(0.0, 100, 0)):
    api = FakeLearningApi(history, 't', [ROOM] + list(CLIMATE.values()))
    rooms, circuits, failed = rl.collect(api, ROOM, CLIMATE, history.start, history.end)
    return rl.analyse(rooms, circuits, start=history.start, end=history.end, home=home, kinds=KINDS,
                      current=current), failed


class AnalysisTests(unittest.TestCase):
    def test_normal_room(self):
        result, failed = analyse(History(normal_room, 14))
        self.assertEqual(failed, 0)
        self.assertTrue(result['enough'])
        self.assertEqual(result['proposal'], (0.0, 100, 0))
        self.assertEqual(result['warnings'], [])
        self.assertGreaterEqual(result['facts']['events']['hc1'], 12)
        self.assertRegex(result['evidence'][0], r'^Tagesmittel 2[01],[0-9] °C bei Ziel 21,0 °C \(14 Tage')
        # The rise between the 15-min medians at the start and the end of the 2 h window.
        self.assertTrue(any(re.match(r'Anstieg beim Aufheizen 0,[89] K/h, HK1 \(14 Aufheizvorgänge\)', line)
                            for line in result['evidence']), result['evidence'])

    def test_cold_room_heated_by_sun_and_neighbour(self):
        result, _failed = analyse(History(sun_room, 14))
        allowance, first, second = result['proposal']
        self.assertGreaterEqual(allowance, 2.0)
        self.assertLessEqual(first, 20)
        self.assertEqual(second, 0)
        self.assertGreater(result['facts']['reached_days'], 0)          # sunny afternoons
        # Two warnings: much more allowance than before, and hardly warmer on its circuit.
        self.assertEqual(len(result['warnings']), 2, result['warnings'])
        self.assertIn('deutlich mehr Abweichung', result['warnings'][0])
        self.assertIn('kaum wärmer', result['warnings'][1])

    def test_room_on_both_circuits(self):
        history = History(both_room, 14, hc2=climate_state('hc2', day_hours=(3, 22)))
        result, _failed = analyse(history, current=(0.0, 100, 50))
        allowance, first, second = result['proposal']
        self.assertEqual(allowance, 0.0)
        # 0.6 and 0.4 K/h ramps, measured between 15-min medians: about 60 % and 45 %.
        self.assertTrue(55 <= first <= 75, first)
        self.assertTrue(40 <= second <= 55, second)
        self.assertGreater(first, second)
        self.assertTrue(any(re.match(r'Anstieg beim Aufheizen 0,[34] K/h, HK2', line) for line in result['evidence']))
        # Heizkreis 1 drops from 100 % below the strong 80 %: the owner is warned.
        self.assertEqual(len(result['warnings']), 1, result['warnings'])
        self.assertIn('sinkt unter 80 %', result['warnings'][0])

    def test_a_second_circuit_found_in_the_history_is_a_warning(self):
        history = History(both_room, 14, hc2=climate_state('hc2', day_hours=(3, 22)))
        result, _failed = analyse(history, current=(0.0, 100, 0))
        self.assertGreaterEqual(result['proposal'][2], 30)
        self.assertTrue(any('auch Heizkreis 2' in line for line in result['warnings']), result['warnings'])

    def test_a_room_reaching_its_target_rarely_is_a_warning(self):
        # One sunny day in seven: the room reaches its target once; still a warning (review M1).
        def rarely(wall):
            day = int((wall - DAY0) // 86400)
            return 21.5 if day == 0 and 13 <= hours(wall) < 16 else failing_room(wall)
        result, _failed = analyse(History(rarely, 7, hc1=climate_state('hc1', day_target=22.0)))
        self.assertEqual(result['facts']['reached_days'], 1)
        self.assertTrue(any('erreicht das Ziel selten' in line for line in result['warnings']), result['warnings'])

    def test_shared_starts_are_not_attributed_to_a_circuit_the_room_may_be_on(self):
        # Both circuits switch together; the room is configured on both: no
        # start can be split, the influences stay as they are.
        result, _failed = analyse(History(normal_room, 14), current=(0.0, 100, 50))
        self.assertEqual(result['proposal'][1:], (100, 50))
        self.assertTrue(any('nur gemeinsam' in line for line in result['evidence']))

    def test_failing_radiator_is_made_visible(self):
        history = History(failing_room, 14, hc1=climate_state('hc1', day_target=22.0))
        result, _failed = analyse(history)
        self.assertTrue(result['enough'])
        allowance, first, _second = result['proposal']
        self.assertEqual((allowance, first), (5.0, 0))
        text = ' '.join(result['warnings'])
        self.assertIn('Der Raum erreicht das Ziel nie', text)
        self.assertIn('Heizkörper', text)
        self.assertIn('kaum wärmer', text)
        self.assertRegex(result['evidence'][0], r'^Tagesmittel 16,[01] °C bei Ziel 22,0 °C')

    def test_short_history_is_too_little(self):
        result, _failed = analyse(History(normal_room, 2))
        self.assertFalse(result['enough'])
        self.assertIsNone(result['proposal'])
        self.assertTrue(any('zu wenig' in line.lower() for line in result['evidence']))

    def test_missing_history(self):
        history = History(normal_room, 14, gaps=((DAY0 - 99 * 86400, DAY0 + 99 * 86400),))
        result, _failed = analyse(history)
        self.assertFalse(result['enough'])
        self.assertIn('keinen Verlauf', result['evidence'][0])

    def test_gaps_reduce_the_days_used(self):
        history = History(normal_room, 7)
        full, _failed = analyse(history)
        gapped, _failed = analyse(History(normal_room, 7, gaps=((history.start + 86400, history.start + 3 * 86400),)))
        self.assertEqual(full['facts']['days'] - gapped['facts']['days'], 2)
        self.assertTrue(gapped['enough'])

    def test_unavailable_rows_are_gaps(self):
        history = History(lambda wall: None if 8 <= hours(wall) < 22 else normal_room(wall), 7)
        result, _failed = analyse(history)
        self.assertEqual(result['facts']['days'], 0)


class BoundsTests(unittest.TestCase):
    def test_thirty_days_in_sixty_bounded_requests(self):
        history = History(normal_room, 30)
        analyse(history)
        self.assertEqual(len(history.calls), 60)
        self.assertTrue(all(end - start <= rl.CHUNK_S for _e, start, end in history.calls))
        self.assertEqual({entities for entities, _s, _e in history.calls},
                         {(ROOM, CLIMATE['hc1'], CLIMATE['hc2'])})

    def test_too_many_failed_pieces_stop_instead_of_guessing(self):
        history = History(normal_room, 4)
        history.fail = {1, 2, 3}            # 3 of 8 pieces
        api = FakeLearningApi(history, 't', [ROOM] + list(CLIMATE.values()))
        with self.assertRaises(hm.ApiError):
            rl.collect(api, ROOM, CLIMATE, history.start, history.end)
        history = History(normal_room, 4)
        history.fail = {2}
        _rooms, _circuits, failed = rl.collect(api.__class__(history, 't', api.entities), ROOM, CLIMATE,
                                               history.start, history.end)
        self.assertEqual(failed, 1)

    def test_allowlist_is_exact(self):
        api = rl.LearningApi('t', [ROOM, CLIMATE['hc1']])
        path = (rl.HISTORY_PATH + rl.stamp(DAY0) + '?filter_entity_id=' + ROOM + ',' + CLIMATE['hc1']
                + '&end_time=' + rl.stamp(DAY0 + 3600))
        self.assertTrue(api.allowed('GET', path, None))
        self.assertTrue(api.allowed('GET', '/core/api/states', None))
        for method, bad, body in (('POST', path, {}), ('GET', path.replace(ROOM, 'sensor.fremd'), None),
                                  ('GET', path + '&minimal_response', None),
                                  ('GET', path.replace(ROOM + ',', ROOM + ',' + ROOM + ','), None),
                                  ('GET', '/core/api/states/sensor.wohnzimmer', None),
                                  ('GET', '/core/api/history/period/' + rl.stamp(DAY0), None),
                                  ('GET', '/core/api/services', None)):
            with self.subTest(bad=bad):
                self.assertFalse(api.allowed(method, bad, body))

    def test_climates_are_found_by_their_vm_attributes(self):
        found = rl.find_climates(History(normal_room, 1).climates())
        self.assertEqual(found, CLIMATE)
        twice = History(normal_room, 1).climates() * 2
        self.assertEqual(rl.find_climates(twice), {})          # ambiguous: none


class Clock:
    def __init__(self):
        self.wall = DAY0 + 30 * 86400

    def __call__(self):
        return self.wall


class LearningWorld(PickerWorld):
    """The room page with learning over an in-memory history and a temp file."""

    def __init__(self, model=normal_room, days=30, house=None, folder=None, **history):
        super().__init__(house or {'heizkreis_1_sensoren': [ROOM, 'sensor.kueche'],
                                   'heizkreis_2_sensoren': ['sensor.bad']})
        self.wallclock = Clock()
        self.history = History(model, days, end=self.wallclock.wall, **history)
        self.folder = folder or tempfile.mkdtemp()
        self.learning = self.make_learning()
        self.picker.learning = self.learning
        self.picker.learn_wait = 10

    def make_learning(self):
        return rl.RoomLearning(file=rl.LearningFile(str(Path(self.folder) / 'room_learning.json')),
                               token_source=lambda: 'synthetic-supervisor-token',
                               api_factory=lambda token, entities: FakeLearningApi(self.history, token, entities),
                               clock=self.wallclock, kinds=lambda: KINDS)

    def act(self, action, entity=ROOM, days=None, checked=False, user=ADMIN, revision=None):
        body = {'aktion': action, 'entity_id': entity, 'tage': days, 'geprueft': checked,
                'revision': revision or rp.revision(self.stored)}
        return self.picker.learn(user, self.token(), json.dumps(body).encode())


class LearningFlowTests(unittest.TestCase):
    def test_start_shows_a_proposal_and_nothing_is_applied(self):
        world = LearningWorld(sun_room)
        status, document = world.act('anlernen', days=14)
        self.assertEqual((status, document['ok']), (200, True), document)
        self.assertEqual(world.posted, [])                   # nothing written
        self.assertEqual(len(world.history.calls), 28)       # 14 days in 12 h pieces
        page = world.page()
        self.assertIn('<strong>Vorschlag</strong>', page)
        self.assertIn('Tagesmittel', page)
        self.assertIn('Ich habe den Raum geprüft', page)    # a warning needs the check
        self.assertIn('data-aktion="uebernehmen"', page)
        self.assertNotRegex(page, '[‐-―]')

    def test_default_period_is_the_longest_up_to_30_days(self):
        world = LearningWorld(normal_room, days=9)           # the recorder holds 9 days
        status, _document = world.act('anlernen', days=0)
        self.assertEqual(status, 200)
        self.assertEqual(len(world.history.calls), 60)
        entry, _learned, _running = world.learning.view(ROOM)
        self.assertEqual(entry['state'], 'proposal')
        self.assertIn('(9 Tage', entry['evidence'][0])

    def test_accept_writes_the_values_and_marks_the_room(self):
        world = LearningWorld(sun_room)
        world.act('anlernen', days=14)
        proposal = world.learning.proposal(ROOM)['proposal']
        status, document = world.act('uebernehmen')
        self.assertEqual(status, 400)                        # warning not confirmed
        self.assertIn('geprüft', document['message'])
        self.assertEqual(world.posted, [])
        status, document = world.act('uebernehmen', checked=True)
        self.assertEqual((status, document['ok']), (200, True), document)
        self.assertTrue(document['message'].startswith('Vorschlag übernommen.'))
        options = r.parse_house_options(world.posted[-1])
        self.assertEqual((options.allowance(ROOM), options.influence(ROOM, 'hc1'), options.influence(ROOM, 'hc2')),
                         tuple(proposal))
        self.assertEqual(world.monitor.pending[0], options)   # handed to the running monitor
        self.assertIn('Angelernt am', world.page())
        # Reset: back to the defaults, the mark is gone.
        status, document = world.act('zuruecksetzen')
        self.assertEqual(status, 200, document)
        options = r.parse_house_options(world.posted[-1])
        self.assertEqual((options.allowance(ROOM), options.influence(ROOM, 'hc1')), (0.0, 100))
        self.assertNotIn('Angelernt am', world.page())

    def test_discard_changes_nothing(self):
        world = LearningWorld(normal_room)
        world.act('anlernen', days=7)
        status, document = world.act('verwerfen')
        self.assertEqual((status, document['ok']), (200, True))
        self.assertEqual(world.posted, [])
        self.assertIsNone(world.learning.view(ROOM)[0])
        self.assertEqual(world.act('uebernehmen')[0], 409)

    def test_a_proposal_for_a_changed_room_is_refused(self):
        world = LearningWorld(normal_room)
        world.act('anlernen', days=7)
        from test_room_picker import row
        world.save([row(ROOM, 'heizkreis_1', kuehler=3), row('sensor.kueche', 'heizkreis_1'),
                    row('sensor.bad', 'heizkreis_2')])
        status, document = world.act('uebernehmen')
        self.assertEqual(status, 409)
        self.assertIn('seit dem Vorschlag geändert', document['message'])

    def test_learning_moves_a_room_to_its_stronger_circuit(self):
        history = {'hc2': climate_state('hc2', day_hours=(3, 22))}
        world = LearningWorld(lambda wall: 17.0 + (0.8 * min(2, hours(wall) - 3) if hours(wall) >= 3 else 0)
                              + jitter(wall), **history,
                              house={'heizkreis_1_sensoren': [ROOM, 'sensor.kueche'],
                                     'heizkreis_2_sensoren': ['sensor.bad'],
                                     'raum_einstellungen': [{'sensor': ROOM, 'kuehler': 0, 'einfluss_hk1': 50,
                                                             'einfluss_hk2': 50}]})
        world.act('anlernen', days=14)
        entry = world.learning.proposal(ROOM)
        self.assertGreater(entry['proposal'][2], entry['proposal'][1])
        self.assertTrue(any('zugeordnet' in line for line in entry['evidence']))
        status, document = world.act('uebernehmen', checked=True)
        self.assertEqual(status, 200, document)
        self.assertIn(ROOM, world.posted[-1]['heizkreis_2_sensoren'])
        self.assertNotIn(ROOM, world.posted[-1]['heizkreis_1_sensoren'])

    def test_an_old_proposal_is_refused(self):
        world = LearningWorld(normal_room)
        world.act('anlernen', days=7)
        world.wallclock.wall += 7 * 86400 + 1
        status, document = world.act('uebernehmen')
        self.assertEqual(status, 409)
        self.assertIn('älter als 7 Tage', document['message'])
        self.assertEqual(world.posted, [])

    def test_accepting_keeps_a_newer_proposal(self):
        world = LearningWorld(normal_room)
        world.act('anlernen', days=7)
        old = world.learning.proposal(ROOM)
        world.wallclock.wall += 60
        world.act('anlernen', days=14)                       # a new analysis finished meanwhile
        world.learning.accepted(ROOM, old['proposal'], old['made'])
        self.assertEqual(world.learning.proposal(ROOM)['end'] - world.learning.proposal(ROOM)['start'], 14 * 86400)

    def test_ordinary_users_start_no_work(self):
        world = LearningWorld(normal_room, days=60)
        world.history = History(normal_room, 60, end=world.wallclock.wall + 8 * 86400)
        world.act('aufzeichnen', days=3)
        world.wallclock.wall += 3 * 86400 + 60
        world.page(USER)
        self.assertEqual(world.history.calls, [])
        world.page()
        world.learning.running[2].wait(10)
        self.assertEqual(len(world.history.calls), 6)

    def test_too_little_data_offers_recording(self):
        world = LearningWorld(normal_room, days=2)
        status, document = world.act('anlernen', days=0)
        self.assertEqual(status, 200)
        self.assertIn('Zu wenig Daten', document['message'])
        page = world.page()
        self.assertIn('<strong>Zu wenig Daten</strong>', page)
        self.assertIn('Ab jetzt aufzeichnen', page)
        self.assertNotIn('data-aktion="uebernehmen"', page)

    def test_cancel_a_running_analysis(self):
        world = LearningWorld(normal_room)
        world.history.block = threading.Event()
        world.picker.learn_wait = 0.2
        status, document = world.act('anlernen', days=30)
        self.assertEqual(status, 202, document)
        self.assertIn('Wird ausgewertet', world.page())
        world.act('abbrechen')
        world.history.block.set()
        world.learning.running[2].wait(5)
        self.assertIsNone(world.learning.view(ROOM)[0])
        self.assertLess(len(world.history.calls), 60)

    def test_recording_quiets_the_room_then_analyses_its_period(self):
        world = LearningWorld(normal_room, days=60)
        world.history = History(normal_room, 60, end=world.wallclock.wall + 8 * 86400)
        status, document = world.act('aufzeichnen', days=7)
        self.assertEqual(status, 200, document)
        self.assertEqual(world.learning.quiet(), frozenset({ROOM}))
        self.assertIn('Aufzeichnung läuft bis', world.page())
        # Restart: the recording is still there and still quiets the room.
        world.learning = world.make_learning()
        world.picker.learning = world.learning
        self.assertEqual(world.learning.quiet(), frozenset({ROOM}))
        world.wallclock.wall += 7 * 86400 + 60
        self.assertEqual(world.learning.quiet(), frozenset())
        world.page()                                        # the page starts the analysis of the recording
        world.learning.running[2].wait(10)
        entry = world.learning.view(ROOM)[0]
        self.assertEqual(entry['state'], 'proposal')
        self.assertEqual(entry['end'] - entry['start'], 7 * 86400)
        self.assertEqual(len(world.history.calls), 14)

    def test_cancel_a_recording(self):
        world = LearningWorld(normal_room)
        world.act('aufzeichnen', days=3)
        self.assertEqual(world.act('abbrechen')[0], 200)
        self.assertEqual(world.learning.quiet(), frozenset())

    def test_proposal_and_mark_survive_a_restart(self):
        world = LearningWorld(normal_room)
        world.act('anlernen', days=7)
        before = world.learning.view(ROOM)[0]
        world.learning = world.make_learning()
        self.assertEqual(world.learning.view(ROOM)[0], before)
        world.picker.learning = world.learning
        world.act('uebernehmen')
        again = world.make_learning()
        self.assertIsNotNone(again.view(ROOM)[1])
        mode = Path(world.folder, 'room_learning.json').stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_a_damaged_file_starts_empty(self):
        folder = tempfile.mkdtemp()
        Path(folder, 'room_learning.json').write_text('{"schema": "harg-room-learning/1", "rooms": 1}')
        self.assertEqual(rl.LearningFile(str(Path(folder, 'room_learning.json'))).load(), {'rooms': {}, 'learned': {}})

    def test_only_admins_and_only_rooms_on_a_circuit(self):
        world = LearningWorld(normal_room)
        body = json.dumps({'aktion': 'anlernen', 'entity_id': ROOM, 'tage': 7, 'geprueft': False,
                           'revision': rp.revision(world.stored)}).encode()
        status, _document = world.picker.learn(USER, world.token(), body)
        self.assertEqual(status, 403)
        status, document = world.act('anlernen', entity='sensor.keller', days=7)
        self.assertEqual(status, 400)
        self.assertIn('Heizkreis', document['message'])
        for bad in ({'aktion': 'loeschen'}, {'tage': 5}, {'tage': '7'}, {'geprueft': 1}, {'extra': 1}):
            body = {'aktion': 'anlernen', 'entity_id': ROOM, 'tage': 7, 'geprueft': False,
                    'revision': rp.revision(world.stored), **bad}
            status, _document = world.picker.learn(ADMIN, world.token(), json.dumps(body).encode())
            self.assertEqual(status, 400, bad)
        self.assertEqual(world.history.calls, [])
        page = world.page(USER)
        self.assertNotIn('data-aktion=', page)


class QuietTests(unittest.TestCase):
    def test_a_recorded_room_reports_no_heating_finding_but_frost(self):
        options = {'heizkreis_1_sensoren': [ROOM, 'sensor.kueche']}
        h = Harness(options, hc1=circuit(target=23.0, day_target=23.0), values={ROOM: 12.0, 'sensor.kueche': 23.0})
        h.engine.quiet = frozenset({ROOM})
        h.run(2 * (400 + PERSIST))
        self.assertNotIn('room_cold', h.codes())
        h.engine.quiet = frozenset()
        h.step()
        self.assertIn('room_cold', h.codes())               # the rule kept running
        h.engine.quiet = frozenset({ROOM})
        h.values[ROOM] = 4.0
        self.assertIsNotNone(h.first('room_frost', 60))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.world = LearningWorld(normal_room)
        settings = ingress_proxy.Settings('unused.example', 443, 'unused.example', 'i' * 43)
        self.server = run_app.MonitorProxyServer(('127.0.0.1', 0), settings, run_app.disabled_status,
                                                 self.world.picker.page, self.world.picker.save,
                                                 self.world.picker.learn)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        peer = patch.object(ingress_proxy, 'SUPERVISOR_IP', '127.0.0.1')
        peer.start()

        def cleanup():
            self.server.shutdown(); self.server.server_close(); thread.join(); peer.stop()
        self.addCleanup(cleanup)

    def send(self, method, body=b'', headers=None):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=15)
        client.putrequest(method, '/api/local-monitor/raumsensoren/anlernen', skip_accept_encoding=True)
        sent = {'X-Remote-User-Id': ADMIN, 'X-Ingress-Path': PREFIX, **(headers or {})}
        for name, value in sent.items():
            client.putheader(name, value)
        if method == 'POST':
            client.putheader('Content-Length', str(len(body)))
        client.endheaders(body if method == 'POST' else None)
        response = client.getresponse()
        result = response.status, response.read()
        client.close()
        return result

    def test_learning_through_ingress(self):
        body = json.dumps({'aktion': 'anlernen', 'entity_id': ROOM, 'tage': 7, 'geprueft': False,
                           'revision': rp.revision(self.world.stored)}).encode()
        headers = {'Content-Type': 'application/json', 'Origin': 'http://ha.example:8123', 'Referer': PAGE,
                   'Sec-Fetch-Site': 'same-origin', rp.TOKEN_HEADER: self.world.token()}
        status, raw = self.send('POST', body, {**headers, 'Origin': 'http://evil.example'})
        self.assertEqual(status, 403, raw)
        self.assertEqual(self.world.history.calls, [])
        self.assertEqual(self.send('GET')[0], 405)
        status, raw = self.send('POST', body, headers)
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)['ok'])
        self.assertEqual(self.world.learning.view(ROOM)[0]['state'], 'proposal')


if __name__ == '__main__':
    unittest.main()
