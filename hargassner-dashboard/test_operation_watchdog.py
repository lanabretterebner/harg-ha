import json
import unittest
import threading

from operation_watchdog import Verdict, WatchdogApi, OperationWatchdog, LEASE, DISCOVERY
from house_monitor import PUBLISH_PATH


class WatchdogTests(unittest.TestCase):
    def test_worker_owns_only_checked_topic_and_same_discovery(self):
        stop = threading.Event()
        sent = []
        count = [0]
        parent = self
        class Sub:
            def __init__(self, token, topic): self.topic = topic
            def open(self): pass
            def close(self): pass
            def poll(self, timeout):
                if self.topic == DISCOVERY:
                    return [(parent.topic, json.dumps(parent.config), True)] if count[0] == 0 else []
                count[0] += 1
                if count[0] == 1:
                    return [(parent.config['state_topic'], json.dumps(dict(state='Normal', summary='fixture',
                             checks={}, problems=[], published_at=parent.wall)), False)]
                parent.tick += 100
                parent.wall += 100
                stop.set()
                return []
        class Api:
            def __init__(self, token, verdict): pass
            def send(self, topic, document, discovery=False): sent.append((topic, document, discovery))
        worker = OperationWatchdog(token_source=lambda: 'synthetic', subscription_factory=Sub,
                                   api_factory=Api, monotonic=lambda: self.tick, clock=lambda: self.wall)
        worker.run(stop)
        states = [d['state'] for _t,d,discovery in sent if not discovery]
        self.assertIn('Normal', states)
        self.assertEqual(states[-1], 'Störung')
        self.assertEqual([t for t,_d,discovery in sent if discovery], [self.topic])
        self.assertNotIn(self.config['state_topic'], [t for t,_d,_r in sent])

    def setUp(self):
        self.tick, self.wall = 0., 1000.
        self.v = Verdict(lambda: self.tick, lambda: self.wall)
        self.config = dict(name='Betriebsüberwachung', unique_id='hargassner_fixture_unit_sensor_betriebsueberwachung',
                           default_entity_id='sensor.betriebsueberwachung', device_class='enum',
                           options=['Normal', 'Hinweis', 'Störung', 'Unbekannt'], device={'identifiers': ['fixture']},
                           state_topic='hargassner/fixture/ha/operation', json_attributes_topic='hargassner/fixture/ha/operation',
                           value_template='{{ value_json.state }}', expire_after=300)
        self.topic = 'homeassistant/sensor/fixture/betriebsueberwachung/config'
        self.assertTrue(self.v.discovery(self.topic, json.dumps(self.config)))

    def report(self, state='Normal', retained=False):
        payload = dict(state=state, summary='synthetic', problems=[], checks={}, published_at=self.wall)
        return self.v.ingest(self.config['state_topic'], json.dumps(payload), retained)

    def advance(self, seconds):
        self.tick += seconds
        self.wall += seconds

    def test_vm_crash_cannot_leave_normal(self):
        self.assertTrue(self.report())
        self.assertEqual(self.v.document()['state'], 'Normal')
        self.advance(LEASE)
        self.assertEqual(self.v.document()['state'], 'Störung')
        self.assertIn('Heizungszustand unbekannt', self.v.document()['summary'])

    def test_retained_normal_never_proves_liveness_even_after_restart(self):
        self.assertFalse(self.report(retained=True))
        self.assertEqual(self.v.document()['state'], 'Unbekannt')
        self.advance(LEASE)
        self.assertFalse(self.report(retained=True))
        self.assertEqual(self.v.document()['state'], 'Störung')

    def test_heartbeat_failure_overrides_even_recent_normal(self):
        self.report()
        self.assertEqual(self.v.document({'status': 'unknown'})['state'], 'Störung')
        self.assertEqual(self.v.document({'status': 'healthy'})['state'], 'Normal')
        self.advance(LEASE)
        self.assertEqual(self.v.document({'status': 'healthy'})['state'], 'Störung')

    def test_recovery_requires_fresh_live_report(self):
        self.report(); self.advance(100)
        self.assertEqual(self.v.document()['state'], 'Störung')
        self.assertFalse(self.report(retained=True))
        self.assertEqual(self.v.document()['state'], 'Störung')
        self.report('Hinweis')
        self.assertEqual(self.v.document()['state'], 'Hinweis')

    def test_stale_future_malformed_and_foreign_reports_refused(self):
        for at in (self.wall - 90, self.wall + 6, float('inf'), None, True):
            raw = json.dumps(dict(state='Normal', summary='', checks={}, problems=[], published_at=at))
            self.assertFalse(self.v.ingest(self.config['state_topic'], raw))
        self.assertFalse(self.v.ingest('hargassner/other/ha/operation', '{}'))
        self.assertFalse(self.v.ingest(self.config['state_topic'], '{bad'))
        self.assertEqual(self.v.document()['state'], 'Unbekannt')

    def test_clock_step_never_extends_old_healthy_report(self):
        self.report(); self.wall -= 100
        self.assertEqual(self.v.document()['state'], 'Störung')

    def test_discovery_preserves_identity_and_owns_distinct_nonretained_output(self):
        for key in ('unique_id', 'default_entity_id', 'device', 'name'):
            self.assertEqual(self.v.config[key], self.config[key])
        self.assertNotEqual(self.v.output_topic, self.v.source_topic)
        self.assertEqual(self.v.config['expire_after'], LEASE)
        self.assertFalse(self.v.discovery(self.topic, json.dumps(self.v.config)))
        self.assertTrue(self.v.discovery(self.topic, json.dumps(self.config)))
        restarted = Verdict(lambda: self.tick, lambda: self.wall)
        self.assertTrue(restarted.discovery(self.topic, json.dumps(self.v.config)))
        self.assertEqual(restarted.source_topic, self.v.source_topic)
        self.assertEqual(restarted.document()['state'], 'Unbekannt')

    def test_foreign_or_invalid_discovery_does_not_redirect_output(self):
        for changed in (dict(unique_id='unrelated'), dict(options=[{}]), dict(default_entity_id='sensor.other'), dict(state_topic='commands')):
            self.assertFalse(self.v.discovery(self.topic, json.dumps({**self.config, **changed})))
        self.assertFalse(self.v.discovery('homeassistant/sensor/other/betriebsueberwachung/config', json.dumps(self.config)))

    def test_transport_cannot_send_commands_or_retained_healthy_states(self):
        api = WatchdogApi('synthetic', self.v)
        def allowed(topic, retain=False, qos=0):
            return api.allowed('POST', PUBLISH_PATH, dict(topic=topic, payload='{}', qos=qos, retain=retain))
        self.assertTrue(allowed(self.v.output_topic))
        self.assertFalse(allowed(self.v.source_topic))
        self.assertFalse(allowed(self.v.output_topic, True))
        self.assertTrue(allowed(self.v.config_topic, True, 1))
        self.assertFalse(allowed('hargassner/fixture/command'))
        self.assertFalse(api.allowed('GET', '/core/api/states', None))


if __name__ == '__main__':
    unittest.main()
