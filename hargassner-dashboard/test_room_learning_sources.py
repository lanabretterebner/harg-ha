"""Room learning sources (app 1.6.10): VM circuit history, HA long-term
statistics, recorder history only for the recent part or as a fallback.

Synthetic fixtures only: the VM answer, the statistics and the recorder are
fakes in memory (the VM reader also against a fake HTTP connection); no real
HA, Supervisor, VM or controller is contacted.
"""
import json
import math
import threading
import unittest

import ha_websocket as ws
import house_monitor as hm
import room_learning as rl
from test_room_learning import (ADMIN, CLIMATE, DAY0, KINDS, ROOM, FakeLearningApi, History, LearningWorld,
                                climate_state, failing_room, normal_room, sun_room)
from test_room_picker import PREFIX

DAY = 86400


def vm_document(start, end, *, hc1=None, hc2=None, targets=None, gaps=(), present=('hc1', 'hc2')):
    """The VM's circuit-history answer: a row every 5 minutes, codes from the programme."""
    states = {'hc1': hc1 or climate_state('hc1'), 'hc2': hc2 or climate_state('hc2')}
    rows, when = [], int(start)
    while when < end:
        if not any(low <= when < high for low, high in gaps):
            row = [when]
            for name in ('hc1', 'hc2'):
                _phase, _target, code = states[name](when)
                row += [code if name in present else None, 40.0 if code == 1 else 25.0]
            rows.append(row)
        when += 300
    if targets is None:
        targets = {name: [[int(start), states[name](start + 6 * 3600 - start % DAY)[1],
                           states[name](start + 3 * 3600 - start % DAY)[1]]] for name in ('hc1', 'hc2')}
    return {'schemaVersion': 1, 'from': '', 'to': '', 'cadenceSeconds': 300, 'gapSeconds': 900,
            'stateKeys': ['HKZustand_1', 'TVL_1', 'HKZustand_2', 'TVL_2'], 'states': rows,
            'targetsKnown': True, 'targets': targets}


class FakeVm:
    def __init__(self, *, days=30, error=None, **options):
        self.days, self.error, self.options = days, error, options
        self.calls = []

    def read(self, identity, start, end):
        self.calls.append((identity, start, end))
        if self.error:
            raise rl.VmError(self.error)
        return json.loads(json.dumps(vm_document(max(start, end - self.days * DAY), end, **self.options)))


def statistics_for(model, first):
    """Hourly mean/min/max from the model, kept from `first` on."""
    calls = []

    def read(token, entity, start, end):
        calls.append((token, entity, start, end))
        rows, hour = [], math.floor(max(start, first) / 3600) * 3600
        while hour < end:
            values = [model(hour + minute * 60) for minute in range(0, 60, 5)]
            values = [value for value in values if value is not None]
            if values:
                rows.append((hour, sum(values) / len(values), min(values), max(values)))
            hour += 3600
        return rows
    read.calls = calls
    return read


class Recorder(History):
    """The recorder keeps room changes `room_days` and climates `climate_days`."""

    def __init__(self, model, days, *, room_days, climate_days, **options):
        super().__init__(model, days, **options)
        self.room_from = self.end - room_days * DAY
        self.climate_from = self.end - climate_days * DAY

    def answer(self, entities, start, end):
        self.calls.append((tuple(entities), start, end))
        series = []
        low = max(start, self.room_from)
        if ROOM in entities and low < end:
            from test_room_learning import room_rows
            series.append(room_rows(self.model, low, end))
        for name, entity in CLIMATE.items():
            if entity in entities and max(start, self.climate_from) < end:
                from test_room_learning import climate_rows
                series.append(climate_rows(name, self.states[name], max(start, self.climate_from), end))
        return [rows for rows in series if rows]


def run(recorder, *, statistics=None, vm=None, home='hc1', current=(0.0, 100, 0), start=None):
    api = FakeLearningApi(recorder, 't', [ROOM] + list(CLIMATE.values()))
    start = recorder.start if start is None else start
    data = rl.gather(api, ROOM, CLIMATE, start, recorder.end, token='t', statistics=statistics,
                     vm=None if vm is None else (lambda low, high: vm.read((ADMIN, PREFIX), low, high)))
    result = rl.analyse(data['room'], data['circuits'], start=start, end=recorder.end, home=home, kinds=KINDS,
                        current=current, hourly=data['hourly'])
    return data, result


