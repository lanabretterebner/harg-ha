"""HA-host owner of the existing verdict entity; never a controller client.

VM reports remain on their original topic. Discovery keeps the same unique id
and device but consumes an independently refreshed HA verdict. No retained VM
report establishes liveness, including after HA/app/broker restarts.
"""
import copy
from datetime import datetime, timezone
import json
import math
import re
import time

from ha_websocket import ContextSubscription, WebSocketError, strict_loads
from house_monitor import CoreApi, ApiError, PUBLISH_PATH, default_token
from ingress_proxy import read_options

DISCOVERY = 'homeassistant/sensor/+/betriebsueberwachung/config'
REPORTS = 'hargassner/+/ha/operation'
LEASE = 90
PERIOD = 15
# Freshness is measured on this app's monotonic clock from the moment a report
# arrives. The producer's `published_at` only has to lie within SKEW of this
# host's clock (two clocks never agree exactly) and must not repeat one of the
# recent ones, so a redelivered or replayed report never counts twice. Order is
# not required: a producer clock may step back.
SKEW = 300
RECENT = 32
# A VM that last reported maintenance (a release restarts the adapter) is given
# this long without reports before it counts as failed.
MAINTENANCE_GRACE = 900
STATES = ('Normal', 'Hinweis', 'Störung', 'Unbekannt')
# Optional third participant (proxy/NOTIFY-CONTRACT.md): the harg-notify
# container's live status, shown as its own check when `notify_watch` is on.
NOTIFY = 'hargassner/+/notify/status'
NOTIFY_CHECK = 'Benachrichtigungsdienst'
# The VM rule notify.offline reports the same silence under its own check
# (`Benachrichtigung`, proxy/monitor_policy.py); one row is enough.
NOTIFY_VM_ROW = 'notify.offline:offline'
NOTIFY_TEXT = ('Der Benachrichtigungsdienst meldet sich nicht. E-Mails und Anrufe '
               'bei Störungen sind nicht sichergestellt. Container harg-notify und Netzwerk prüfen.')


def load_notify_watch(filename='/data/options.json'):
    """Never raises: anything but an explicit true leaves the check off."""
    try:
        return read_options(filename).get('notify_watch', False) is True
    except Exception:
        return False


