"""Independent HA-host health observer. No controller/broker/control access."""
from dataclasses import dataclass, field
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import threading
import time

import health_alerts as health
from ingress_proxy import Settings, TOKEN_PATTERN, upstream_connection

SUPERVISOR_IP = '172.30.32.2'
HEARTBEAT_PATH = '/api/dashboard/v1/monitor-health'
NOTIFY_PATH = '/core/api/services/persistent_notification/create'
REQUEST_TIMEOUT = 5
MAX_RESPONSE = 65536


@dataclass(frozen=True)
class MonitorSettings:
    enabled: bool
    upstream: Settings = field(repr=False)
    token: str | None = field(default=None, repr=False)
    interval: int = 30

    @classmethod
    def load(cls, filename='/data/options.json'):
        upstream = Settings.load(filename)
        with open(filename, 'rb') as stream:
            options = health.decode(stream.read(MAX_RESPONSE + 1))
        if not isinstance(options, dict) or type(options.get('monitor_enabled', False)) is not bool:
            raise ValueError('monitor_configuration')
        enabled = options.get('monitor_enabled', False)
        if not enabled:
            return cls(False, upstream)
        token = options.get('monitor_token')
        interval = options.get('monitor_poll_interval', 30)
        if (not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token)
                or token == upstream.token or type(interval) is not int or not 15 <= interval <= 60):
            raise ValueError('monitor_configuration')
        return cls(True, upstream, token, interval)


def supervisor_token(environ=None):
    token = (os.environ if environ is None else environ).get('SUPERVISOR_TOKEN')
    if (not isinstance(token, str) or not 1 <= len(token) <= 8192
            or any(ord(char) < 33 or ord(char) > 126 for char in token)):
        raise ValueError('supervisor_token_unavailable')
    return token


def private_directory(path):
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise health.Refusal('state_invalid')
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise health.Refusal('state_invalid')
    return path