class SourceTests(unittest.TestCase):
    def test_long_term_statistics_and_vm_history(self):
        recorder = Recorder(normal_room, 90, room_days=10, climate_days=10)
        statistics = statistics_for(normal_room, recorder.end - 63 * DAY)
        vm = FakeVm()
        data, result = run(recorder, statistics=statistics, vm=vm)
        self.assertEqual(data['sources'][0], 'Raumtemperatur: Langzeitstatistik von Home Assistant, stündlich, '
                                             '63 Tage vorhanden; feiner Verlauf (jede Änderung) 10 Tage für das Aufheizen.')
        # The recorder keeps the climates only 10 days, all inside the VM's window: nothing older.
        self.assertEqual(data['sources'][1], 'Heizkreise: VM-Verlauf, alle 5 Minuten, 30 Tage.')
        self.assertTrue(result['enough'])
        self.assertEqual(result['proposal'], (0.0, 100, 0))
        self.assertEqual(result['facts']['level'], 'hourly')
        # Day level from the VM's 30 days (the recorder's climates add none older here).
        self.assertEqual(result['facts']['days'], 30)
        self.assertRegex(result['evidence'][0], r'\(30 Tage mit Tagbetrieb ausgewertet, aus Stundenmitteln\)')
        # The heat-up response from the recorder's fine room history (10 days).
        self.assertEqual(result['facts']['events']['hc1'], 10)
        # One VM read over its 30 days, as the administrator who started it.
        self.assertEqual(vm.calls, [((ADMIN, PREFIX), recorder.end - 30 * DAY, recorder.end)])
        self.assertEqual(statistics.calls, [('t', ROOM, recorder.start, recorder.end)])
        # The recorder: the room alone inside the VM's window, the climates only before it.
        inside = [call for call in recorder.calls if call[2] > recorder.end - 30 * DAY]
        self.assertEqual({entities for entities, _s, _e in inside}, {(ROOM,)})
        older = [call for call in recorder.calls if call[2] <= recorder.end - 30 * DAY]
        self.assertEqual({entities for entities, _s, _e in older}, {(CLIMATE['hc1'], CLIMATE['hc2'])})
        # Newest first, stopping two empty pieces after the recorder's 10 days.
        self.assertEqual(len(inside), 22)
        self.assertEqual(len(older), 2)
        self.assertTrue(all(end - start <= rl.CHUNK_S for _e, start, end in recorder.calls))

    def test_older_days_from_the_recorder_climates_add_to_the_vm_days(self):
        recorder = Recorder(normal_room, 90, room_days=10, climate_days=60)
        data, result = run(recorder, statistics=statistics_for(normal_room, recorder.start), vm=FakeVm())
        self.assertIn('Heizkreise: VM-Verlauf, alle 5 Minuten, 30 Tage; davor Verlauf der Heizkreise in Home '
                      'Assistant, 30 Tage.', data['sources'])
        self.assertIn('stündlich, 90 Tage vorhanden', data['sources'][0])
        self.assertEqual(result['facts']['days'], 60)

    def test_statistics_missing_falls_back_to_the_recorder_history(self):
        recorder = Recorder(normal_room, 30, room_days=10, climate_days=10)
        data, result = run(recorder, statistics=lambda *_args: [], vm=FakeVm())
        self.assertEqual(data['sources'][0], 'Raumtemperatur: keine Langzeitstatistik in Home Assistant (dem Sensor '
                                             'fehlt wohl state_class: measurement); ersatzweise Verlauf von Home '
                                             'Assistant (jede Änderung), 10 Tage.')
        self.assertIsNone(data['hourly'])
        self.assertEqual(result['facts']['level'], 'fine')
        self.assertTrue(result['enough'])
        self.assertEqual(result['facts']['days'], 10)
        self.assertNotIn('Stundenmitteln', result['evidence'][0])

    def test_statistics_unreadable_falls_back_and_says_so(self):
        def broken(*_args):
            raise ws.WebSocketError('refused')
        recorder = Recorder(normal_room, 14, room_days=14, climate_days=14)
        data, result = run(recorder, statistics=broken, vm=FakeVm())
        self.assertTrue(data['sources'][0].startswith('Raumtemperatur: Langzeitstatistik von Home Assistant nicht '
                                                      'lesbar; ersatzweise Verlauf'))
        self.assertTrue(result['enough'])

    def test_vm_refused_unreachable_or_old_falls_back_to_the_recorder_climates(self):
        for error, text in (('unsupported', 'ältere VM-Version'), ('refused', 'Zugriff abgelehnt'),
                            ('unreachable', 'nicht erreichbar'), ('response', 'ungültig')):
            with self.subTest(error=error):
                recorder = Recorder(normal_room, 30, room_days=10, climate_days=10)
                vm = FakeVm(error=error)
                data, result = run(recorder, statistics=statistics_for(normal_room, recorder.start), vm=vm)
                self.assertEqual(len(vm.calls), 1)
                self.assertEqual(data['vm'], error)
                self.assertIn(f'Heizkreise: VM-Verlauf nicht verwendet (', data['sources'][1])
                self.assertIn(text, data['sources'][1])
                self.assertIn('ersatzweise Verlauf der Heizkreise in Home Assistant, 10 Tage.', data['sources'][1])
                self.assertTrue(result['enough'])
                self.assertEqual(result['facts']['days'], 10)
                # Without the VM the recorder is asked for room and climates together.
                self.assertIn((ROOM, CLIMATE['hc1'], CLIMATE['hc2']), {call[0] for call in recorder.calls})

    def test_no_vm_and_no_recorder_climates_says_so(self):
        recorder = Recorder(normal_room, 14, room_days=14, climate_days=0)
        data, result = run(recorder, statistics=statistics_for(normal_room, recorder.start), vm=None)
        self.assertEqual(data['sources'][1], 'Heizkreise: VM-Verlauf nicht verwendet (keine Verbindung zur VM '
                                             'eingerichtet); auch Home Assistant hat keinen Verlauf der Heizkreise.')
        self.assertFalse(result['enough'])

    def test_vm_without_known_targets_is_not_used_and_home_assistant_stays_the_source(self):
        # Review H1/M2: a VM answer whose targets are all unknown (journal
        # purged) or that holds no rows at all must not hide the recorder.
        for name, options in (('null targets', {'targets': {'hc1': [[0, None, None]], 'hc2': [[0, None, None]]}}),
                              ('no rows', {'days': 0})):
            with self.subTest(name):
                recorder = Recorder(normal_room, 30, room_days=10, climate_days=10)
                data, result = run(recorder, statistics=statistics_for(normal_room, recorder.start),
                                   vm=FakeVm(**options))
                self.assertEqual(data['vm'], 'empty')
                self.assertIn('keine Heizkreisdaten mit Zieltemperatur', data['sources'][1])
                self.assertIn('ersatzweise Verlauf der Heizkreise in Home Assistant, 10 Tage.', data['sources'][1])
                self.assertTrue(result['enough'])
                self.assertEqual(result['facts']['days'], 10)

    def test_a_morning_start_from_night_shut_off_is_a_heating_start(self):
        # Review M1: code 4 (Tag/Nacht Abschaltung) at night, 1 at 06:00.
        def shut_off(wall):
            hour = (wall - DAY0) % DAY / 3600
            return ('Tag', 21.0, 1) if 6 <= hour < 22 else ('Absenken', 17.0, 4)
        recorder = Recorder(normal_room, 14, room_days=14, climate_days=0)
        _data, result = run(recorder, statistics=statistics_for(normal_room, recorder.start),
                            vm=FakeVm(hc1=shut_off))
        self.assertGreaterEqual(result['facts']['events']['hc1'], 12)
        self.assertEqual(result['proposal'][1], 100)

    def test_recorder_rows_end_where_the_vm_begins(self):
        # Review M3: the VM's rows start 5 days into its window; the last
        # recorder row (day, 06:00-22:00 programme) must not be held over them.
        recorder = Recorder(normal_room, 30, room_days=30, climate_days=30)
        vm_start = recorder.end - 30 * DAY
        vm = FakeVm(gaps=((vm_start, vm_start + 5 * DAY + 12 * 3600),),
                    hc1=climate_state('hc1', day_hours=(0, 0)), hc2=climate_state('hc2', day_hours=(0, 0)))
        data, _result = run(recorder, statistics=statistics_for(normal_room, recorder.start), vm=vm)
        rows = data['circuits']['hc1']
        boundary = vm_start + 5 * DAY + 12 * 3600
        self.assertIn((boundary, None, None, None), rows)
        self.assertTrue(all(row[1] in (None, 'setback') for row in rows if row[0] >= boundary))
        self.assertIn('davor Verlauf der Heizkreise in Home Assistant', data['sources'][1])

    def test_vm_gaps_are_unknown_not_held(self):
        recorder = Recorder(normal_room, 14, room_days=14, climate_days=0)
        start = recorder.start
        # Days 2 to 5 missing at the VM: those days give no day level.
        vm = FakeVm(gaps=((start + 2 * DAY, start + 6 * DAY),))
        _data, result = run(recorder, statistics=statistics_for(normal_room, start), vm=vm)
        self.assertEqual(result['facts']['days'], 10)

    def test_vm_targets_decide_the_level_and_a_failing_radiator_still_warns(self):
        recorder = Recorder(failing_room, 30, room_days=10, climate_days=0)
        vm = FakeVm(targets={'hc1': [[int(recorder.end - 30 * DAY), 22.0, 18.0]], 'hc2': [[int(recorder.start), 21.0, 17.0]]})
        _data, result = run(recorder, statistics=statistics_for(failing_room, recorder.start), vm=vm)
        self.assertTrue(result['enough'])
        self.assertEqual(result['proposal'][:2], (5.0, 0))
        self.assertRegex(result['evidence'][0], r'^Tagesmittel 16,[01] °C bei Ziel 22,0 °C \(30 Tage')
        self.assertTrue(any('erreicht das Ziel nie' in line for line in result['warnings']), result['warnings'])

    def test_unknown_vm_targets_give_no_day_level(self):
        recorder = Recorder(normal_room, 14, room_days=14, climate_days=0)
        vm = FakeVm(targets={'hc1': [[int(recorder.start), None, None]], 'hc2': [[int(recorder.start), None, None]]})
        _data, result = run(recorder, statistics=statistics_for(normal_room, recorder.start), vm=vm)
        self.assertEqual(result['facts']['days'], 0)
        # Heat-up starts need a target too (the day target after the start).
        self.assertEqual(result['facts']['events'].get('hc1', 0), 0)

    def test_cold_room_from_hourly_statistics(self):
        recorder = Recorder(sun_room, 30, room_days=10, climate_days=0)
        _data, result = run(recorder, statistics=statistics_for(sun_room, recorder.start), vm=FakeVm())
        allowance, first, second = result['proposal']
        self.assertGreaterEqual(allowance, 2.0)
        self.assertLessEqual(first, 20)
        self.assertEqual(second, 0)
        self.assertTrue(any('deutlich mehr Abweichung' in line for line in result['warnings']))

    def test_mixed_periods_short_choice_reads_only_what_it_needs(self):
        recorder = Recorder(normal_room, 90, room_days=10, climate_days=60)
        vm = FakeVm()
        statistics = statistics_for(normal_room, recorder.start)
        start = recorder.end - 7 * DAY
        data, result = run(recorder, statistics=statistics, vm=vm, start=start)
        self.assertEqual(vm.calls, [((ADMIN, PREFIX), start, recorder.end)])
        self.assertEqual(statistics.calls[0][2:], (start, recorder.end))
        self.assertTrue(all(call[1] >= start for call in recorder.calls))
        self.assertEqual(len(recorder.calls), 14)                     # 7 days of room changes, nothing older
        self.assertIn('stündlich, 7 Tage vorhanden', data['sources'][0])
        self.assertEqual(data['sources'][1], 'Heizkreise: VM-Verlauf, alle 5 Minuten, 7 Tage.')
        self.assertEqual(result['facts']['days'], 7)


