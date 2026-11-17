"""HA-host owner of the existing verdict entity; never a controller client.

VM reports remain on their original topic. Discovery keeps the same unique id
and device but consumes an independently refreshed HA verdict. No retained VM
report establishes liveness, including after HA/app/broker restarts.
"""
import copy
import json
import math
import re
import time

from ha_websocket import ContextSubscription, WebSocketError, strict_loads
from house_monitor import CoreApi, ApiError, PUBLISH_PATH, default_token

DISCOVERY = 'homeassistant/sensor/+/betriebsueberwachung/config'
REPORTS = 'hargassner/+/ha/operation'
LEASE = 90
PERIOD = 15
STATES = ('Normal', 'Hinweis', 'Störung', 'Unbekannt')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class Verdict:
    def __init__(self, monotonic=time.monotonic, clock=time.time):
        self.monotonic, self.clock = monotonic, clock
        self.started = monotonic()
        self.config_topic = self.output_topic = self.source_topic = None
        self.config = None
        self.report = self.received = None

    def discovery(self, topic, payload):
        if not isinstance(topic, str):
            return False
        match = re.fullmatch(r'homeassistant/sensor/([A-Za-z0-9_-]{1,80})/betriebsueberwachung/config', topic)
        if not match or not isinstance(payload, str) or len(payload.encode()) > 32768:
            return False
        try:
            config = strict_loads(payload)
        except (ValueError, RecursionError):
            return False
        gateway = match.group(1)
        source = 'hargassner/' + gateway + '/ha/operation'
        if (not isinstance(config, dict) or config.get('state_topic') not in (source, source + '/checked')
                or config.get('json_attributes_topic') != config.get('state_topic')
                or not isinstance(config.get('unique_id'), str)
                or not re.fullmatch(r'hargassner_' + re.escape(gateway) + r'_[A-Za-z0-9_-]{1,80}_sensor_betriebsueberwachung', config['unique_id'])
                or config.get('default_entity_id') != 'sensor.betriebsueberwachung'
                or config.get('value_template') != '{{ value_json.state }}'
                or config.get('device_class') != 'enum'
                or not isinstance(config.get('options'), list)
                or not all(isinstance(x, str) for x in config['options'])
                or set(config['options']) != set(STATES)
                or (self.config_topic is not None and self.config_topic != topic)):
            return False
        self.config_topic, self.source_topic, self.output_topic = topic, source, source + '/checked'
        checked = copy.deepcopy(config)
        checked.update(state_topic=self.output_topic, json_attributes_topic=self.output_topic, expire_after=LEASE)
        changed = checked != self.config
        self.config = checked
        # A VM announcement can restore the old subscription, so reapply the
        # checked configuration even when our desired config itself is unchanged.
        return changed or config != checked

    def ingest(self, topic, payload, retained=False):
        if retained or topic != self.source_topic or not isinstance(payload, str) or len(payload.encode()) > 32768:
            return False
        try:
            report = strict_loads(payload)
        except (ValueError, RecursionError):
            return False
        if (not isinstance(report, dict) or report.get('state') not in STATES
                or not finite(report.get('published_at'))
                or not isinstance(report.get('summary'), str)
                or not isinstance(report.get('problems'), list)
                or not isinstance(report.get('checks'), (list, dict))):
            return False
        age = self.clock() - report['published_at']
        if not finite(age) or not -5 <= age < LEASE:
            return False
        self.report, self.received = report, self.monotonic()
        return True

    def document(self, heartbeat=None):
        now = self.monotonic()
        age = None if self.received is None else now - self.received
        wall_age = None if self.report is None else self.clock() - self.report['published_at']
        fresh = (age is not None and 0 <= age < LEASE and finite(wall_age) and -5 <= wall_age < LEASE)
        heartbeat_failed = heartbeat is not None and heartbeat.get('status') not in ('healthy', 'maintenance')
        if fresh and not heartbeat_failed:
            result = copy.deepcopy(self.report)
            result['ha_watchdog'] = {'source': 'live_vm_report', 'age_seconds': round(age)}
            return result
        failed = heartbeat_failed or (self.received is not None and not fresh) or now - self.started >= LEASE
        text = ('VM nicht erreichbar oder keine aktuellen Betriebsdaten. Heizungszustand unbekannt.' if failed
                else 'Warte auf aktuelle Betriebsdaten der VM. Heizungszustand unbekannt.')
        return {'state': 'Störung' if failed else 'Unbekannt', 'summary': text,
                'problems': [{'code': 'vm_monitor_unavailable', 'level': 'Störung' if failed else 'Unbekannt', 'text': text}],
                'checks': {}, 'maintenance': False,
                'house_public': {'aktiv': False, 'Modus': None, 'Zustand': 'Unbekannt', 'Sensoren': 0, 'nutzbar': 0},
                'ha_watchdog': {'source': 'ha_app', 'reason': 'heartbeat_failed' if heartbeat_failed else 'vm_report_missing'},
                'published_at': self.clock()}


