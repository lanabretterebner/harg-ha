"""House-sensor worker: reads selected sensors, receives the VM context, reports.

Room sensors are monitoring only. The app reads the configured sensor states
through Home Assistant Core, receives the adapter context through one Core
WebSocket `mqtt/subscribe` and sends codes and numbers through Core's
`mqtt.publish`. It has no broker credentials, no controller access and no
control-capable call. The allowlist in CoreApi.allowed is the complete set of
requests of the worker; the room page adds the requests in room_picker.py.
The rules' state is saved in /data (house_state.py) at most every 5 minutes
and on stop, and a restart continues from it as after an HA outage of the same
length. A new sensor selection restarts only the rooms and circuits it changes.
"""
from datetime import datetime, timezone
import html
import http.client
import json
import re
import secrets
import socket
import threading
import time

import house_rules as rules
import house_state
from ha_websocket import Backoff, ContextSubscription, WebSocketError
from ingress_proxy import read_options

SUPERVISOR_IP = '172.30.32.2'
REQUEST_SOCKET_TIMEOUT_S = 4
REQUEST_DEADLINE_S = 5
STATE_CAP = 65536
STATES_CAP = 8 * 1024 * 1024
REPORT_PERIOD_S = 120
REPORT_MIN_SPACING_S = 10
CONTEXT_RESUBSCRIBE_S = 180
LABEL_REFRESH_S = 600
CANDIDATE_CACHE_S = 600
CANDIDATE_ROWS = 1000
LOG_INTERVAL_S = 300
BASE_TOPIC = re.compile(r'(hargassner/[A-Za-z0-9_-]{1,80})/house/context')
CONFIG_PATH = '/core/api/config'
STATES_PATH = '/core/api/states'
PUBLISH_PATH = '/core/api/services/mqtt/publish'
LOG_CATEGORIES = frozenset({
    'config_invalid', 'ha_api_unavailable', 'websocket_unavailable', 'publish_failed',
    'context_version_unsupported', 'report_not_accepted', 'internal', 'ready',
    'state_restored', 'state_partly_restored', 'state_unreadable', 'state_incompatible', 'state_clock',
    'state_too_old', 'state_save_failed'})


class ApiError(Exception):
    """Fixed category only."""


def load_house_options(filename='/data/options.json'):
    """Never raises: an unreadable or invalid file gives an invalid configuration."""
    try:
        return rules.parse_house_options(read_options(filename))
    except Exception:
        return rules.HouseOptions(False, raw_count=1)