class ParseVmTests(unittest.TestCase):
    def test_phase_from_documented_codes_and_targets_from_steps(self):
        start = DAY0
        document = {'schemaVersion': 1, 'cadenceSeconds': 300, 'gapSeconds': 900, 'states': [
            [start, 3, 25.0, None, None], [start + 300, 1, 40.0, 7, None], [start + 600, 2, 30.0, 1, None],
            [start + 3600, 1, 40.0, 1, None]],
            'targets': {'hc1': [[start, 21.0, 17.0], [start + 500, 22.0, 17.5]], 'hc2': [[start, None, 16.0]]}}
        rows = rl.parse_vm(document, start, start + 7200)
        self.assertEqual(rows['hc1'], [
            (start, 'setback', 17.0, 3), (start + 300, 'day', 21.0, 1), (start + 600, 'setback', 17.5, 2),
            (start + 900, None, None, None),                          # the gap after the row at +600
            (start + 3600, 'day', 22.0, 1), (start + 3900, None, None, None)])
        self.assertEqual(rows['hc2'][1:3], [(start + 300, None, None, 7), (start + 600, 'day', None, 1)])

    def test_a_circuit_without_any_code_is_absent(self):
        document = vm_document(DAY0, DAY0 + DAY, present=('hc1',))
        self.assertEqual(set(rl.parse_vm(document, DAY0, DAY0 + DAY)), {'hc1'})

    def test_malformed_answers_are_refused(self):
        good = vm_document(DAY0, DAY0 + 3600)
        for bad in (None, [], {}, dict(good, schemaVersion=2), dict(good, states={}), dict(good, targets=[]),
                    dict(good, states=[[1, 2, 3]]), dict(good, states=[['x', 1, 2, 3, 4]]),
                    dict(good, states=[[1, 'a', 2, 3, 4]]), dict(good, targets={'hc1': [[1, 'x', 2]]}),
                    dict(good, targets={'hc1': [[1.5, 20, 17]]}), dict(good, states=[[0, 1, 2, 3, 4]] * 20001)):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(rl.VmError) as caught:
                rl.parse_vm(bad, DAY0, DAY0 + 3600)
            self.assertEqual(caught.exception.args, ('response',))

    def test_odd_values_become_unknown(self):
        document = {'schemaVersion': 1, 'states': [[DAY0, 1, None, 70000, None], [DAY0 + 300, 1.5, None, -1, None]],
                    'targets': {'hc1': [[DAY0, 45.0, 17.0]], 'hc2': []}}
        rows = rl.parse_vm(document, DAY0, DAY0 + 600)
        self.assertEqual(rows['hc1'][:2], [(DAY0, 'day', None, 1), (DAY0 + 300, None, None, None)])
        self.assertNotIn('hc2', rows)