class WatchdogApi(CoreApi):
    def __init__(self, token, verdict, **kwargs):
        super().__init__(token, **kwargs)
        self.verdict = verdict

    def allowed(self, method, path, body):
        if method != 'POST' or path != PUBLISH_PATH or not isinstance(body, dict):
            return False
        if set(body) != {'topic', 'payload', 'qos', 'retain'} or not isinstance(body['payload'], str) or len(body['payload'].encode()) > 32768:
            return False
        return ((body['topic'] == self.verdict.config_topic and self.verdict.config_topic is not None
                 and body['retain'] is True and type(body['qos']) is int and body['qos'] == 1)
                or (body['topic'] == self.verdict.output_topic and self.verdict.output_topic is not None
                    and body['retain'] is False and type(body['qos']) is int and body['qos'] == 0))

    def send(self, topic, value, discovery=False):
        status, _ = self.request('POST', PUBLISH_PATH, {'topic': topic, 'payload': json.dumps(value, ensure_ascii=False, allow_nan=False),
                                'retain': discovery, 'qos': 1 if discovery else 0})
        if not 200 <= status < 300:
            raise ApiError('status')


class OperationWatchdog:
    def __init__(self, heartbeat=lambda: None, token_source=default_token,
                 subscription_factory=ContextSubscription, api_factory=WatchdogApi,
                 monotonic=time.monotonic, clock=time.time):
        self.heartbeat, self.token_source = heartbeat, token_source
        self.subscription_factory, self.api_factory = subscription_factory, api_factory
        self.verdict = Verdict(monotonic, clock)

    def run(self, stop):
        # Reconnect uses a new liveness epoch, never an old healthy verdict.
        while not stop.is_set():
            sockets = []
            try:
                token = self.token_source()
                api = self.api_factory(token, self.verdict)
                self.verdict.report = self.verdict.received = None
                for topic in (DISCOVERY, REPORTS):
                    sub = self.subscription_factory(token, topic=topic)
                    sockets.append(sub)
                    sub.open()
                opened = self.verdict.monotonic()
                next_send = 0
                last_verdict = None
                while not stop.is_set():
                    dirty = False
                    for index, sub in enumerate(sockets):
                        for topic, payload, retained in sub.poll(0.5):
                            if index == 0:
                                if self.verdict.discovery(topic, payload):
                                    api.send(self.verdict.output_topic, self.verdict.document(self.heartbeat()))
                                    api.send(self.verdict.config_topic, self.verdict.config, True)
                                    dirty = True
                            else:
                                dirty = self.verdict.ingest(topic, payload, retained) or dirty
                    now = self.verdict.monotonic()
                    document = self.verdict.document(self.heartbeat())
                    signature = (document['state'], document['summary'])
                    if self.verdict.config is not None and (dirty or now >= next_send or signature != last_verdict):
                        api.send(self.verdict.output_topic, document)
                        last_verdict = signature
                        next_send = now + PERIOD
                    # mqtt subscriptions may disappear on an integration reload.
                    last_live = self.verdict.received if self.verdict.received is not None else opened
                    if now - last_live >= 180:
                        raise WebSocketError('stale_subscription')
            except Exception:
                # No raw exception, topic, identifier, payload or credential logs.
                print('Operation watchdog: unavailable.', flush=True)
            finally:
                for sub in sockets:
                    sub.close()
            stop.wait(5)
