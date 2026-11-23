import json
import unittest
import threading

import os
import tempfile

from operation_watchdog import (Verdict, WatchdogApi, OperationWatchdog, LEASE, DISCOVERY, NOTIFY,
                                NOTIFY_CHECK, NOTIFY_VM_ROW, load_notify_watch, SKEW, MAINTENANCE_GRACE)
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

    def test_a_crash_after_maintenance_ended_is_a_failure(self):
        # Audit 2026-11-18: only the last report decides the maintenance grace.
        self.advance(3600)
        payload = dict(state='Normal', summary='synthetic', problems=[], checks={}, published_at=self.wall, maintenance=True)
        self.assertTrue(self.v.ingest(self.config['state_topic'], json.dumps(payload)))
        for _ in range(5):
            self.advance(60)
            self.assertTrue(self.report())
        self.advance(LEASE + 30)
        self.assertEqual(self.v.document()['state'], 'Störung')

    def test_vm_crash_cannot_leave_normal(self):
        self.assertTrue(self.report())
        self.assertEqual(self.v.document()['state'], 'Normal')
        self.advance(LEASE)
        self.assertEqual(self.v.document()['state'], 'Störung')
        self.assertIn('Zustand der Heizung unbekannt', self.v.document()['summary'])

    def test_vm_outage_text_says_what_to_do(self):
        """Alert text audit 2026-11-19 (unreleased): the outage Störung names the next step."""
        self.assertTrue(self.report())
        self.advance(LEASE)
        document = self.v.document()
        self.assertEqual(document['summary'], 'Die VM ist nicht erreichbar oder sendet keine aktuellen '
                                              'Betriebsdaten, deshalb ist der Zustand der Heizung unbekannt. '
                                              'Prüfen, ob die VM läuft und im Netzwerk erreichbar ist.')
        self.assertEqual(document['problems'][0]['text'], document['summary'])

    def test_retained_normal_never_proves_liveness_even_after_restart(self):
        self.assertFalse(self.report(retained=True))
        self.assertEqual(self.v.document()['state'], 'Unbekannt')
        self.advance(LEASE)
        self.assertFalse(self.report(retained=True))
        self.assertEqual(self.v.document()['state'], 'Störung')

    def test_heartbeat_failure_overrides_even_recent_normal(self):
        self.report()
        self.assertEqual(self.v.document({'status': 'fault'})['state'], 'Störung')
        self.assertEqual(self.v.document({'status': 'healthy'})['state'], 'Normal')
        self.advance(LEASE)
        self.assertEqual(self.v.document({'status': 'healthy'})['state'], 'Störung')

    def test_heartbeat_unknown_at_start_is_not_a_failure(self):
        """Project review: every app start published "VM nicht erreichbar" before the first poll."""
        self.assertEqual(self.v.document({'status': 'unknown'})['state'], 'Unbekannt')  # no report yet: waiting
        self.report()
        self.assertEqual(self.v.document({'status': 'unknown'})['state'], 'Normal')
        self.advance(LEASE - 1)
        self.report()
        self.assertEqual(self.v.document({'status': 'unknown'})['state'], 'Normal')
        self.advance(1)  # the grace is over: a heartbeat still unknown is a failure
        self.report()
        document = self.v.document({'status': 'unknown'})
        self.assertEqual(document['state'], 'Störung')
        self.assertEqual(document['ha_watchdog']['reason'], 'heartbeat_failed')

    def test_recovery_requires_fresh_live_report(self):
        self.report(); self.advance(100)
        self.assertEqual(self.v.document()['state'], 'Störung')
        self.assertFalse(self.report(retained=True))
        self.assertEqual(self.v.document()['state'], 'Störung')
        self.report('Hinweis')
        self.assertEqual(self.v.document()['state'], 'Hinweis')

    def test_stale_future_malformed_and_foreign_reports_refused(self):
        for at in (self.wall - LEASE - SKEW, self.wall + SKEW + 1, float('inf'), None, True):
            raw = json.dumps(dict(state='Normal', summary='', checks={}, problems=[], published_at=at))
            self.assertFalse(self.v.ingest(self.config['state_topic'], raw))
        self.assertFalse(self.v.ingest('hargassner/other/ha/operation', '{}'))
        self.assertFalse(self.v.ingest(self.config['state_topic'], '{bad'))
        self.assertEqual(self.v.document()['state'], 'Unbekannt')

    def test_clock_step_never_extends_old_healthy_report(self):
        self.report(); self.wall -= 100
        self.assertEqual(self.v.document()['state'], 'Normal')  # freshness is this app's monotonic clock
        self.advance(LEASE)
        self.assertEqual(self.v.document()['state'], 'Störung')
        # The same report again (redelivery or replay) never makes it live again.
        self.wall -= LEASE
        payload = json.dumps(dict(state='Normal', summary='synthetic', problems=[], checks={}, published_at=self.wall))
        self.advance(0)
        self.assertTrue(self.v.ingest(self.config['state_topic'], payload))
        self.advance(LEASE)
        self.assertFalse(self.v.ingest(self.config['state_topic'], payload))
        self.assertEqual(self.v.document()['state'], 'Störung')

    def test_vm_clock_steps_back_or_one_future_report(self):
        """Third review: a VM clock step back of more than 90 s, or one report up to
        300 s in the future, then made every following report look old."""
        self.report()
        self.wall -= 200          # this host's view: the VM's clock is now 200 s behind
        for _ in range(5):
            self.advance(30)
            self.assertTrue(self.report())
            self.assertEqual(self.v.document()['state'], 'Normal')
        future = dict(state='Normal', summary='synthetic', problems=[], checks={}, published_at=self.wall + 290)
        self.assertTrue(self.v.ingest(self.config['state_topic'], json.dumps(future)))
        for _ in range(5):
            self.advance(30)
            self.assertTrue(self.report())
            self.assertEqual(self.v.document()['state'], 'Normal')

    def test_the_vm_signature_is_not_copied(self):
        payload = dict(state='Normal', summary='synthetic', problems=[], checks={}, published_at=self.wall,
                       authorization={'version': 2, 'issued_at_ms': int(self.wall * 1000), 'mac': '0' * 64})
        self.assertTrue(self.v.ingest(self.config['state_topic'], json.dumps(payload)))
        self.assertNotIn('authorization', self.v.document())

    def test_vm_clock_ahead_is_tolerated(self):
        """HA-app review: a VM clock 6 s ahead gave a permanent Störung."""
        for offset in (6, 120):
            payload = dict(state='Normal', summary='synthetic', problems=[], checks={}, published_at=self.wall + offset)
            self.assertTrue(self.v.ingest(self.config['state_topic'], json.dumps(payload)))
            self.assertEqual(self.v.document()['state'], 'Normal')
            self.advance(30)

    def test_reconnect_after_a_fresh_vm_gets_the_start_grace(self):
        """HA-app review: every HA restart or reconnect gave a Störung."""
        self.advance(3600)
        self.report()
        self.v.reconnected()  # HA Core restart: the subscription is new
        self.assertEqual(self.v.document()['state'], 'Unbekannt')
        self.advance(LEASE - 1)
        self.assertEqual(self.v.document()['state'], 'Unbekannt')
        self.report()
        self.assertEqual(self.v.document()['state'], 'Normal')

    def test_a_real_vm_outage_does_not_flap_through_reconnects(self):
        self.advance(3600)
        self.report()
        self.advance(LEASE)
        self.assertEqual(self.v.document()['state'], 'Störung')
        for _ in range(3):  # the stale subscription is reopened every 180 s
            self.advance(180)
            self.v.reconnected()
            self.assertEqual(self.v.document()['state'], 'Störung')

    def test_maintenance_release_restart_is_not_a_failure(self):
        """HA-app review: a normal VM release restart gave a Störung."""
        self.advance(3600)
        payload = dict(state='Normal', summary='synthetic', problems=[], checks={}, maintenance=True, published_at=self.wall)
        self.assertTrue(self.v.ingest(self.config['state_topic'], json.dumps(payload)))
        self.advance(LEASE + 60)  # the adapter restarts
        document = self.v.document()
        self.assertEqual(document['state'], 'Unbekannt')
        self.assertIn('gewartet', document['summary'])
        self.v.reconnected()
        self.assertEqual(self.v.document()['state'], 'Unbekannt')
        self.advance(MAINTENANCE_GRACE)  # far too long: now it is a failure
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