class Response:
    def __init__(self, status, body, encoding='identity'):
        self.status, self.body, self.encoding = status, body, encoding

    def getheader(self, name, default=None):
        return self.encoding if name == 'Content-Encoding' else default

    def read(self, limit):
        return self.body[:limit]


class Connection:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.requests, self.closed = [], False

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class VmHistoryTests(unittest.TestCase):
    def setUp(self):
        import ingress_proxy
        self.settings = ingress_proxy.Settings('harg.example', 443, 'harg.example', 'i' * 43)

    def reader(self, connection):
        return rl.VmHistory(self.settings, connection=lambda settings, timeout: connection)

    def test_one_get_as_the_ingress_user(self):
        body = json.dumps(vm_document(DAY0, DAY0 + 600)).encode()
        connection = Connection(Response(200, body))
        document = self.reader(connection).read((ADMIN, PREFIX + '/'), DAY0 + 0.5, DAY0 + 600.2)
        self.assertEqual(document['schemaVersion'], 1)
        self.assertEqual(len(connection.requests), 1)
        method, path, request_body, headers = connection.requests[0]
        self.assertEqual((method, request_body), ('GET', None))
        self.assertEqual(path, f'/api/dashboard/v1/circuit-history?from={DAY0}&to={DAY0 + 601}')
        self.assertEqual(headers, {'Host': 'harg.example', 'Accept': 'application/json',
                                   'Accept-Encoding': 'identity', 'Connection': 'close',
                                   'X-Hargassner-Ingress': 'i' * 43, 'X-Hargassner-User': ADMIN,
                                   'X-Ingress-Path': PREFIX})
        self.assertTrue(connection.closed)

    def test_failures_have_fixed_categories(self):
        cases = ((Connection(Response(404, b'')), 'unsupported'), (Connection(Response(403, b'')), 'refused'),
                 (Connection(Response(401, b'')), 'refused'), (Connection(Response(503, b'')), 'unreachable'),
                 (Connection(error=ConnectionRefusedError()), 'unreachable'),
                 (Connection(error=TimeoutError()), 'unreachable'),
                 (Connection(Response(200, b'{"a":1,"a":2}')), 'response'),
                 (Connection(Response(200, b'not json')), 'response'),
                 (Connection(Response(200, b'{}', encoding='gzip')), 'response'),
                 (Connection(Response(200, b' ' * (rl.VM_CAP + 1))), 'response'))
        for connection, category in cases:
            with self.subTest(category=category), self.assertRaises(rl.VmError) as caught:
                self.reader(connection).read((ADMIN, PREFIX), DAY0, DAY0 + 600)
            self.assertEqual(caught.exception.args, (category,))

    def test_no_request_without_a_valid_identity(self):
        connection = Connection(Response(200, b'{}'))
        for identity in (None, (ADMIN,), ('bad user', PREFIX), (ADMIN, '/elsewhere'), (ADMIN, None)):
            with self.subTest(identity=identity), self.assertRaises(rl.VmError) as caught:
                self.reader(connection).read(identity, DAY0, DAY0 + 600)
            self.assertEqual(caught.exception.args, ('no_identity',))
        self.assertEqual(connection.requests, [])


