"""House monitor transport, worker and the section 9 contract (synthetic, loopback only)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import re
import threading
import time
import unittest
from unittest.mock import patch

import house_monitor as hm
import house_rules as r
import ingress_proxy
import run_app
from ha_websocket import WebSocketError
from house_test_support import context_document, circuit, stamp
import test_house_model as model

ENTITIES = ['sensor.wohnzimmer', 'sensor.kueche']
OPTIONS = {'heizkreis_1_sensoren': ENTITIES}
TOPIC = 'hargassner/gateway_1/house/context'

# --- independent contract checker (section 9.3 and 9.4) ---------------------
TABLE = {  # code: (max level, label, circuit, room, target, count)
    'room_cold': ('Hinweis', 'req', 'req', 'req', 'req', 'null'),
    'circuit_cold': ('Störung', 'null', 'req', 'req', 'req', 'req'),
    'no_response': ('Hinweis', 'req', 'req', 'null', 'null', 'null'),
    'circuit_no_response': ('Störung', 'null', 'req', 'null', 'null', 'req'),
    'room_warm': ('Hinweis', 'req', 'req', 'req', 'req', 'null'),
    'circuit_warm': ('Hinweis', 'null', 'req', 'req', 'req', 'null'),
    'room_hot': ('Hinweis', 'req', 'req', 'req', 'null', 'null'),
    'limit_cold': ('Hinweis', 'req', 'req', 'req', 'req', 'null'),
    'room_frost': ('Hinweis', 'req', 'opt', 'req', 'null', 'null'),
    'frost': ('Störung', 'null', 'null', 'req', 'null', 'req'),
    'sensor_missing': ('Hinweis', 'req', 'opt', 'null', 'null', 'null'),
    'sensor_unavailable': ('Hinweis', 'req', 'opt', 'null', 'null', 'null'),
    'sensor_stale': ('Hinweis', 'req', 'opt', 'null', 'null', 'null'),
    'sensor_implausible': ('Hinweis', 'req', 'opt', 'null', 'null', 'null'),
    'sensor_jumping': ('Hinweis', 'req', 'opt', 'null', 'null', 'null'),
    'sensor_stuck': ('Hinweis', 'req', 'opt', 'null', 'null', 'null'),
    'sensor_battery': ('Hinweis', 'req', 'opt', 'null', 'null', 'null'),
    'sensors_all_unavailable': ('Hinweis', 'null', 'null', 'null', 'null', 'req'),
    'config_invalid': ('Hinweis', 'null', 'null', 'null', 'null', 'null'),
    'config_circuit_absent': ('Hinweis', 'null', 'req', 'null', 'null', 'null'),
}


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def check_report(test, payload):
    raw = payload.encode('utf-8')
    test.assertLessEqual(len(raw), 16384)
    report = r.strict_json(raw)
    test.assertEqual(json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=False), payload)
    test.assertEqual(set(report), {'schema', 'context_majors', 'app_version', 'instance', 'seq', 'context', 'mode',
                                   'monitor', 'reason', 'sensors', 'circuits', 'findings', 'folded'})
    test.assertEqual(report['schema'], 'harg-house-report/1')
    test.assertTrue(isinstance(report['context_majors'], list) and 1 <= len(report['context_majors']) <= 4)
    test.assertTrue(all(integer(m, 1, 99) for m in report['context_majors']))
    test.assertRegex(report['app_version'], r'\A[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\Z')
    test.assertRegex(report['instance'], r'\A[0-9a-f]{32}\Z')
    test.assertTrue(integer(report['seq'], 1, 2 ** 53 - 1))
    test.assertTrue(report['context'] is None or re.fullmatch(r'[0-9a-f]{16}:[0-9]{1,15}', report['context']))
    test.assertIn(report['mode'], ('beobachten', 'melden'))
    test.assertIn(report['monitor'], ('ok', 'unknown'))
    if report['monitor'] == 'ok':
        test.assertIsNone(report['reason'])
    else:
        test.assertIn(report['reason'], ('config_invalid', 'ha_starting', 'ha_api_unavailable', 'no_context',
                                         'context_version_unsupported'))
    sensors = report['sensors']
    test.assertEqual(set(sensors), {'configured', 'usable', 'unavailable'})
    test.assertTrue(all(integer(v, 0, 24) for v in sensors.values()))
    test.assertLessEqual(sensors['usable'] + sensors['unavailable'], sensors['configured'])
    test.assertEqual(set(report['circuits']), {'hc1', 'hc2'})
    for row in report['circuits'].values():
        test.assertEqual(set(row), {'type', 'type_source', 'sensors', 'judged', 'why'})
        test.assertIn(row['type'], ('radiator', 'floor', 'absent', None))
        test.assertIn(row['type_source'], ('controller', 'option', None))
        test.assertTrue(integer(row['sensors'], 0, 24))
        test.assertIn(row['judged'], ('yes', 'inactive', 'unknown'))
        if row['judged'] == 'unknown':
            test.assertIn(row['why'], ('context', 'phase', 'type', 'sensors', 'absent', 'ha'))
        else:
            test.assertIsNone(row['why'])
    test.assertTrue(isinstance(report['findings'], list) and len(report['findings']) <= 24)
    test.assertTrue(integer(report['folded'], 0, 1000))
    seen = set()
    for finding in report['findings']:
        test.assertEqual(set(finding), {'code', 'level', 'label', 'circuit', 'held_s', 'room', 'target', 'count'})
        top, label, circ, room, target, count = TABLE[finding['code']]
        test.assertIn(finding['level'], ('Hinweis', 'Störung') if top == 'Störung' else ('Hinweis',))
        for value, rule, valid in ((finding['label'], label, lambda v: isinstance(v, str)
                                    and re.fullmatch(r'[\w ÄÖÜäöüß.,()/+-]{1,40}', v)
                                    and not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+', v)),
                                   (finding['circuit'], circ, lambda v: v in ('hc1', 'hc2')),
                                   (finding['room'], room, lambda v: type(v) in (int, float) and -40 <= v <= 80 and (v * 2) == int(v * 2)),
                                   (finding['target'], target, lambda v: type(v) in (int, float) and -40 <= v <= 80 and (v * 2) == int(v * 2)),
                                   (finding['count'], count, lambda v: integer(v, 1, 24))):
            if rule == 'null':
                test.assertIsNone(value, finding)
            elif rule == 'req':
                test.assertTrue(value is not None and valid(value), finding)
            elif value is not None:
                test.assertTrue(valid(value), finding)
        test.assertTrue(integer(finding['held_s'], 0, 2592000))
        identity = (finding['code'], finding['circuit'], finding['label'])
        test.assertNotIn(identity, seen)
        seen.add(identity)
    for number in re.findall(r'"(?:room|target)":(-?[0-9.]+)', payload):
        test.assertRegex(number, r'\A-?[0-9]+\.[05]\Z')
    return report


# --- fakes -------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.mono, self.wall = 5000.0, 2_000_000_000.0

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds


class FakeApi:
    def __init__(self, token, entities, world):
        self.token, self.entities, self.world = token, entities, world
        world.apis.append(self)

    def config(self):
        self.world.calls.append(('config',))
        if self.world.down:
            raise hm.ApiError('unavailable')
        return {'state': self.world.ha_state, 'time_zone': 'UTC'}

    def state(self, entity):
        self.world.calls.append(('state', entity))
        if self.world.down:
            raise hm.ApiError('unavailable')
        value = self.world.values.get(entity, 'missing')
        if value == 'missing':
            return 'missing'
        return {'entity_id': entity, 'state': str(value), 'last_reported': stamp(self.world.clock.wall),
                'attributes': {'unit_of_measurement': '°C', 'friendly_name': entity.split('.')[1].title()}}

    def states(self):
        self.world.calls.append(('states',))
        return self.world.all_states

    def publish(self, topic, payload):
        self.world.calls.append(('publish', topic))
        if self.world.publish_fails:
            raise hm.ApiError('status')
        self.world.published.append((topic, payload))


class FakeSubscription:
    def __init__(self, token, world):
        self.world = world
        world.subscriptions.append(self)
        self.closed = False

    def open(self):
        if self.world.refuse_socket:
            raise WebSocketError('subscribe_refused')

    def poll(self, _timeout):
        messages, self.world.inbox = self.world.inbox, []
        return messages

    def close(self):
        self.closed = True


class NoWait:
    def __init__(self):
        self.flag = False

    def is_set(self):
        return self.flag

    def wait(self, _seconds):
        return self.flag

    def set(self):
        self.flag = True


class World:
    def __init__(self, options=OPTIONS, values=None):
        self.clock = Clock()
        self.values = dict(values or {e: 21.0 for e in ENTITIES})
        self.ha_state, self.down, self.publish_fails, self.refuse_socket = 'RUNNING', False, False, False
        self.calls, self.published, self.inbox, self.apis, self.subscriptions = [], [], [], [], []
        self.all_states = []
        self.logs = []
        self.monitor = hm.HouseMonitor(
            r.parse_house_options(options), token_source=lambda: 'synthetic-supervisor-token',
            api_factory=lambda token, entities: FakeApi(token, entities, self),
            subscription_factory=lambda token: FakeSubscription(token, self),
            clock=lambda: self.clock.wall, monotonic=lambda: self.clock.mono, log=self.logs.append)
        self.stop = NoWait()
        self.sequence = 0

    def context(self, topic=TOPIC, **top):
        self.sequence += 1
        document = context_document(self.sequence, **top)
        self.inbox.append((topic, json.dumps(document), False))
        return document

    def cycle(self, seconds=1):
        self.monitor.cycle(self.stop)
        self.clock.advance(seconds)

    def run(self, seconds, context_every=60):
        for second in range(seconds):
            if context_every and second % context_every == 0:
                self.context()
            self.cycle()

    def reports(self):
        return [json.loads(payload) for _topic, payload in self.published]


class WorkerTests(unittest.TestCase):
    def test_no_base_means_no_report_and_context_pins_the_base(self):
        world = World()
        world.run(300, context_every=0)
        self.assertEqual(world.published, [])
        self.assertEqual(world.monitor.engine.reason, 'no_context')
        world.context()
        world.cycle()
        self.assertEqual(world.monitor.base, 'hargassner/gateway_1')
        world.run(130)
        self.assertTrue(world.published)
        self.assertEqual({topic for topic, _p in world.published}, {'hargassner/gateway_1/house/report'})

    def test_foreign_bases_and_bad_topics_are_ignored_and_counted(self):
        world = World()
        world.context()
        world.cycle()
        world.context(topic='hargassner/other/house/context')
        world.inbox.append(('hargassner/gateway_1/house/context/x', '{}', False))
        world.inbox.append(('hargassner/gateway_1/house/context', json.dumps(context_document(9)), True))
        world.cycle()
        self.assertEqual(world.monitor.base, 'hargassner/gateway_1')
        self.assertEqual(world.monitor.counters['foreign_base'], 1)
        self.assertEqual(world.monitor.counters['context_invalid'], 2)

    def test_report_cadence_seq_and_canonical_payload(self):
        world = World(values={ENTITIES[0]: 21.0, ENTITIES[1]: 'missing'})
        world.run(1000)
        times = [index for index, call in enumerate(world.calls) if call[0] == 'publish']
        reports = world.reports()
        self.assertGreaterEqual(len(reports), 7)
        self.assertEqual([report['seq'] for report in reports], list(range(1, len(reports) + 1)))
        self.assertEqual(len({report['instance'] for report in reports}), 1)
        for _topic, payload in world.published:
            check_report(self, payload)
        self.assertTrue(times)
        # The sensor_missing finding appears after 30 min: reported within 10 s.
        world.run(1800)
        stamps = []
        for topic, payload in world.published:
            stamps.append(json.loads(payload))
        first = next(i for i, rep in enumerate(stamps) if rep['findings'])
        self.assertEqual(stamps[first]['findings'][0]['code'], 'sensor_missing')
        self.assertNotIn('sensor.', json.dumps(stamps))   # no entity ids in reports

    def test_report_timing(self):
        world = World()
        published = []
        original = FakeApi.publish

        def record(api, topic, payload):
            published.append(world.clock.mono)
            original(api, topic, payload)
        with patch.object(FakeApi, 'publish', record):
            world.run(1200)
        gaps = [b - a for a, b in zip(published, published[1:])]
        self.assertTrue(all(gap >= 10 for gap in gaps))
        self.assertTrue(all(gap <= 121 for gap in gaps), gaps)

    def test_change_triggered_report_within_ten_seconds(self):
        world = World(values={ENTITIES[0]: 21.0, ENTITIES[1]: 'missing'})
        world.run(1795)
        before = len(world.published)
        identities = None
        for _ in range(130):
            world.context() if world.clock.mono % 60 == 0 else None
            world.cycle()
            if any(r_['findings'] for r_ in world.reports()[before:]):
                identities = world.clock.mono
                break
        self.assertIsNotNone(identities)
        sample_time = world.monitor.next_sample - 60
        self.assertLessEqual(identities - sample_time, 11)

    def test_instances_are_random(self):
        self.assertNotEqual(World().monitor.instance, World().monitor.instance)
        self.assertRegex(World().monitor.instance, r'\A[0-9a-f]{32}\Z')

    def test_context_expires_after_180_seconds_and_resubscribes(self):
        world = World()
        world.context()
        world.run(170, context_every=0)
        self.assertEqual(world.monitor.fresh_context(world.clock.mono)[0], 'ok')
        world.run(20, context_every=0)
        self.assertEqual(world.monitor.fresh_context(world.clock.mono)[0], 'none')
        self.assertGreaterEqual(len(world.subscriptions), 2)   # reconnected with one fresh subscription
        self.assertTrue(world.subscriptions[0].closed)
        world.run(200, context_every=0)
        report = world.reports()[-1]
        self.assertIsNone(report['context'])
        self.assertEqual((report['monitor'], report['reason']), ('unknown', 'no_context'))

    def test_unknown_context_major(self):
        world = World()
        world.context()
        world.cycle()
        document = context_document(5)
        document['schema'] = 'harg-house-context/2'
        for second in range(300):
            if second % 60 == 0:
                world.inbox.append((TOPIC, json.dumps(document), False))
            world.cycle()
        report = world.reports()[-1]
        self.assertEqual((report['monitor'], report['reason']), ('unknown', 'context_version_unsupported'))
        self.assertEqual(report['context_majors'], [1])
        self.assertIn('House monitor: context_version_unsupported.', world.logs)
        self.assertIn('Version der VM passt nicht', world.monitor.page().decode())

    def test_ha_api_unavailable_pauses_and_reports_unknown(self):
        world = World(values={e: 19.0 for e in ENTITIES})
        world.run(215 * 60)
        self.assertTrue(world.reports()[-1]['findings'])   # room_cold after settle and dwell
        world.down = True
        world.run(600)
        report = world.reports()[-1]
        self.assertEqual((report['monitor'], report['reason']), ('unknown', 'ha_api_unavailable'))
        self.assertTrue(all(row['judged'] == 'unknown' for row in report['circuits'].values()))
        world.ha_state, world.down = 'NOT_RUNNING', False
        world.run(120)
        self.assertEqual(world.reports()[-1]['reason'], 'ha_starting')
        self.assertIn('House monitor: ha_api_unavailable.', world.logs)
        self.assertEqual(sum(1 for line in world.logs if line == 'House monitor: ha_api_unavailable.'), 2)

    def test_only_allowed_requests(self):
        world = World()
        world.run(400)
        kinds = {call[0] for call in world.calls}
        self.assertEqual(kinds, {'config', 'state', 'publish'})
        self.assertEqual({call[1] for call in world.calls if call[0] == 'state'}, set(ENTITIES))

    def test_invalid_config_reports_config_invalid(self):
        world = World(options={'heizkreis_1_sensoren': ['sensor.a', 'sensor.a']})
        world.run(200)
        report = check_report(self, world.published[-1][1])
        self.assertEqual((report['monitor'], report['reason']), ('unknown', 'config_invalid'))
        self.assertEqual([row['code'] for row in report['findings']], ['config_invalid'])
        self.assertEqual(report['sensors']['configured'], 1)
        # The circuit the owner filled is reported as not judged with its entries.
        self.assertEqual({name: (row['sensors'], row['judged'], row['why']) for name, row in report['circuits'].items()},
                         {'hc1': (1, 'unknown', 'ha'), 'hc2': (0, 'unknown', 'ha')})
        self.assertNotIn(('state', 'sensor.a'), world.calls)
        self.assertIn('House monitor: config_invalid.', world.logs)

    def test_empty_lists_report_configured_zero(self):
        world = World(options={})
        world.run(200)
        report = check_report(self, world.published[-1][1])
        self.assertEqual(report['sensors'], {'configured': 0, 'usable': 0, 'unavailable': 0})
        self.assertEqual(report['findings'], [])

    def test_report_ack_and_failures_on_the_page(self):
        world = World()
        world.run(200)
        page = world.monitor.page().decode()
        self.assertIn('Die VM nimmt die Raummeldungen nicht an', page)
        ack = {'instance': world.monitor.instance, 'seq': 1, 'result': 'accepted'}
        world.context(report_ack=ack)
        world.cycle()
        self.assertEqual(world.monitor.status()['ack'], 'accepted')
        self.assertIn('Die VM nimmt die Raummeldungen an.', world.monitor.page().decode())
        world.context(report_ack={**ack, 'result': 'rejected_version'})
        world.cycle()
        self.assertIn('die Version passt nicht', world.monitor.page().decode())
        self.assertIn('House monitor: report_not_accepted.', world.logs)
        world.context(report_ack={**ack, 'instance': 'f' * 32})
        world.cycle()
        self.assertTrue(world.monitor.status()['ack_other_instance'])
        self.assertIn('sie bestätigt eine andere App', world.monitor.page().decode())
        world.publish_fails = True
        world.run(130)
        self.assertEqual(world.monitor.status()['publish'], 'failed')
        self.assertIn('Senden fehlgeschlagen', world.monitor.page().decode())

    def test_websocket_refusal_backs_off(self):
        world = World()
        world.refuse_socket = True
        world.run(200, context_every=0)
        self.assertEqual(len(world.subscriptions), 7)   # at 0, 5, 15, 35, 75, 135 and 195 s
        self.assertEqual(world.monitor.status()['websocket'], 'subscribe_refused')

    def test_worker_survives_internal_errors(self):
        world = World()
        calls = []

        def broken(_stop):
            calls.append(1)
            if len(calls) == 3:
                world.stop.set()
            raise RuntimeError('SECRET text never logged')
        world.monitor.cycle = broken
        world.monitor.run(world.stop)
        self.assertEqual(len(calls), 3)
        self.assertEqual(world.logs, ['House monitor: internal.'])

    def test_candidate_page_lists_temperature_sensors_and_caches(self):
        world = World()
        world.all_states = [
            {'entity_id': 'sensor.wohnzimmer', 'state': '21.5', 'attributes': {'device_class': 'temperature', 'unit_of_measurement': '°C', 'friendly_name': 'Wohn<script>'}},
            {'entity_id': 'sensor.aussen', 'state': '50', 'attributes': {'unit_of_measurement': '°F'}},
            {'entity_id': 'sensor.feuchte', 'state': '40', 'attributes': {'unit_of_measurement': '%'}},
            {'entity_id': 'light.flur', 'state': 'on', 'attributes': {'device_class': 'temperature'}},
        ]
        page = world.monitor.page().decode()
        self.assertIn('sensor.wohnzimmer', page)
        self.assertIn('sensor.aussen', page)
        self.assertNotIn('sensor.feuchte', page)
        self.assertNotIn('light.flur', page)
        self.assertIn('Wohn&lt;script&gt;', page)
        world.monitor.page()
        self.assertEqual(sum(1 for call in world.calls if call[0] == 'states'), 1)
        world.clock.advance(601)
        world.monitor.page()
        self.assertEqual(sum(1 for call in world.calls if call[0] == 'states'), 2)
        self.assertIn('Minuten ohne gültigen Sollwert', page)


class CoreApiWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_any(self):
                length = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(length)
                self.server.requests.append((self.command, self.path, dict(self.headers), body))
                status, payload, headers = self.server.reply
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                if not any(name == 'Content-Length' for name, _v in headers):
                    self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                try:
                    if self.server.trickle:
                        for byte in payload:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(self.server.trickle)
                    else:
                        self.wfile.write(payload)
                except OSError:
                    pass   # the client gave up at its deadline
            do_GET = do_POST = handle_any
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.requests, self.server.reply, self.server.trickle = [], (200, b'{}', []), 0
        self.api = hm.CoreApi('synthetic-token', ENTITIES, host='127.0.0.1', port=self.server.server_address[1])

    def test_allowlist_is_exactly_five_requests(self):
        api = self.api
        report = {'topic': 'hargassner/gw/house/report', 'payload': '{}', 'qos': 0, 'retain': False}
        self.assertTrue(api.allowed('GET', '/core/api/config', None))
        self.assertTrue(api.allowed('GET', '/core/api/states', None))
        self.assertTrue(api.allowed('GET', '/core/api/states/sensor.wohnzimmer', None))
        self.assertTrue(api.allowed('POST', '/core/api/services/mqtt/publish', report))
        for method, path, body in [
                ('GET', '/core/api/states/sensor.other', None), ('GET', '/core/api/states/light.flur', None),
                ('POST', '/core/api/services/mqtt/publish', {**report, 'retain': True}),
                ('POST', '/core/api/services/mqtt/publish', {**report, 'qos': 1}),
                ('POST', '/core/api/services/mqtt/publish', {**report, 'topic': 'hargassner/gw/command/x'}),
                ('POST', '/core/api/services/mqtt/publish', {**report, 'extra': 1}),
                ('POST', '/core/api/services/mqtt/publish', {**report, 'payload': 'x' * 16385}),
                ('POST', '/core/api/services/climate/set_temperature', report),
                ('POST', '/core/api/services/persistent_notification/create', report),
                ('GET', '/core/api/services', None), ('DELETE', '/core/api/states/sensor.wohnzimmer', None),
                ('POST', '/core/api/states/sensor.wohnzimmer', {}), ('GET', '/core/api/config', {})]:
            with self.subTest(method=method, path=path):
                self.assertFalse(api.allowed(method, path, body))
                with self.assertRaises(hm.ApiError):
                    api.request(method, path, body)
        self.assertEqual(self.server.requests, [])

    def test_publish_body_and_headers(self):
        self.api.publish('hargassner/gw/house/report', '{"a":"ä"}')
        method, path, headers, body = self.server.requests[0]
        self.assertEqual((method, path), ('POST', '/core/api/services/mqtt/publish'))
        self.assertEqual(json.loads(body), {'topic': 'hargassner/gw/house/report', 'payload': '{"a":"ä"}',
                                            'qos': 0, 'retain': False})
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-token')

    def test_states_404_caps_and_strict_json(self):
        self.server.reply = (404, b'{"message":"Entity not found."}', [])
        self.assertEqual(self.api.state('sensor.wohnzimmer'), 'missing')
        self.server.reply = (200, b'{"state":"1","state":"2"}', [])
        with self.assertRaises(hm.ApiError):
            self.api.state('sensor.wohnzimmer')
        self.server.reply = (200, b'{"a":' + b'1' * 70000 + b'}', [])
        with self.assertRaises(hm.ApiError):
            self.api.state('sensor.wohnzimmer')
        self.server.reply = (200, b'{}', [('Content-Length', str(10 ** 9))])
        with self.assertRaises(hm.ApiError):
            self.api.config()
        self.server.reply = (200, b'{}', [('Content-Encoding', 'gzip')])
        with self.assertRaises(hm.ApiError):
            self.api.config()
        self.server.reply = (401, b'{}', [])
        with self.assertRaises(hm.ApiError):
            self.api.config()
        self.server.reply = (200, json.dumps([{'x': 'y' * 100000}]).encode(), [])
        self.assertEqual(len(self.api.states()), 1)        # 8 MiB cap for the candidate list

    def test_wall_deadline(self):
        self.server.reply, self.server.trickle = (200, b'{"state":"RUNNING"}' + b' ' * 200, []), 0.03
        started = time.monotonic()
        with patch.object(hm, 'REQUEST_DEADLINE_S', 1):
            with self.assertRaises(hm.ApiError):
                self.api.config()
        self.assertLess(time.monotonic() - started, 2.5)


class AppIntegrationTests(unittest.TestCase):
    def serve(self, house_page):
        settings = ingress_proxy.Settings('unused.example', 443, 'unused.example', 'i' * 43)
        server = run_app.MonitorProxyServer(('127.0.0.1', 0), settings, run_app.disabled_status, house_page)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        peer = patch.object(ingress_proxy, 'SUPERVISOR_IP', '127.0.0.1')
        peer.start()

        def cleanup():
            server.shutdown(); server.server_close(); thread.join(); peer.stop()
        self.addCleanup(cleanup)
        return server

    def request(self, server, method='GET', path='/api/local-monitor/raumsensoren', identity=True):
        client = http.client.HTTPConnection(*server.server_address, timeout=3)
        headers = {'X-Remote-User-Id': 'synthetic-user', 'X-Ingress-Path': '/api/hassio_ingress/synthetic_test_path_1234'} if identity else {}
        client.request(method, path, headers=headers)
        response = client.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        client.close()
        return result

    def test_room_page_gate_and_headers(self):
        world = World()
        seen = []

        def page(user, nonce):
            seen.append((user, nonce))
            return world.monitor.page()
        server = self.serve(page)
        status, body, headers = self.request(server)
        self.assertEqual(status, 200)
        self.assertIn('Raumüberwachung', body.decode())
        nonce = seen[0][1]
        self.assertEqual(seen[0][0], 'synthetic-user')
        self.assertRegex(nonce, r'\A[A-Za-z0-9_-]{24}\Z')
        self.assertEqual(headers['Content-Security-Policy'],
                         f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
                         "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('synthetic-supervisor-token', body.decode())
        self.assertEqual(self.request(server, identity=False)[0], 400)
        self.request(server)
        self.assertNotEqual(seen[1][1], nonce)   # a fresh nonce per response
        # A POST without JSON, origin and page token is refused before any save.
        self.assertEqual(self.request(server, method='POST')[0], 415)
        for method in ('PUT', 'DELETE', 'PATCH', 'OPTIONS'):
            self.assertEqual(self.request(server, method=method)[0], 405)
        self.assertEqual(self.request(server, path='/api/local-monitor/raumsensoren?x=1')[0], 400)
        with patch.object(ingress_proxy, 'SUPERVISOR_IP', '172.30.32.2'):
            self.assertEqual(self.request(server)[0], 403)
        status, body, _headers = self.request(server, path='/api/local-monitor/status')
        self.assertEqual(json.loads(body), run_app.disabled_status())   # the status schema is unchanged

    def test_broken_house_page_never_breaks_ingress(self):
        def broken():
            raise RuntimeError('SECRET')
        server = self.serve(broken)
        status, body, _headers = self.request(server)
        self.assertEqual(status, 503)
        self.assertNotIn(b'SECRET', body)
        server = self.serve(None)
        self.assertEqual(self.request(server)[0], 503)
        self.assertEqual(self.request(server, path='/api/local-monitor/status')[0], 200)

    def test_main_starts_house_thread_and_survives_invalid_options(self):
        started, stopped = threading.Event(), threading.Event()
        created = []

        class Server:
            def __init__(self, *args):
                created.append(args)
            def serve_forever(self):
                stopped.wait(2)
            def shutdown(self):
                stopped.set()
            def server_close(self):
                pass

        class House:
            def __init__(self, options):
                self.options = options
            def page(self):
                return b''
            def run(self, stop):
                started.set()
                stop.wait(2)

        def install(_name, callback):
            threading.Timer(.1, callback).start()
        disabled = run_app.MonitorSettings(False, ingress_proxy.Settings('dashboard.example', 443, 'dashboard.example', 'i' * 43))
        invalid = r.HouseOptions(False, raw_count=3)
        with patch.object(run_app.MonitorSettings, 'load', return_value=disabled), \
                patch.object(run_app, 'MonitorProxyServer', Server), \
                patch.object(run_app, 'load_house_options', return_value=invalid), \
                patch.object(run_app, 'HouseMonitor', House), \
                patch.object(run_app.signal, 'signal', side_effect=install), \
                patch('builtins.print'):
            self.assertEqual(run_app.main(), 0)
        self.assertTrue(started.is_set())
        self.assertEqual(len(created), 1)
        with patch.object(run_app, 'load_house_options', side_effect=RuntimeError('boom')), patch('builtins.print') as printed:
            self.assertIsNone(run_app.start_house_monitor())
        printed.assert_called_once_with('House monitor: internal.', flush=True)


class ContractTests(unittest.TestCase):
    def test_reports_from_fault_scenarios_meet_the_contract(self):
        house = model.radiator_house()
        house.run(2 * model.DAY)
        for room in house.rooms:
            room.fault = 'low'
        house.dead.add('sensor.bad')
        seen = set()
        for minute in range(2 * model.DAY):
            house.tick(gaps=False)
            if minute % 30 == 0:
                engine = house.h.engine
                engine.refresh_labels()
                _report, payload = r.build_report(
                    instance='0123456789abcdef0123456789abcdef', seq=minute + 1, context_id='abcdef0123456789:7',
                    mode='melden', monitor=engine.monitor, reason=engine.reason,
                    sensors=engine.sensors_report(house.h.now), circuits=engine.circuits_report(),
                    findings=engine._findings(house.h.now, engine.ctx))
                report = check_report(self, payload)
                seen.update(row['code'] for row in report['findings'])
        self.assertTrue({'room_cold', 'sensor_unavailable'} <= seen)

    def test_truncated_report_with_many_findings(self):
        rows = [{'code': 'sensor_battery', 'level': 'Hinweis', 'label': f'Raum {i}', 'circuit': None,
                 'held_s': 60, 'room': None, 'target': None, 'count': None} for i in range(30)]
        _report, payload = r.build_report(instance='a' * 32, seq=2 ** 53 - 1, context_id=None, mode='beobachten',
                                          monitor='unknown', reason='no_context',
                                          sensors={'configured': 24, 'usable': 24, 'unavailable': 0},
                                          circuits={n: {'type': 'floor', 'type_source': 'option', 'sensors': 12,
                                                        'judged': 'unknown', 'why': 'context'} for n in r.CIRCUITS},
                                          findings=rows)
        report = check_report(self, payload)
        self.assertEqual((len(report['findings']), report['folded']), (24, 6))

    def test_worst_case_size_stays_below_the_limit(self):
        label = 'Ä' * 40
        rows = [{'code': 'room_cold', 'level': 'Hinweis', 'label': label[:36] + f'{i:04d}', 'circuit': 'hc1',
                 'held_s': 2592000, 'room': -39.5, 'target': 29.5, 'count': None} for i in range(24)]
        _report, payload = r.build_report(instance='a' * 32, seq=2 ** 53 - 1, context_id='f' * 16 + ':' + '9' * 15,
                                          mode='beobachten', monitor='unknown', reason='context_version_unsupported',
                                          sensors={'configured': 24, 'usable': 0, 'unavailable': 24},
                                          circuits={n: {'type': 'absent', 'type_source': 'controller', 'sensors': 24,
                                                        'judged': 'unknown', 'why': 'sensors'} for n in r.CIRCUITS},
                                          findings=rows)
        self.assertLess(len(payload.encode()), 16384)

    def test_context_fixture_matches_the_section_9_2_shape(self):
        document = context_document()
        self.assertEqual(set(document), {'schema', 'context', 'online', 'maintenance', 'outdoor', 'outdoor_avg',
                                         'report_ack', 'circuits'})
        self.assertEqual(set(document['circuits']['hc1']), set(circuit()))
        self.assertEqual(set(circuit()), {'mode', 'phase', 'target', 'day_target', 'setback_target', 'status', 'pump',
                                          'flow', 'flow_target', 'type', 'type_source'})


if __name__ == '__main__':
    unittest.main()