class CoreApi:
    """The only HTTP requests of the house monitor (spec 3)."""

    def __init__(self, token, entities=(), *, host=SUPERVISOR_IP, port=80, monotonic=time.monotonic,
                 connection_factory=http.client.HTTPConnection):
        self.token, self.entities = token, frozenset(entities)
        self.host, self.port, self.monotonic = host, port, monotonic
        self.connection_factory = connection_factory

    def allowed(self, method, path, body):
        if method == 'GET' and body is None:
            if path in (CONFIG_PATH, STATES_PATH):
                return True
            prefix = STATES_PATH + '/'
            return path.startswith(prefix) and path[len(prefix):] in self.entities
        if method == 'POST' and path == PUBLISH_PATH and isinstance(body, dict):
            topic = body.get('topic')
            return (set(body) == {'topic', 'payload', 'qos', 'retain'} and isinstance(topic, str)
                    and re.fullmatch(r'hargassner/[A-Za-z0-9_-]{1,80}/house/report', topic) is not None
                    and isinstance(body['payload'], str) and body['qos'] == 0 and body['retain'] is False
                    and len(body['payload'].encode('utf-8')) <= rules.MAX_REPORT_BYTES)
        return False

    def request(self, method, path, body=None, cap=STATE_CAP):
        if not self.allowed(method, path, body):
            raise ApiError('not_allowed')
        deadline = self.monotonic() + REQUEST_DEADLINE_S
        connection = None
        expired = threading.Event()

        def abort():
            # The wall deadline covers headers and slowly sent bodies: shutting
            # the socket down ends any blocking read in this request.
            expired.set()
            sock = getattr(connection, 'sock', None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        watchdog = threading.Timer(REQUEST_DEADLINE_S, abort)
        watchdog.daemon = True
        try:
            connection = self.connection_factory(self.host, self.port, timeout=REQUEST_SOCKET_TIMEOUT_S)
            watchdog.start()
            data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json',
                       'Accept-Encoding': 'identity', 'Connection': 'close'}
            if data is not None:
                headers['Content-Type'] = 'application/json'
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ApiError('response')
            length = response.getheader('Content-Length')
            if length is not None and (not re.fullmatch(r'[0-9]{1,10}', length) or int(length) > cap):
                raise ApiError('too_large')
            chunks, size = [], 0
            while True:
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise ApiError('deadline')
                if connection.sock is not None:
                    connection.sock.settimeout(min(REQUEST_SOCKET_TIMEOUT_S, remaining))
                chunk = response.read1(min(65536, cap + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > cap:
                    raise ApiError('too_large')
            if response.length not in (None, 0) or expired.is_set():
                raise ApiError('incomplete')
            return response.status, b''.join(chunks)
        except ApiError:
            raise
        except Exception:
            raise ApiError('deadline' if expired.is_set() else 'unavailable') from None
        finally:
            watchdog.cancel()
            if connection is not None:
                connection.close()

    def _json(self, method, path, body=None, cap=STATE_CAP, accept=(200,)):
        status, raw = self.request(method, path, body, cap)
        if status == 404 and 404 in accept:
            return 404, None
        if status not in accept:
            raise ApiError('status')
        try:
            return status, rules.strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ApiError('json') from None

    def config(self):
        _status, document = self._json('GET', CONFIG_PATH)
        if not isinstance(document, dict):
            raise ApiError('json')
        return document

    def state(self, entity):
        status, document = self._json('GET', STATES_PATH + '/' + entity, accept=(200, 404))
        if status == 404:
            return 'missing'
        if not isinstance(document, dict):
            raise ApiError('json')
        return document

    def states(self):
        _status, document = self._json('GET', STATES_PATH, cap=STATES_CAP)
        if not isinstance(document, list):
            raise ApiError('json')
        return document

    def publish(self, topic, payload):
        status, _raw = self.request('POST', PUBLISH_PATH, {'topic': topic, 'payload': payload, 'qos': 0, 'retain': False})
        if not 200 <= status < 300:
            raise ApiError('status')


def default_token():
    from app_monitor import supervisor_token
    return supervisor_token()


def local_minutes(wall, zone_name, cache):
    if not isinstance(zone_name, str) or not 1 <= len(zone_name) <= 64:
        return None
    try:
        if zone_name not in cache:
            from zoneinfo import ZoneInfo
            cache.clear()
            cache[zone_name] = ZoneInfo(zone_name)
        moment = datetime.fromtimestamp(wall, cache[zone_name])
        return moment.hour * 60 + moment.minute
    except Exception:
        return None


class HouseMonitor:
    """One worker thread; never exits before stop (a dead worker ends the app)."""

    def __init__(self, options, *, token_source=default_token, api_factory=CoreApi,
                 subscription_factory=ContextSubscription, clock=time.time, monotonic=time.monotonic,
                 instance=None, log=None, state_path=None, boot_id=house_state.boot_id,
                 uptime=house_state.uptime):
        self.options = options
        self.token_source, self.api_factory = token_source, api_factory
        self.subscription_factory = subscription_factory
        self.clock, self.monotonic = clock, monotonic
        self.instance = instance or secrets.token_hex(16)
        self.log = log or (lambda line: print(line, flush=True))
        self.logged = {}
        # Without a path (tests, tools) nothing is read or written.
        self.state_file = house_state.StateFile(state_path) if state_path else None
        self.boot = boot_id() if self.state_file is not None else None
        self.uptime = uptime
        self.next_save = monotonic() + house_state.SAVE_PERIOD_S
        self.engine = self._start_engine(options)
        self.started = monotonic()
        self.lock = threading.Lock()
        self.token = None
        self.api = None
        self.socket = None
        self.backoff = Backoff()
        self.socket_retry = 0.0
        self.socket_opened = None
        self.socket_state = 'starting'
        self.base = None
        self.context = None
        self.context_mono = None
        self.unsupported_mono = None
        self.counters = {'foreign_base': 0, 'context_invalid': 0, 'context_unsupported': 0}
        self.next_sample = monotonic()
        self.next_labels = monotonic()
        self.sampled = False
        self.zone = None
        self.zones = {}
        self.last_report = None
        self.last_report_wall = None
        self.last_identities = None
        self.seq = 0
        self.publish_state = None
        self.ack = None
        self.ack_wall = None
        self.findings = []
        self.candidates = None
        self.candidate_lock = threading.Lock()
        self.pending = None
        if not options.valid:
            self._log('config_invalid')

    # -- logging: fixed categories, one line per category per 5 min
    def _log(self, category):
        if category not in LOG_CATEGORIES:
            category = 'internal'
        now = self.monotonic()
        last = self.logged.get(category)
        if last is not None and now - last < LOG_INTERVAL_S:
            return
        self.logged[category] = now
        self.log(f'House monitor: {category}.')

    # -- saved state (house_state.py)
    def _start_engine(self, options):
        """The engine at start: continued from the saved state when it fits."""
        if not options.valid:
            return None
        if self.state_file is None:
            return rules.HouseEngine(options)
        try:
            document, problem = self.state_file.load()
            if document is None:
                if problem is not None:
                    self._log(problem)
                return rules.HouseEngine(options)
            engine, outcome = house_state.restore(options, document, mono=self.monotonic(), wall=self.clock(),
                                                  boot=self.boot, since_boot=self.uptime())
            self._log(outcome)
            return engine
        except Exception:
            self._log('state_unreadable')
            return rules.HouseEngine(options)

    def save_state(self, force=False):
        """Write the engine's state, at most once per SAVE_PERIOD_S unless forced."""
        if self.state_file is None:
            return False
        now = self.monotonic()
        if not force and now < self.next_save:
            return False
        self.next_save = now + house_state.SAVE_PERIOD_S
        with self.lock:
            engine = self.engine
            if engine is None:
                return False
            try:
                document = house_state.export(engine, mono=now, wall=self.clock(), boot=self.boot)
            except Exception:
                document = None
        if document is None or not self.state_file.save(document):
            self._log('state_save_failed')
            return False
        return True

    # -- main loop
    def run(self, stop):
        while not stop.is_set():
            try:
                self.cycle(stop)
            except Exception:
                self._log('internal')
                stop.wait(1)
        self._close_socket()
        try:
            self.save_state(force=True)
        except Exception:
            self._log('internal')

    def _ensure_token(self):
        if self.token is None:
            try:
                self.token = self.token_source()
            except Exception:
                self.token = None
        with self.lock:
            if self.token is not None and self.api is None:
                self.api = self.api_factory(self.token, self.options.entities if self.options.valid else ())
        return self.token

    # -- a new selection from the room page, applied by the worker thread only
    def request_reconfigure(self, options):
        """Queue validated options; the worker swaps them in at its next cycle.

        Returns an Event that is set once the worker uses them. A later request
        replaces an earlier one that was not yet applied."""
        done = threading.Event()
        with self.lock:
            self.pending = (options, done)
        return done

    def _apply_pending(self):
        with self.lock:
            pending, self.pending = self.pending, None
            if pending is None:
                return False
            options, done = pending
            now = self.monotonic()
            previous = self.engine
            self.options = options
            # Rooms and circuits the new selection does not change continue;
            # the others start again (house_state.py). The entity allowlist of
            # the Core API follows the new set.
            self.engine = self._carry_over(previous, options, now)
            self.api = None
            self.findings = []
            self.sampled = False
            self.started = now
            self.next_sample = now
            self.next_labels = now
            self.last_identities = None
        if not options.valid:
            self._log('config_invalid')
        done.set()
        return True

    def _carry_over(self, previous, options, now):
        if not options.valid:
            return None
        if previous is None:
            return rules.HouseEngine(options)
        try:
            wall = self.clock()
            document = house_state.export(previous, mono=now, wall=wall, boot=None)
            engine, _outcome = house_state.restore(options, document, mono=now, wall=wall, boot=None)
            return engine
        except Exception:
            return rules.HouseEngine(options)

    def cycle(self, stop):
        self._apply_pending()
        token = self._ensure_token()
        if token is not None:
            self._socket_step(token, stop)
        else:
            stop.wait(1)
        if stop.is_set():
            return
        now = self.monotonic()
        if now >= self.next_sample:
            self.next_sample = now + rules.SAMPLE_PERIOD_S
            self.sample(stop)
        if not stop.is_set():
            self.maybe_report()
            self.save_state()

    # -- context
    def _close_socket(self):
        if self.socket is not None:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    def _socket_step(self, token, stop):
        now = self.monotonic()
        if self.socket is None:
            if now < self.socket_retry:
                stop.wait(min(1.0, self.socket_retry - now))
                return
            subscription = self.subscription_factory(token)
            try:
                subscription.open()
            except WebSocketError as error:
                self.socket_state = error.args[0] if error.args else 'unavailable'
                self.socket_retry = self.monotonic() + self.backoff.failed()
                self._log('websocket_unavailable')
                return
            self.socket = subscription
            self.socket_opened = self.monotonic()
            self.socket_state = 'subscribed'
            self.backoff.reset()
            self.next_labels = self.monotonic()  # labels are recomputed on reconnect
            return
        try:
            messages = self.socket.poll(1.0)
        except WebSocketError as error:
            self._close_socket()
            self.socket_state = error.args[0] if error.args else 'unavailable'
            self.socket_retry = self.monotonic() + self.backoff.failed()
            self._log('websocket_unavailable')
            return
        for topic, payload, retained in messages:
            self.ingest_context(topic, payload, retained)
        now = self.monotonic()
        last = self.context_mono if self.context_mono is not None and self.context_mono > self.socket_opened else self.socket_opened
        if now - last >= CONTEXT_RESUBSCRIBE_S:
            # An MQTT integration reload drops WebSocket subscriptions. A fresh
            # connection keeps exactly one subscription per connection.
            self._close_socket()
            self.socket_state = 'resubscribe'
            self.socket_retry = now

    def ingest_context(self, topic, payload, retained=False):
        match = BASE_TOPIC.fullmatch(topic) if isinstance(topic, str) else None
        if match is None or retained:
            self.counters['context_invalid'] += 1
            return
        base = match.group(1)
        if self.base is not None and base != self.base:
            self.counters['foreign_base'] += 1
            return
        status, context = rules.validate_context(payload)
        if status == 'invalid':
            self.counters['context_invalid'] += 1
            return
        now = self.monotonic()
        with self.lock:
            # A well-formed context of any major pins the base, so reports can
            # still tell the adapter which context majors this app accepts.
            self.base = base
            if status == 'unsupported':
                self.counters['context_unsupported'] += 1
                self.unsupported_mono = now
                self._log('context_version_unsupported')
                return
            self.context, self.context_mono = context, now
            ack = context['report_ack']
            if ack is not None:
                self.ack, self.ack_wall = ack, self.clock()
                if ack['instance'] == self.instance and ack['result'] != 'accepted':
                    self._log('report_not_accepted')

    def fresh_context(self, now):
        if self.context is not None and now - self.context_mono <= rules.CONTEXT_VALID_S:
            return 'ok', self.context
        if self.unsupported_mono is not None and now - self.unsupported_mono <= rules.CONTEXT_VALID_S:
            return 'unsupported', None
        return 'none', None

    # -- sampling
    def sample(self, stop):
        with self.lock:
            engine, options, api = self.engine, self.options, self.api
        if engine is None:
            self.sampled = True
            return
        ha, samples, config = 'unavailable', {}, None
        if api is not None:
            try:
                config = api.config()
                ha = 'running' if config.get('state') == 'RUNNING' else 'starting'
            except ApiError:
                ha = 'unavailable'
        if ha == 'running':
            for entity in options.entities:
                if stop.is_set():
                    return
                try:
                    samples[entity] = api.state(entity)
                except ApiError:
                    ha, samples = 'unavailable', {}
                    break
        if ha == 'unavailable':
            self._log('ha_api_unavailable')
        if config is not None:
            self.zone = config.get('time_zone') if isinstance(config.get('time_zone'), str) else None
        now, wall = self.monotonic(), self.clock()
        state, context = self.fresh_context(now)
        with self.lock:
            if self.engine is not engine:
                return   # a new selection arrived meanwhile; its sample comes next
            self.findings = self.engine.step(now, wall=wall, ha=ha, samples=samples, context=context,
                                             context_state=state,
                                             local_minutes=local_minutes(wall, self.zone, self.zones))
            if now >= self.next_labels and ha == 'running':
                self.engine.refresh_labels()
                self.findings = self.engine._findings(now, self.engine.ctx)
                self.next_labels = now + LABEL_REFRESH_S
            self.sampled = True

    # -- reporting
    def current_report(self, now):
        state, context = self.fresh_context(now)
        context_id = context['context'] if state == 'ok' else None
        if self.engine is None:
            sensors, circuits, findings = rules.invalid_config_parts(self.options, now - self.started)
            monitor, reason = 'unknown', 'config_invalid'
        else:
            sensors, circuits = self.engine.sensors_report(now), self.engine.circuits_report()
            findings, monitor, reason = self.findings, self.engine.monitor, self.engine.reason
        return dict(instance=self.instance, context_id=context_id, mode=self.options.mode,
                    monitor=monitor, reason=reason, sensors=sensors, circuits=circuits, findings=findings)

    def maybe_report(self):
        api = self.api
        if self.base is None or not self.sampled or api is None:
            return False
        now = self.monotonic()
        with self.lock:
            parts = self.current_report(now)
            current = rules.identities(parts['findings'])
            due = (self.last_report is None or now - self.last_report >= REPORT_PERIOD_S
                   or (current != self.last_identities and now - self.last_report >= REPORT_MIN_SPACING_S))
            if not due:
                return False
            self.seq += 1
            _report, payload = rules.build_report(seq=self.seq, **parts)
            topic = self.base + '/house/report'
        self.last_report, self.last_identities = now, current
        try:
            api.publish(topic, payload)
            self.publish_state, self.last_report_wall = 'ok', self.clock()
        except ApiError:
            self.publish_state = 'failed'
            self._log('publish_failed')
        return True

    # -- read-only page
    def status(self):
        now = self.monotonic()
        with self.lock:
            state, context = self.fresh_context(now)
            parts = self.current_report(now)
            ack = self.ack if self.ack is not None and self.ack['instance'] == self.instance else None
            return {
                'valid': self.options.valid, 'mode': self.options.mode, 'configured': self.options.configured,
                'monitor': parts['monitor'], 'reason': parts['reason'], 'circuits': parts['circuits'],
                'sensors': parts['sensors'], 'findings': len(parts['findings']),
                'context': state, 'base_pinned': self.base is not None, 'websocket': self.socket_state,
                'last_report_wall': self.last_report_wall, 'publish': self.publish_state,
                'ack': ack['result'] if ack else None, 'ack_other_instance': self.ack is not None and ack is None,
                'basis_unknown': self.engine.basis_unknown_minutes(now) if self.engine else None,
                'counters': dict(self.counters),
            }

    def _candidates(self, max_age=CANDIDATE_CACHE_S):
        now = self.monotonic()
        with self.candidate_lock:
            if self.candidates is not None and now - self.candidates[0] < max_age:
                return self.candidates[1]
            rows = None
            token = self.token
            if token is None:
                try:
                    token = self.token_source()
                except Exception:
                    token = None
            if token is not None:
                try:
                    states = self.api_factory(token, ()).states()
                    rows = candidate_rows(states)
                except ApiError:
                    rows = None
            self.candidates = (now, rows)
            return rows

    def page(self):
        return render_page(self.status(), self._candidates(), self.zone, self.zones)


def candidate_rows(states):
    rows = []
    for state in states:
        if not isinstance(state, dict):
            continue
        entity = state.get('entity_id')
        attributes = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        if not isinstance(entity, str) or not rules.ENTITY_PATTERN.fullmatch(entity):
            continue
        unit = attributes.get('unit_of_measurement')
        if attributes.get('device_class') != 'temperature' and unit not in ('°C', '°F'):
            continue
        name = attributes.get('friendly_name')
        value = state.get('state')
        rows.append((entity, name if isinstance(name, str) else '',
                     (value if isinstance(value, str) else '') + (' ' + unit if isinstance(unit, str) else '')))
        if len(rows) >= CANDIDATE_ROWS:
            break
    return sorted(rows)


REASON_TEXT = {
    None: 'Bewertung läuft.',
    'config_invalid': 'Die Raumsensor-Einstellungen sind ungültig. Konfiguration der App prüfen.',
    'ha_starting': 'Home Assistant startet noch.',
    'ha_api_unavailable': 'Home Assistant ist für die App gerade nicht erreichbar.',
    'no_context': 'Keine aktuellen Daten der VM. Die Räume werden gerade nicht bewertet.',
    'context_version_unsupported': 'Die Version der VM passt nicht zu dieser App. App oder VM aktualisieren.',
}
ACK_TEXT = {
    'accepted': 'Die VM nimmt die Raummeldungen an.',
    'context_mismatch': 'Die VM nimmt die Raummeldungen an, aber sie passen nicht zum aktuellen Stand der VM.',
    'instance_conflict': 'Die VM nimmt die Raummeldungen nicht an: eine andere App meldet bereits.',
    'rejected_schema': 'Die VM nimmt die Raummeldungen nicht an: ungültige Daten.',
    'rejected_version': 'Die VM nimmt die Raummeldungen nicht an: die Version passt nicht.',
    'rejected_limits': 'Die VM nimmt die Raummeldungen nicht an: zu viele oder zu große Daten.',
}
TYPE_TEXT = {'radiator': 'Heizkörper', 'floor': 'Fußboden', 'absent': 'nicht vorhanden', None: 'unbekannt'}
SOURCE_TEXT = {'controller': 'aus der Steuerung', 'option': 'aus den App-Einstellungen', None: ''}
JUDGED_TEXT = {'yes': 'wird bewertet', 'inactive': 'Heizkreis aus', 'unknown': 'nicht bewertet'}
WHY_TEXT = {'context': 'keine gültigen Daten der Steuerung', 'phase': 'Sollwert unbekannt',
            'type': 'Art des Heizkreises unbekannt', 'sensors': 'kein nutzbarer Sensor',
            'absent': 'Heizkreis nicht vorhanden', 'ha': 'Home Assistant oder Einstellungen'}


def _time(wall, zone_name, zones):
    if wall is None:
        return 'noch keine'
    try:
        if isinstance(zone_name, str) and zone_name in zones:
            return datetime.fromtimestamp(wall, zones[zone_name]).strftime('%d.%m.%Y %H:%M')
        return datetime.fromtimestamp(wall, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
    except (OverflowError, OSError, ValueError):
        return 'unbekannt'


PAGE_STYLE = (
    ':root{color-scheme:light dark;--fg:#1c1c1c;--bg:#fff;--muted:#5f6368;--line:#c8c8c8;--accent:#03a9f4;'
    '--card:#f6f7f8;--ok:#1b5e20;--bad:#b00020}'
    '@media (prefers-color-scheme:dark){:root{--fg:#e8e8e8;--bg:#111315;--muted:#a8adb3;--line:#3b3f44;'
    '--accent:#4fc3f7;--card:#1c1f22;--ok:#81c784;--bad:#ff8a80}}'
    'body{font-family:sans-serif;margin:16px;max-width:60rem;color:var(--fg);background:var(--bg);line-height:1.4}'
    'table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:4px;text-align:left;'
    'overflow-wrap:break-word}')


def status_html(status, zone=None, zones=None):
    """The monitoring status part of the room page (unchanged wording)."""
    escape = html.escape
    zones = zones or {}
    lines = ['<h1>Raumüberwachung</h1><p>Raumsensoren dienen nur der Überwachung. Sie steuern nichts.</p><ul>']
    lines.append(f'<li>Modus: {escape(status["mode"])}'
                 + (' (Meldungen werden nur angezeigt)' if status['mode'] == 'beobachten' else ' (Meldungen zählen für den Gesamtzustand)')
                 + '</li>')
    lines.append(f'<li>Überwachte Sensoren: {int(status["configured"]) if status["valid"] else "keine gültige Auswahl"}</li>')
    if status['valid'] and status['configured'] == 0:
        lines.append('<li>Keine Sensoren ausgewählt: die Raumüberwachung ist aus.</li>')
    lines.append(f'<li>Zustand: {escape(REASON_TEXT.get(status["reason"], "unbekannt"))}</li>')
    lines.append(f'<li>Letzte Meldung an die VM: {escape(_time(status["last_report_wall"], zone, zones))}'
                 + (' (Senden fehlgeschlagen)' if status['publish'] == 'failed' else '') + '</li>')
    if not status['base_pinned']:
        ack = 'Noch keine Daten von der VM empfangen. Die VM nimmt die Raummeldungen nicht an.'
    elif status['ack'] is None and status['ack_other_instance']:
        ack = 'Die VM nimmt die Raummeldungen nicht an: sie bestätigt eine andere App.'
    elif status['ack'] is None:
        ack = 'Die VM nimmt die Raummeldungen nicht an (keine Bestätigung).'
    else:
        ack = ACK_TEXT.get(status['ack'], 'Die VM nimmt die Raummeldungen nicht an.')
    lines.append(f'<li>{escape(ack)}</li></ul>')
    basis = status.get('basis_unknown') or {}
    lines.append('<h2>Heizkreise</h2><table><tr><th>Heizkreis</th><th>Art</th><th>Bewertung</th>'
                 '<th>Minuten ohne gültigen Sollwert (24 Std. / 7 Tage)</th></tr>')
    for index, name in enumerate(rules.CIRCUITS, 1):
        circuit = status['circuits'][name]
        kind = TYPE_TEXT.get(circuit['type'], 'unbekannt')
        source = SOURCE_TEXT.get(circuit['type_source'], '')
        judged = JUDGED_TEXT.get(circuit['judged'], 'nicht bewertet')
        if circuit['judged'] == 'unknown' and circuit['why'] in WHY_TEXT:
            judged += ': ' + WHY_TEXT[circuit['why']]
        minutes = basis.get(name)
        counted = f'{minutes["24h"]} / {minutes["7d"]}' if minutes else '-'
        lines.append(f'<tr><td>Heizkreis {index}</td><td>{escape(kind)} {escape(source)}</td>'
                     f'<td>{escape(judged)}</td><td>{escape(counted)}</td></tr>')
    lines.append('</table>')
    return ''.join(lines)


def render_page(status, rows, zone=None, zones=None):
    """Read-only page without script: status and the candidate list."""
    escape = html.escape
    lines = ['<!doctype html><html lang="de"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>Raumsensoren</title><style>{PAGE_STYLE}</style></head><body>',
             status_html(status, zone, zones),
             '<h2>Temperatursensoren in Home Assistant</h2>']
    if rows is None:
        lines.append('<p>Die Liste ist gerade nicht verfügbar.</p>')
    else:
        lines.append('<table><tr><th>Sensor</th><th>Name</th><th>Wert</th></tr>')
        for entity, name, value in rows:
            lines.append(f'<tr><td>{escape(entity)}</td><td>{escape(name[:200])}</td><td>{escape(value[:64])}</td></tr>')
        lines.append('</table>')
    lines.append('</body></html>')
    return ''.join(lines).encode('utf-8')