class FakeSocket:
    """The HA WebSocket side of statistics_during_period, scripted."""

    def __init__(self, answer):
        self.answer, self.sent, self.closed = answer, [], False
        self.outbox = [json.dumps({'type': 'auth_required'})]

    def receive(self, timeout):
        return self.outbox.pop(0) if self.outbox else None

    def send_json(self, document):
        self.sent.append(document)
        if document.get('type') == 'auth':
            self.outbox.append(json.dumps({'type': 'auth_ok'}))
        else:
            self.outbox.append(json.dumps(self.answer(document)))

    def close(self):
        self.closed = True


class StatisticsTests(unittest.TestCase):
    def read(self, answer, start=DAY0, end=DAY0 + 25 * DAY):
        socket_ = FakeSocket(answer)
        rows = ws.statistics_during_period('token', ROOM, start, end, connect=lambda *_a: socket_)
        return rows, socket_

    def test_hourly_rows_in_ten_day_pieces(self):
        def answer(command):
            low = ws.datetime.fromisoformat(command['start_time']).timestamp()
            rows = [{'start': (low + hour * 3600) * 1000, 'end': (low + hour * 3600 + 3600) * 1000,
                     'mean': 20.5, 'min': 20.0, 'max': 21.0} for hour in range(2)]
            return {'id': command['id'], 'type': 'result', 'success': True, 'result': {ROOM: rows}}
        rows, socket_ = self.read(answer)
        commands = socket_.sent[1:]
        self.assertEqual(socket_.sent[0], {'type': 'auth', 'access_token': 'token'})
        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0], {'id': 1, 'type': 'recorder/statistics_during_period',
                                       'start_time': '2033-05-18T00:00:00+00:00',
                                       'end_time': '2033-05-28T00:00:00+00:00', 'statistic_ids': [ROOM],
                                       'period': 'hour', 'types': ['mean', 'min', 'max'],
                                       'units': {'temperature': '°C'}})
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0], (DAY0, 20.5, 20.0, 21.0))
        self.assertTrue(socket_.closed)

    def test_iso_starts_and_no_statistics(self):
        def iso(command):
            return {'id': command['id'], 'type': 'result', 'success': True, 'result': {ROOM: [
                {'start': '2033-05-18T00:00:00+00:00', 'mean': 19.0, 'min': None, 'max': 'x'}]}}
        rows, _socket = self.read(iso, end=DAY0 + DAY)
        self.assertEqual(rows, [(DAY0, 19.0, None, None)])
        rows, _socket = self.read(lambda c: {'id': c['id'], 'type': 'result', 'success': True, 'result': {}})
        self.assertEqual(rows, [])

    def test_refused_or_malformed_raise_fixed_categories(self):
        for answer, category in ((lambda c: {'id': c['id'], 'type': 'result', 'success': False}, 'refused'),
                                 (lambda c: {'id': c['id'], 'type': 'result', 'success': True, 'result': []}, 'protocol'),
                                 (lambda c: {'id': 99, 'type': 'result', 'success': True, 'result': {}}, 'protocol')):
            with self.subTest(category=category), self.assertRaises(ws.WebSocketError) as caught:
                self.read(answer)
            self.assertEqual(caught.exception.args, (category,))