def iso(wall):
    try:
        return datetime.fromtimestamp(wall, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class Verdict:
    def __init__(self, monotonic=time.monotonic, clock=time.time, notify_watch=False):
        self.monotonic, self.clock = monotonic, clock
        self.started = monotonic()
        # Start of the current grace: app start, or a reconnect that interrupted a
        # VM that was fresh until then (HA Core restart, integration reload).
        self.epoch = self.started
        self.recent_reports, self.recent_notify = [], []
        self.maintenance_at = None  # monotonic time of the last report in maintenance
        self.config_topic = self.output_topic = self.source_topic = None
        self.config = None
        self.report = self.received = None
        self.notify_watch = notify_watch is True
        self.notify_received = self.notify_published = self.notify_stale_since = None

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

    def fresh(self, now=None):
        now = self.monotonic() if now is None else now
        return self.received is not None and 0 <= now - self.received < LEASE

    def reconnected(self):
        """A new subscription: a new liveness epoch, never an old healthy verdict.

        If the VM was fresh until now, the outage is ours (HA restart or reload) and
        the VM gets the same grace as at start; a VM already silent gets none, so a
        real VM outage (which forces reconnects) does not flap."""
        now = self.monotonic()
        if self.fresh(now):
            self.epoch = now
        self.report = self.received = None

    def skewed(self, published):
        """True unless `published_at` lies within SKEW of this host's clock."""
        age = self.clock() - published
        return not finite(age) or not -SKEW <= age < LEASE + SKEW

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
        if self.skewed(report['published_at']) or (
                report['published_at'] in self.recent_reports):
            return False
        self.report, self.received = report, self.monotonic()
        self.recent_reports = (self.recent_reports + [report['published_at']])[-RECENT:]
        # Only the last report counts (DOCS.md): a VM that left maintenance and
        # then falls silent is a failure after the lease, not "Wartung der VM".
        self.maintenance_at = self.received if report.get('maintenance') is True else None
        return True

    def notify_ingest(self, topic, payload, retained=False):
        """Freshness only: a live, non-retained status of this gateway's container."""
        if (not self.notify_watch or retained or self.source_topic is None
                or topic != self.source_topic[:-len('/ha/operation')] + '/notify/status'
                or not isinstance(payload, str) or len(payload.encode()) > 65536):
            return False
        try:
            message = strict_loads(payload)
        except (ValueError, RecursionError):
            return False
        # Contract version 2: {"notify_status": ..., "authorization": ...}. The app
        # holds no notify key, so this is freshness only; the VM verifies the signature.
        if not isinstance(message, dict) or set(message) != {'notify_status', 'authorization'}:
            return False
        status = message['notify_status']
        if (not isinstance(status, dict) or type(status.get('schema')) is not int or status['schema'] != 1
                or not finite(status.get('published_at'))):
            return False
        if self.skewed(status['published_at']) or (
                status['published_at'] in self.recent_notify):
            return False
        self.notify_received, self.notify_published = self.monotonic(), status['published_at']
        self.recent_notify = (self.recent_notify + [status['published_at']])[-RECENT:]
        return True

    def notify_state(self):
        now = self.monotonic()
        if self.notify_received is not None:
            return 'ok' if 0 <= now - self.notify_received < LEASE else 'stale'
        return 'unknown' if now - self.started < LEASE else 'stale'

    def with_notify(self, result):
        """Add the container check; it raises Normal to Hinweis, never lowers."""
        if not self.notify_watch:
            return result
        state = self.notify_state()
        age = None if self.notify_received is None else round(self.monotonic() - self.notify_received)
        result.setdefault('ha_watchdog', {})['notify'] = {'state': state, 'age_seconds': age}
        level = {'ok': 'Normal', 'unknown': 'Unbekannt', 'stale': 'Hinweis'}[state]
        if isinstance(result.get('checks'), dict):
            result['checks'][NOTIFY_CHECK] = level
        if state != 'stale':
            self.notify_stale_since = None
            return result
        if self.notify_stale_since is None:
            self.notify_stale_since = self.clock()
        problems = result.get('problems') if isinstance(result.get('problems'), list) else []
        if not any(isinstance(row, dict) and (row.get('check') == NOTIFY_CHECK or row.get('id') == NOTIFY_VM_ROW)
                   for row in problems):
            # The VM rule notify.offline may already report it; one row is enough.
            problems.append({'check': NOTIFY_CHECK, 'level': 'Hinweis', 'text': NOTIFY_TEXT,
                             'since': iso(self.notify_stale_since)})
        result['problems'] = problems
        if result.get('state') == 'Normal':
            result['state'] = 'Hinweis'
            result['summary'] = NOTIFY_TEXT
        return result

    def document(self, heartbeat=None):
        return self.with_notify(self.vm_document(heartbeat))

    def vm_document(self, heartbeat=None):
        now = self.monotonic()
        age = None if self.received is None else now - self.received
        fresh = self.fresh(now)
        # The heartbeat monitor answers `unknown` until its first poll; within the
        # same start-up grace as the VM report that is not a failure (otherwise every
        # app start or update published "VM nicht erreichbar").
        state = None if heartbeat is None else heartbeat.get('status')
        heartbeat_failed = (heartbeat is not None and state not in ('healthy', 'maintenance')
                            and not (state == 'unknown' and now - self.started < LEASE))
        if fresh and not heartbeat_failed:
            result = copy.deepcopy(self.report)
            # The VM's signature covers its own verdict, not this copy (NOTIFY-CONTRACT.md).
            result.pop('authorization', None)
            result['ha_watchdog'] = {'source': 'live_vm_report', 'age_seconds': round(age)}
            return result
        maintenance = self.maintenance_at is not None and 0 <= now - self.maintenance_at < MAINTENANCE_GRACE
        failed = not maintenance and (heartbeat_failed or (self.received is not None and not fresh)
                                      or now - self.epoch >= LEASE)
        text = ('Die VM ist nicht erreichbar oder sendet keine aktuellen Betriebsdaten, deshalb ist der '
                'Zustand der Heizung unbekannt. Prüfen, ob die VM läuft und im Netzwerk erreichbar ist.' if failed
                else 'Die VM wird gerade gewartet und sendet nach dem Neustart wieder Betriebsdaten. Bis dahin '
                'ist der Zustand der Heizung unbekannt.' if maintenance
                else 'Die App wartet auf aktuelle Betriebsdaten der VM, bis dahin ist der Zustand der Heizung '
                'unbekannt.')
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
                 monotonic=time.monotonic, clock=time.time, notify_watch=False):
        self.heartbeat, self.token_source = heartbeat, token_source
        self.subscription_factory, self.api_factory = subscription_factory, api_factory
        self.verdict = Verdict(monotonic, clock, notify_watch)

    def run(self, stop):
        # Reconnect uses a new liveness epoch, never an old healthy verdict.
        while not stop.is_set():
            sockets = []
            try:
                token = self.token_source()
                api = self.api_factory(token, self.verdict)
                self.verdict.reconnected()
                for topic in (DISCOVERY, REPORTS) + ((NOTIFY,) if self.verdict.notify_watch else ()):
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
                            elif index == 1:
                                dirty = self.verdict.ingest(topic, payload, retained) or dirty
                            else:
                                self.verdict.notify_ingest(topic, payload, retained)
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