class NotifyWatchTests(unittest.TestCase):
    """harg-notify status freshness as the check Benachrichtigungsdienst."""

    def setUp(self):
        self.tick, self.wall = 0., 1000.
        self.config = dict(name='Betriebsüberwachung', unique_id='hargassner_fixture_unit_sensor_betriebsueberwachung',
                           default_entity_id='sensor.betriebsueberwachung', device_class='enum',
                           options=['Normal', 'Hinweis', 'Störung', 'Unbekannt'], device={'identifiers': ['fixture']},
                           state_topic='hargassner/fixture/ha/operation', json_attributes_topic='hargassner/fixture/ha/operation',
                           value_template='{{ value_json.state }}', expire_after=300)
        self.v = self.verdict(True)

    def verdict(self, watch):
        v = Verdict(lambda: self.tick, lambda: self.wall, watch)
        self.assertTrue(v.discovery('homeassistant/sensor/fixture/betriebsueberwachung/config', json.dumps(self.config)))
        return v

    def advance(self, seconds):
        self.tick += seconds
        self.wall += seconds

    def report(self, v, state='Normal', problems=()):
        payload = dict(state=state, summary='Alles in Ordnung', problems=list(problems),
                       checks={'Verbindung': 'Normal'}, published_at=self.wall)
        self.assertTrue(v.ingest(self.config['state_topic'], json.dumps(payload)))

    def status(self, v, retained=False, topic='hargassner/fixture/notify/status', **changes):
        payload = dict(schema=1, version='1.0.0', published_at=self.wall, policy_revision=None, policy_hash=None,
                       policy_error=None, channels={}, peers={}, deliveries=[])
        payload.update(changes)
        envelope = {'notify_status': payload,
                    'authorization': {'version': 2, 'issued_at_ms': int(self.wall * 1000), 'mac': '0' * 64}}
        return v.notify_ingest(topic, json.dumps(envelope), retained)

    def test_off_by_default_changes_nothing(self):
        v = self.verdict(False)
        self.report(v)
        self.assertFalse(self.status(v))
        self.advance(LEASE + 10)
        self.report(v)
        document = v.document()
        self.assertEqual(document['state'], 'Normal')
        self.assertNotIn(NOTIFY_CHECK, document['checks'])
        self.assertNotIn('notify', document['ha_watchdog'])

    def test_unknown_then_fresh(self):
        self.report(self.v)
        document = self.v.document()
        self.assertEqual(document['state'], 'Normal')
        self.assertEqual(document['checks'][NOTIFY_CHECK], 'Unbekannt')
        self.assertEqual(document['ha_watchdog']['notify'], {'state': 'unknown', 'age_seconds': None})
        self.assertTrue(self.status(self.v))
        document = self.v.document()
        self.assertEqual(document['checks'][NOTIFY_CHECK], 'Normal')
        self.assertEqual(document['ha_watchdog']['notify']['state'], 'ok')
        self.assertEqual(document['problems'], [])

    def test_never_seen_becomes_hinweis_after_lease(self):
        self.advance(LEASE)
        self.report(self.v)
        document = self.v.document()
        self.assertEqual(document['state'], 'Hinweis')
        self.assertEqual(document['checks'][NOTIFY_CHECK], 'Hinweis')
        row = [r for r in document['problems'] if r['check'] == NOTIFY_CHECK]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]['level'], 'Hinweis')
        # Alert text audit 2026-11-19 (unreleased): what is affected and what to check.
        self.assertEqual(row[0]['text'], 'Der Benachrichtigungsdienst meldet sich nicht. E-Mails und Anrufe bei '
                                         'Störungen sind nicht sichergestellt. Container harg-notify und '
                                         'Netzwerk prüfen.')
        self.assertRegex(row[0]['since'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(document['summary'], row[0]['text'])

    def test_the_vm_notify_offline_row_is_not_duplicated(self):
        # Audit 2026-11-18: the VM rule notify.offline reports the same silence
        # under its own check; the app adds no second row.
        self.advance(LEASE)
        row = {'check': 'Benachrichtigung', 'level': 'Hinweis', 'id': NOTIFY_VM_ROW, 'rule': 'notify.offline',
               'text': 'Der Benachrichtigungsdienst (E-Mail und Anruf) meldet sich nicht mehr. Container und Netzwerk prüfen.'}
        self.report(self.v, 'Hinweis', [row])
        document = self.v.document()
        self.assertEqual([r['check'] for r in document['problems']], ['Benachrichtigung'])
        self.assertEqual(document['checks'][NOTIFY_CHECK], 'Hinweis')

    def test_stale_after_lease_and_recovery(self):
        self.assertTrue(self.status(self.v))
        self.advance(LEASE - 1)
        self.report(self.v)
        self.assertEqual(self.v.document()['state'], 'Normal')
        self.advance(1)
        self.report(self.v)
        first = self.v.document()
        self.assertEqual(first['state'], 'Hinweis')
        self.advance(30)
        self.report(self.v)
        self.assertEqual(self.v.document()['problems'][0]['since'], first['problems'][0]['since'])
        self.assertTrue(self.status(self.v))
        self.assertEqual(self.v.document()['state'], 'Normal')

    def test_never_lowers_and_keeps_vm_row(self):
        self.advance(LEASE)
        row = {'check': NOTIFY_CHECK, 'level': 'Hinweis', 'text': 'VM sagt', 'since': '2026-11-17T10:00:00Z'}
        self.report(self.v, 'Störung', [row, {'check': 'Kessel', 'level': 'Störung', 'text': 'x'}])
        document = self.v.document()
        self.assertEqual(document['state'], 'Störung')
        self.assertEqual(document['summary'], 'Alles in Ordnung')
        self.assertEqual([r['text'] for r in document['problems'] if r['check'] == NOTIFY_CHECK], ['VM sagt'])

    def test_added_to_vm_outage_document(self):
        self.advance(LEASE)
        document = self.v.document()
        self.assertEqual(document['state'], 'Störung')
        self.assertEqual(document['checks'][NOTIFY_CHECK], 'Hinweis')
        self.assertIn(NOTIFY_CHECK, [r.get('check') for r in document['problems']])

    def test_retained_foreign_stale_or_malformed_status_refused(self):
        self.assertFalse(self.status(self.v, retained=True))
        self.assertFalse(self.status(self.v, topic='hargassner/other/notify/status'))
        self.assertFalse(self.status(self.v, published_at=self.wall - LEASE - SKEW))
        self.assertFalse(self.status(self.v, published_at=self.wall + SKEW + 1))
        self.assertFalse(self.status(self.v, schema=2))
        self.assertFalse(self.v.notify_ingest('hargassner/fixture/notify/status', '{"schema": 1, "schema": 1}'))
        # The unwrapped version-1 status is no longer a container status.
        self.assertFalse(self.v.notify_ingest('hargassner/fixture/notify/status',
                                              json.dumps({'schema': 1, 'published_at': self.wall})))
        self.assertFalse(self.v.notify_ingest('hargassner/fixture/notify/status', '{"schema": 1, "published_at": NaN}'))
        self.assertEqual(self.v.notify_state(), 'unknown')
        undiscovered = Verdict(lambda: self.tick, lambda: self.wall, True)
        self.assertFalse(self.status(undiscovered))

    def test_report_without_published_check_keeps_document_valid(self):
        self.report(self.v)
        self.advance(LEASE)
        self.report(self.v)
        document = self.v.document()
        json.dumps(document, allow_nan=False)
        self.assertEqual(self.v.document()['problems'][-1]['check'], NOTIFY_CHECK)

    def test_worker_subscribes_status_only_when_enabled(self):
        for watch, expected in ((False, [DISCOVERY, 'hargassner/+/ha/operation']),
                                (True, [DISCOVERY, 'hargassner/+/ha/operation', NOTIFY])):
            stop = threading.Event()
            topics = []

            class Sub:
                def __init__(self, token, topic):
                    topics.append(topic)

                def open(self):
                    pass

                def close(self):
                    stop.set()

                def poll(self, timeout):
                    raise ConnectionError('fixture')

            class Api:
                def __init__(self, token, verdict):
                    pass
            OperationWatchdog(token_source=lambda: 'synthetic', subscription_factory=Sub, api_factory=Api,
                              monotonic=lambda: 0., clock=lambda: 1000., notify_watch=watch).run(stop)
            self.assertEqual(topics, expected)

    def test_option_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'options.json')
            for raw, expected in (('{"notify_watch": true}', True), ('{"notify_watch": false}', False),
                                  ('{}', False), ('{"notify_watch": "true"}', False),
                                  ('{"notify_watch": true, "notify_watch": true}', False), ('{', False)):
                with open(path, 'w') as handle:
                    handle.write(raw)
                self.assertIs(load_notify_watch(path), expected, raw)
            self.assertFalse(load_notify_watch(os.path.join(directory, 'missing.json')))


if __name__ == '__main__':
    unittest.main()