class FlowTests(unittest.TestCase):
    def world(self, **options):
        world = LearningWorld(normal_room, days=30)
        world.vm = FakeVm(**options)
        world.statistics = statistics_for(normal_room, world.history.start)
        world.learning.vm, world.learning.statistics = world.vm, world.statistics
        return world

    def test_the_owner_starts_it_and_the_vm_is_read_as_that_user(self):
        world = self.world()
        body = {'aktion': 'anlernen', 'entity_id': ROOM, 'tage': 0, 'geprueft': False,
                'revision': __import__('room_picker').revision(world.stored)}
        status, document = world.picker.learn(ADMIN, world.token(), json.dumps(body).encode(), prefix=PREFIX)
        self.assertEqual((status, document['ok']), (200, True), document)
        end = world.wallclock.wall
        self.assertEqual(world.vm.calls, [((ADMIN, PREFIX), end - 30 * DAY, end)])
        self.assertEqual(world.statistics.calls, [('synthetic-supervisor-token', ROOM, end - 365 * DAY, end)])
        entry, _learned, _running = world.learning.view(ROOM)
        self.assertEqual(entry['state'], 'proposal')
        self.assertTrue(any(line.startswith('Raumtemperatur: Langzeitstatistik') for line in entry['evidence']))
        self.assertTrue(any(line.startswith('Heizkreise: VM-Verlauf, alle 5 Minuten, 30 Tage')
                            for line in entry['evidence']), entry['evidence'])
        self.assertEqual(world.posted, [])                       # nothing written
        page = world.page()
        self.assertIn('Langzeitstatistik von Home Assistant', page)
        self.assertIn('alles Verfügbare (bis 1 Jahr)', page)
        self.assertIn('letzte 90 Tage', page)

    def test_vm_refusal_is_shown_and_the_proposal_still_comes_from_home_assistant(self):
        world = self.world(error='refused')
        status, _document = world.act('anlernen', days=14)
        self.assertEqual(status, 200)
        entry, _learned, _running = world.learning.view(ROOM)
        self.assertEqual(entry['state'], 'proposal')
        self.assertTrue(any('VM-Verlauf nicht verwendet (die VM hat den Zugriff abgelehnt)' in line
                            for line in entry['evidence']), entry['evidence'])

    def test_without_an_ingress_path_no_vm_read_is_tried(self):
        world = self.world()
        world.act('anlernen', days=7)                             # act() passes no prefix
        self.assertEqual(len(world.vm.calls), 1)
        self.assertIsNone(world.vm.calls[0][0])

    def test_ninety_days_are_offered_and_bounded(self):
        world = self.world()
        status, _document = world.act('anlernen', days=90)
        self.assertEqual(status, 200)
        for bad in (60, 365, 400, -1):
            status, document = world.act('anlernen', days=bad)
            self.assertEqual(status, 400, (bad, document))

    def test_a_recording_is_analysed_as_the_visiting_administrator(self):
        world = self.world()
        world.act('aufzeichnen', days=3)
        world.wallclock.wall += 3 * DAY + 60
        world.history.end = world.wallclock.wall
        world.picker.page(ADMIN, 'nonce', prefix=PREFIX)
        for _ in range(200):
            entry, _learned, running = world.learning.view(ROOM)
            if entry is not None and entry['state'] != 'recording' and not running:
                break
            threading.Event().wait(0.02)
        self.assertEqual(world.vm.calls[-1][0], (ADMIN, PREFIX))


if __name__ == '__main__':
    unittest.main()