def request_worker(request):
    """Fixed two-destination transport; called only in the bounded child."""
    connection = None
    try:
        kind = request.get('kind')
        if kind == 'heartbeat':
            # Re-run the SAME origin/connect-IP policy in this isolated child.
            settings = Settings(request['host'], request['port'], request['authority'],
                                request['token'], request.get('connect_host'))
            if (not isinstance(settings.host, str) or not settings.host
                    or type(settings.port) is not int or not 1 <= settings.port <= 65535
                    or not TOKEN_PATTERN.fullmatch(settings.token)):
                raise ValueError('request_invalid')
            context = ssl.create_default_context()
            connection = upstream_connection(settings, context, REQUEST_TIMEOUT - 1)
            connection.request('GET', HEARTBEAT_PATH, headers={
                'Host': settings.authority, 'X-Hargassner-Health': settings.token,
                'Accept': 'application/json', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status != 200:
                return {'ok': False}
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                return {'ok': False}
            length = response.getheader('Content-Length')
            if length is not None and (not re.fullmatch('[0-9]{1,8}', length) or int(length) > MAX_RESPONSE):
                return {'ok': False}
            raw = response.read(MAX_RESPONSE + 1)
            return {'ok': True, 'document': health.decode(raw)}
        if kind == 'notification':
            token = request['token']
            if (not isinstance(token, str) or not 1 <= len(token) <= 8192
                    or any(ord(char) < 33 or ord(char) > 126 for char in token)):
                raise ValueError('request_invalid')
            event = request['event']
            health.validate_state({**health.initial_state(), 'queue': [event]})
            body = json.dumps({'notification_id': 'harg_health_' + event['component'],
                               'title': 'Hargassner health: ' + event['component'],
                               'message': f"{event['transition']}: {event['status']} ({event['reason']}). Event {event['event_id']}" + (' Monitor evidence is unverified; this does not prove the heater is down.' if event['status'] == 'unknown' else '')}, allow_nan=False).encode()
            # Explicit local-only policy. Never interpolate a configured URL.
            connection = http.client.HTTPConnection(SUPERVISOR_IP, 80, timeout=REQUEST_TIMEOUT - 1)
            connection.request('POST', NOTIFY_PATH, body=body, headers={
                'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                'Idempotency-Key': event['event_id'], 'Connection': 'close'})
            response = connection.getresponse()
            return {'ok': 200 <= response.status < 300}
        return {'ok': False}
    except Exception:
        return {'ok': False}
    finally:
        if connection is not None:
            connection.close()


def bounded_request(request):
    """Parent wall deadline includes DNS, TLS, headers and body reads."""
    try:
        response = subprocess.run([sys.executable, str(Path(__file__).resolve()), '_request'],
                                  input=json.dumps(request, allow_nan=False).encode(),
                                  capture_output=True, timeout=REQUEST_TIMEOUT,
                                  env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                       'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
                                       **{key: os.environ[key] for key in ('SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}})
        reply = health.decode(response.stdout)
        if response.returncode or not isinstance(reply, dict) or reply.get('ok') is not True:
            raise health.Refusal('delivery_failed')
        return reply
    except (OSError, ValueError, TypeError, health.Refusal, subprocess.TimeoutExpired):
        raise health.Refusal('delivery_failed') from None


class Monitor:
    """One poller, durable bounded outbox, at most one send per interval."""
    def __init__(self, settings, token, state_directory='/data/health-monitor',
                 *, network=bounded_request, clock=time.time):
        self.settings, self.token, self.network, self.clock = settings, token, network, clock
        self.directory = private_directory(state_directory)
        self.state_path = self.directory / 'alert-state.json'
        self.last_error = None
        self._status_lock = threading.Lock()
        self._status = {'schema_version': 1, 'enabled': True, 'running': False,
                        'last_check_at': None, 'queue_count': None,
                        'last_delivery_condition': None, 'last_error': None}

    def status(self):
        with self._status_lock:
            return {**self._status, 'last_delivery_condition':
                    dict(self._status['last_delivery_condition']) if self._status['last_delivery_condition'] else None}

    def update_status(self, **fields):
        with self._status_lock:
            self._status.update(fields)

    def heartbeat(self):
        upstream = self.settings.upstream
        request = {'kind': 'heartbeat', 'host': upstream.host, 'port': upstream.port,
                   'authority': upstream.authority, 'connect_host': upstream.connect_host,
                   'token': self.settings.token}
        try:
            return self.network(request)['document'], False
        except (health.Refusal, KeyError, TypeError):
            return None, True

    def poll_once(self, stop=None):
        document, failed = self.heartbeat()
        now = self.clock()
        self.update_status(last_check_at=now if health.number(now) and now >= 0 else None)
        with health.state_lock(self.state_path):
            try:
                saved = health.read_json(self.state_path, private=True)
            except health.Refusal:
                if self.state_path.exists() or self.state_path.is_symlink():
                    raise health.Refusal('state_invalid') from None
                saved = health.initial_state()
            proposed = health.transition(health.evaluate(document, now=now, endpoint_failed=failed), saved, now)
            health.atomic_json(self.state_path, proposed)
            self.update_status(queue_count=len(proposed['queue']))
            if proposed['queue'] and not (stop is not None and stop.is_set()):
                event = proposed['queue'][0]
                try:
                    self.network({'kind': 'notification', 'token': self.token, 'event': event})
                except health.Refusal:
                    return {'pending': len(proposed['queue']), 'delivered': 0, 'error': 'delivery_failed'}
                self.update_status(last_delivery_condition={key: event[key] for key in ('component', 'transition', 'status', 'reason')})
                proposed['queue'].pop(0)
                health.atomic_json(self.state_path, proposed)
                self.update_status(queue_count=len(proposed['queue']))
                return {'pending': len(proposed['queue']), 'delivered': 1}
            return {'pending': len(proposed['queue']), 'delivered': 0}

    def run(self, stop):
        self.update_status(running=True)
        try:
            while not stop.is_set():
                try:
                    reply = self.poll_once(stop)
                    failure = reply.get('error')
                except (health.Refusal, OSError, ValueError, TypeError):
                    failure = 'state_or_monitor_unavailable'
                self.update_status(last_error=failure)
                if failure != self.last_error:
                    print('Health monitor: unavailable.' if failure else 'Health monitor: ready.', flush=True)
                    self.last_error = failure
                stop.wait(self.settings.interval)
        finally:
            self.update_status(running=False)


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['_request']:
            raise ValueError('internal_worker_only')
        request = health.decode(sys.stdin.buffer.read(MAX_RESPONSE + 1))
        reply = request_worker(request)
    except Exception:
        reply = {'ok': False}
    print(json.dumps(reply, allow_nan=False))
    raise SystemExit(0 if reply.get('ok') else 2)
