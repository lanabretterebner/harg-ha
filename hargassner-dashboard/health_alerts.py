#!/usr/bin/env python3
"""Sanitized source-only health capture, off-host evaluation and explicit alert delivery."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

MAX_DOCUMENT = 65536
MAX_QUEUE = 64
KINDS = ('heartbeat', 'certificate', 'telnet')
STATES = {'healthy', 'warning', 'fault', 'unknown', 'maintenance'}
REASONS = {'ok', 'report_missing', 'report_invalid', 'clock_invalid', 'heartbeat_stale',
           'run_missing', 'run_stale', 'run_future', 'result_failed', 'certificate_expiring',
           'telemetry_unhealthy', 'maintenance', 'endpoint_unavailable', 'state_invalid'}
TELNET_STATES = {'fresh', 'maintenance', 'stale', 'disconnected', 'profile_mismatch',
                 'unqualified', 'invalid', 'missing'}

class Refusal(Exception):
    """Only fixed codes may cross the public boundary."""


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal('document_invalid')
        result[key] = value
    return result


def decode(raw):
    if len(raw) > MAX_DOCUMENT:
        raise Refusal('document_invalid')
    try:
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal('document_invalid')))
    except (ValueError, UnicodeError, RecursionError):
        raise Refusal('document_invalid') from None


def read_json(path, private=False):
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DOCUMENT
                    or private and (stat.S_IMODE(info.st_mode) != 0o600
                                    or info.st_uid != os.geteuid() or info.st_nlink != 1)):
                raise Refusal('input_refused')
            return decode(stream.read(MAX_DOCUMENT + 1))
    except (OSError, ValueError):
        raise Refusal('input_unavailable') from None


def atomic_json(path, value):
    path = Path(path)
    raw = json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > MAX_DOCUMENT:
        raise Refusal('state_capacity')
    if path.is_symlink():
        raise Refusal('state_invalid')
    fd, temporary = tempfile.mkstemp(prefix='.health-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def state_lock(path):
    lockpath = str(path) + '.lock'
    fd = None
    try:
        fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            os.close(fd)
            raise Refusal('state_invalid')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if fd is not None:
            os.close(fd)
        raise Refusal('state_locked') from None
    try:
        yield
    finally:
        os.close(fd)


def stamp(value):
    if number(value) and value >= 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None and parsed.timestamp() >= 0:
                return parsed.timestamp()
        except (ValueError, OverflowError):
            pass
    raise Refusal('result_invalid')


def normalize(kind, document):
    """Validate producer consistency; copy fixed statuses/original times only."""
    if not isinstance(document, dict):
        raise Refusal('result_invalid')
    if kind == 'certificate':
        state = document.get('status')
        checks = document.get('checks')
        if (not isinstance(state, str) or state not in {'pass', 'warn', 'fail'}
                or type(document.get('exit_code')) is not int
                or document['exit_code'] != {'pass': 0, 'warn': 1, 'fail': 2}[state]
                or document.get('mode') != 'endpoint'
                or not isinstance(checks, dict) or not checks):
            raise Refusal('result_invalid')
        statuses = []
        for check in checks.values():
            if (not isinstance(check, dict) or not isinstance(check.get('status'), str)
                    or check['status'] not in {'pass', 'warn', 'fail'}):
                raise Refusal('result_invalid')
            statuses.append(check['status'])
        aggregate = 'fail' if 'fail' in statuses else 'warn' if 'warn' in statuses else 'pass'
        if state != aggregate:
            raise Refusal('result_invalid')
        if state in {'pass', 'warn'}:
            # Failed preflights legitimately have only a failing preflight check.
            # Successful/warning endpoint results always contain the complete six.
            required = {'chain_trust', 'verified_tls_handshake', 'hostname',
                        'rsa_compatibility', 'leaf_validity', 'leaf_expiry_threshold'}
            if (document.get('network_attempted') is not True or not required <= set(checks)
                    or any(checks[name]['status'] != 'pass'
                           for name in required - {'leaf_expiry_threshold'})
                    or checks['leaf_expiry_threshold']['status'] != state):
                raise Refusal('result_invalid')
        return {'checked_at': stamp(document.get('checked_at_utc')),
                'status': {'pass': 'healthy', 'warn': 'warning', 'fail': 'fault'}[state]}
    if kind == 'telnet':
        state = document.get('state')
        if (type(document.get('schema_version')) is not int or document['schema_version'] != 1
                or document.get('source') != 'telnet' or not isinstance(state, str) or state not in TELNET_STATES
                or type(document.get('alert')) is not bool
                or type(document.get('suppressed')) is not bool
                or document['alert'] != (state not in {'fresh', 'maintenance'})
                or document['suppressed'] != (state == 'maintenance')):
            raise Refusal('result_invalid')
        checked = stamp(document.get('checked_at'))
        observed, age = document.get('last_observed_at'), document.get('age_seconds')
        reasons = document.get('reasons')
        expected, available = document.get('expected_metric_count'), document.get('available_metric_count')
        profile = document.get('profile_id')
        changed, duration = document.get('state_changed_at'), document.get('check_duration_seconds')
        if (type(expected) is not int or not 0 <= expected <= 512 or type(available) is not int
                or not 0 <= available <= expected
                or profile is not None and (not isinstance(profile, str) or not re.fullmatch('[a-f0-9]{64}', profile))
                or not number(changed) or changed < 0 or not number(duration) or duration < 0
                or not isinstance(reasons, list) or len(reasons) > 1
                or any(not isinstance(reason, str) or not re.fullmatch('[a-z_]{1,64}', reason) for reason in reasons)):
            raise Refusal('result_invalid')
        if observed is None:
            if age is not None:
                raise Refusal('result_invalid')
        elif (not number(observed) or observed < 0 or not number(age) or age < 0
              or not math.isclose(age, max(0, checked - observed), rel_tol=0, abs_tol=0.000001)):
            raise Refusal('result_invalid')
        if state == 'fresh':
            # This validates the monitor's own5s sample contract at its original
            # check time. It does not refresh a sensor or control-path authority.
            if (profile is None or expected == 0 or observed is None or reasons
                    or observed - checked > 2 or age > 5):
                raise Refusal('result_invalid')
        elif state == 'maintenance':
            if reasons != ['maintenance_active'] or available != 0:
                raise Refusal('result_invalid')
        elif not reasons or available != 0:
            raise Refusal('result_invalid')
        return {'checked_at': checked,
                'status': 'healthy' if state == 'fresh' else 'maintenance' if state == 'maintenance' else 'fault'}
    raise Refusal('result_invalid')


def capture(certificate=None, telnet=None, now=None):
    now = time.time() if now is None else now
    if not number(now) or now < 0:
        raise Refusal('clock_invalid')
    checks = {}
    for kind, path in [('certificate', certificate), ('telnet', telnet)]:
        if path is None:
            checks[kind] = None
            continue
        try:
            checks[kind] = normalize(kind, read_json(path))
        except Refusal:
            checks[kind] = {'checked_at': None, 'status': 'unknown'}
    return {'schema_version': 1, 'sent_at': now, 'checks': checks}


def settings(config):
    if not isinstance(config, dict) or set(config) - {'heartbeat_max_age', 'certificate_max_age',
            'telnet_max_age', 'future_tolerance', 'timeout', 'webhook', 'home_assistant', 'heartbeat_url', 'ca_file'}:
        raise Refusal('configuration_invalid')
    result = {'heartbeat_max_age': 120, 'certificate_max_age': 90000,
              'telnet_max_age': 45, 'future_tolerance': 2, 'timeout': 5}
    result.update(config)
    for key in ('heartbeat_max_age', 'certificate_max_age', 'telnet_max_age'):
        if not number(result[key]) or not 1 <= result[key] <= 604800:
            raise Refusal('configuration_invalid')
    if not number(result['future_tolerance']) or not 0 <= result['future_tolerance'] <= 5:
        raise Refusal('configuration_invalid')
    if not number(result['timeout']) or not 0.2 <= result['timeout'] <= 30:
        raise Refusal('configuration_invalid')
    if 'ca_file' in result and not isinstance(result['ca_file'], str):
        raise Refusal('configuration_invalid')
    if 'heartbeat_url' in result:
        validate_url(result['heartbeat_url'])
    if 'webhook' in result:
        hook = result['webhook']
        if not isinstance(hook, dict) or set(hook) - {'url', 'format', 'headers'}:
            raise Refusal('configuration_invalid')
        validate_url(hook.get('url'))
        if not isinstance(hook.get('format', 'generic'), str) or hook.get('format', 'generic') not in {'generic', 'slack', 'discord', 'ntfy'}:
            raise Refusal('configuration_invalid')
        headers = hook.get('headers', {})
        if (not isinstance(headers, dict) or len(headers) > 8
                or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}', k)
                       or k.lower() in {'host', 'content-length', 'transfer-encoding', 'connection',
                                       'content-type', 'idempotency-key'}
                       or not isinstance(v, str) or len(v) > 2048
                       or any(ord(c) < 32 or ord(c) > 126 for c in v)
                       for k, v in headers.items())):
            raise Refusal('configuration_invalid')
    if 'home_assistant' in result:
        ha = result['home_assistant']
        if ('webhook' in result or not isinstance(ha, dict)
                or set(ha) - {'url', 'token_file', 'allow_insecure_local'}
                or type(ha.get('allow_insecure_local', False)) is not bool
                or not isinstance(ha.get('token_file'), str) or not ha['token_file']):
            raise Refusal('configuration_invalid')
        parts = validate_url(ha.get('url'), ha.get('allow_insecure_local', False))
        if parts.path not in ('', '/') or parts.query:
            raise Refusal('configuration_invalid')
    return result


def result(status, reason, observed_at=None):
    return {'status': status, 'reason': reason, 'observed_at': observed_at}


def evaluate(document, config=None, now=None, endpoint_failed=False):
    config = settings(config or {})
    now = time.time() if now is None else now
    if not number(now) or now < 0:
        return {kind: result('unknown', 'clock_invalid') for kind in KINDS}
    if endpoint_failed:
        return {kind: result('unknown', 'endpoint_unavailable') for kind in KINDS}
    if document is None:
        return {kind: result('unknown', 'report_missing') for kind in KINDS}
    if (not isinstance(document, dict) or set(document) != {'schema_version', 'sent_at', 'checks'}
            or type(document['schema_version']) is not int or document['schema_version'] != 1
            or not number(document['sent_at']) or document['sent_at'] < 0
            or not isinstance(document['checks'], dict)
            or set(document['checks']) != {'certificate', 'telnet'}):
        return {kind: result('unknown', 'report_invalid') for kind in KINDS}
    sent = document['sent_at']
    if sent > now + config['future_tolerance']:
        return {kind: result('unknown', 'run_future') for kind in KINDS}
    if now - sent > config['heartbeat_max_age']:
        return {kind: result('unknown', 'heartbeat_stale', sent) for kind in KINDS}
    rows = {'heartbeat': result('healthy', 'ok', sent)}
    for kind in ('certificate', 'telnet'):
        check = document['checks'][kind]
        if check is None:
            rows[kind] = result('unknown', 'run_missing')
        elif (not isinstance(check, dict) or set(check) != {'checked_at', 'status'}
              or not isinstance(check['status'], str) or check['status'] not in STATES):
            rows[kind] = result('unknown', 'report_invalid')
        elif check['checked_at'] is None and check['status'] == 'unknown':
            rows[kind] = result('unknown', 'report_invalid')
        elif not number(check['checked_at']) or check['checked_at'] < 0:
            rows[kind] = result('unknown', 'report_invalid')
        elif check['checked_at'] > min(now, sent) + config['future_tolerance']:
            rows[kind] = result('unknown', 'run_future')
        elif now - check['checked_at'] > config[kind + '_max_age']:
            rows[kind] = result('unknown', 'run_stale', check['checked_at'])
        else:
            state = check['status']
            reason = ('ok' if state == 'healthy' else 'maintenance' if state == 'maintenance' else
                      'certificate_expiring' if state == 'warning' and kind == 'certificate' else
                      'telemetry_unhealthy' if kind == 'telnet' and state == 'fault' else 'result_failed')
            # Certificate maintenance and Telnet warning have no producer contract.
            if kind == 'certificate' and state == 'maintenance' or kind == 'telnet' and state == 'warning':
                rows[kind] = result('unknown', 'report_invalid')
            else:
                rows[kind] = result(state, reason, check['checked_at'])
    return rows


def initial_state():
    return {'schema_version': 1, 'sequence': 0, 'conditions': {}, 'queue': [], 'last_evaluated_at': None}


def validate_state(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'sequence', 'conditions', 'queue', 'last_evaluated_at'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['sequence']) is not int or not 0 <= value['sequence'] <= 2**53
            or not isinstance(value['conditions'], dict) or set(value['conditions']) - set(KINDS)
            or not isinstance(value['queue'], list) or len(value['queue']) > MAX_QUEUE
            or value['last_evaluated_at'] is not None and (not number(value['last_evaluated_at']) or value['last_evaluated_at'] < 0)):
        raise Refusal('state_invalid')
    for row in value['conditions'].values():
        if (not isinstance(row, dict) or set(row) != {'status', 'reason', 'observed_at'}
                or not isinstance(row['status'], str) or row['status'] not in STATES
                or not isinstance(row['reason'], str) or row['reason'] not in REASONS
                or row['observed_at'] is not None and (not number(row['observed_at']) or row['observed_at'] < 0)):
            raise Refusal('state_invalid')
    seen = set()
    for event in value['queue']:
        if (not isinstance(event, dict) or set(event) != {'schema_version', 'event_id', 'component', 'transition',
                'status', 'reason', 'observed_at', 'evaluated_at'}
                or type(event['schema_version']) is not int or event['schema_version'] != 1
                or not isinstance(event['component'], str) or event['component'] not in KINDS
                or not isinstance(event['transition'], str) or event['transition'] not in {'alert', 'recovery', 'suppression'}
                or not isinstance(event['status'], str) or event['status'] not in STATES
                or not isinstance(event['reason'], str) or event['reason'] not in REASONS
                or not isinstance(event['event_id'], str) or not re.fullmatch('[a-f0-9]{64}', event['event_id'])
                or event['event_id'] in seen
                or not number(event['evaluated_at']) or event['evaluated_at'] < 0
                or event['observed_at'] is not None and (not number(event['observed_at']) or event['observed_at'] < 0)):
            raise Refusal('state_invalid')
        seen.add(event['event_id'])
    return value


def transition(rows, saved, now):
    saved = decode(json.dumps(validate_state(saved)).encode())
    if not number(now) or now < 0:
        raise Refusal('clock_invalid')
    previous_time = saved['last_evaluated_at']
    if previous_time is not None and now < previous_time:
        rows = {kind: result('unknown', 'clock_invalid') for kind in KINDS}
    for kind in KINDS:
        row = rows[kind]
        old = saved['conditions'].get(kind)
        signature = lambda r: (r['status'], r['reason'])
        if old is None or signature(old) != signature(row):
            # Healthy first observation establishes baseline without a recovery claim.
            if row['status'] != 'healthy' or old is not None:
                if len(saved['queue']) >= MAX_QUEUE:
                    raise Refusal('state_capacity')
                saved['sequence'] += 1
                event = {'schema_version': 1, 'component': kind, 'status': row['status'],
                         'reason': row['reason'], 'observed_at': row['observed_at'], 'evaluated_at': now,
                         'transition': 'recovery' if row['status'] == 'healthy' else
                                       'suppression' if row['status'] == 'maintenance' else 'alert'}
                event['event_id'] = hashlib.sha256(json.dumps([saved['sequence'], event], sort_keys=True).encode()).hexdigest()
                saved['queue'].append(event)
        saved['conditions'][kind] = row
    saved['last_evaluated_at'] = max(now, previous_time or 0)
    return validate_state(saved)


def validate_url(url, allow_insecure_local=False):
    try:
        parts = urlsplit(url)
        if (not isinstance(url, str) or len(url) > 4096 or any(ord(c) <= 32 or ord(c) > 126 for c in url)
                or not parts.hostname or parts.username is not None or parts.password is not None
                or parts.fragment or not 1 <= (parts.port if parts.port is not None
                                               else 443 if parts.scheme == 'https' else 80) <= 65535):
            raise ValueError()
        if parts.scheme != 'https':
            if parts.scheme != 'http' or allow_insecure_local is not True:
                raise ValueError()
            address = ipaddress.ip_address(parts.hostname)
            private = any(address in network for network in
                          (ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'),
                           ipaddress.ip_network('192.168.0.0/16'), ipaddress.ip_network('fc00::/7'))
                          if address.version == network.version)
            if not address.is_loopback and not private:
                raise ValueError()
        return parts
    except (ValueError, TypeError, AttributeError):
        raise Refusal('configuration_invalid') from None


def read_token(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 8192
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid() or info.st_nlink != 1):
                raise Refusal('token_unavailable')
            raw = stream.read(8193)
        token = raw.decode('ascii').rstrip('\n')
        if not token or len(raw) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise Refusal('token_unavailable')
        return token
    except (OSError, UnicodeError, ValueError):
        raise Refusal('token_unavailable') from None


def network_worker(request):
    """Child process; parent enforces a wall-clock deadline including DNS/body I/O."""
    parts = validate_url(request['url'], request.get('allow_insecure_local', False))
    context = ssl.create_default_context(cafile=request.get('ca_file'))
    connection = (http.client.HTTPSConnection(parts.hostname, parts.port or 443,
                                             timeout=request['timeout'], context=context)
                  if parts.scheme == 'https' else
                  http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=request['timeout']))
    try:
        target = parts.path or '/'
        if parts.query:
            target += '?' + parts.query
        body = request.get('body')
        connection.request(request['method'], target, body=body.encode() if body is not None else None,
                           headers=request.get('headers', {}))
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise Refusal('delivery_failed')
        if request['method'] == 'GET':
            raw = response.read(MAX_DOCUMENT + 1)
            return {'ok': True, 'document': decode(raw)}
        # Delivery success needs response headers only; never expose receiver body.
        return {'ok': True}
    finally:
        connection.close()


def request_https(method, url, timeout=5, headers=None, body=None, ca_file=None, allow_insecure_local=False):
    validate_url(url, allow_insecure_local)
    request = {'method': method, 'url': url, 'timeout': timeout, 'headers': headers or {}, 'body': body, 'allow_insecure_local': allow_insecure_local}
    if ca_file:
        request['ca_file'] = ca_file
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '_request'],
                                 input=json.dumps(request, allow_nan=False).encode(), capture_output=True,
                                 timeout=timeout)
        response = decode(process.stdout)
        if process.returncode or response.get('ok') is not True:
            raise Refusal('delivery_failed')
        return response.get('document')
    except (OSError, subprocess.TimeoutExpired, Refusal, ValueError, AttributeError):
        raise Refusal('delivery_failed') from None


def delivery(event, config):
    if 'home_assistant' in config:
        ha = config['home_assistant']
        token = read_token(ha['token_file'])
        body = json.dumps({'notification_id': 'harg_health_' + event['component'],
                           'title': 'Hargassner health: ' + event['component'],
                           'message': f"{event['transition']}: {event['status']} ({event['reason']}). Event {event['event_id']}"})
        request_https('POST', ha['url'].rstrip('/') + '/api/services/persistent_notification/create',
                      config['timeout'], {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                                          'Idempotency-Key': event['event_id']},
                      body, config.get('ca_file'), ha.get('allow_insecure_local', False))
        return
    hook = config.get('webhook')
    if hook is None:
        raise Refusal('destination_unconfigured')
    text = f"Hargassner health: {event['component']} {event['transition']} ({event['reason']}); event {event['event_id']}"
    format_ = hook.get('format', 'generic')
    body = event if format_ == 'generic' else {'text' if format_ == 'slack' else 'content' if format_ == 'discord' else 'message': text}
    if format_ == 'ntfy':
        # ntfy JSON publish needs an explicit topic in its path; send text to that
        # reviewed topic endpoint instead of guessing one from host configuration.
        body = text
    else:
        body = json.dumps(body, allow_nan=False)
    headers = dict(hook.get('headers', {}))
    headers.update({'Content-Type': 'text/plain' if format_ == 'ntfy' else 'application/json',
                    'Idempotency-Key': event['event_id']})
    request_https('POST', hook['url'], config['timeout'], headers, body, config.get('ca_file'))


def run(report, state_path, config, now, deliver=False, endpoint_failed=False):
    config = settings(config)
    state_path = Path(state_path).absolute()
    try:
        for ancestor in (state_path.parent, *state_path.parents):
            if ancestor.is_symlink():
                raise Refusal('state_invalid')
        parent = state_path.parent.stat()
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
            raise Refusal('state_invalid')
    except OSError:
        raise Refusal('state_invalid') from None
    def process(saved):
        proposed = transition(evaluate(report, config, now, endpoint_failed), saved, now)
        if not deliver:
            return {'schema_version': 1, 'dry_run': True, 'conditions': proposed['conditions'],
                    'pending_events': proposed['queue'], 'delivered': 0}
        if not ('webhook' in config or 'home_assistant' in config):
            raise Refusal('destination_unconfigured')
        atomic_json(state_path, proposed)  # durable outbox before any delivery
        sent = 0
        while proposed['queue']:
            event = proposed['queue'][0]
            try:
                delivery(event, config)
            except Refusal:
                return {'schema_version': 1, 'dry_run': False, 'conditions': proposed['conditions'],
                        'pending_count': len(proposed['queue']), 'delivered': sent, 'error': 'delivery_failed'}
            proposed['queue'].pop(0)
            atomic_json(state_path, proposed)
            sent += 1
        return {'schema_version': 1, 'dry_run': False, 'conditions': proposed['conditions'],
                'pending_count': 0, 'delivered': sent}
    if deliver:
        with state_lock(state_path):
            try:
                saved = read_json(state_path, private=True)
            except Refusal:
                if Path(state_path).exists() or Path(state_path).is_symlink():
                    raise Refusal('state_invalid') from None
                saved = initial_state()
            return process(saved)
    try:
        saved = read_json(state_path, private=True)
    except Refusal:
        if Path(state_path).exists() or Path(state_path).is_symlink():
            raise Refusal('state_invalid') from None
        saved = initial_state()
    return process(saved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    capture_parser = commands.add_parser('capture', help='Sanitize existing result files; no network or writes')
    capture_parser.add_argument('--certificate-result')
    capture_parser.add_argument('--telnet-result')
    evaluate_parser = commands.add_parser('evaluate', help='Dry-run by default; use an independent off-host scheduler')
    evaluate_parser.add_argument('--report')
    evaluate_parser.add_argument('--state', required=True)
    evaluate_parser.add_argument('--config', help='Private0600 JSON configuration')
    evaluate_parser.add_argument('--deliver', action='store_true')
    commands.add_parser('_request', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.operation == '_request':
            request = decode(sys.stdin.buffer.read(MAX_DOCUMENT + 1))
            try:
                reply = network_worker(request)
            except Exception:
                reply = {'ok': False}
            print(json.dumps(reply, allow_nan=False))
            return 0 if reply['ok'] else 2
        if args.operation == 'capture':
            reply = capture(args.certificate_result, args.telnet_result)
        else:
            config = settings(read_json(args.config, private=True) if args.config else {})
            failed = False
            if args.report and 'heartbeat_url' in config:
                raise Refusal('configuration_invalid')
            if 'heartbeat_url' in config:
                try:
                    report = request_https('GET', config['heartbeat_url'], config['timeout'],
                                           ca_file=config.get('ca_file'))
                except Refusal:
                    report, failed = None, True
            elif args.report:
                try:
                    report = read_json(args.report)
                except Refusal:
                    report = None
            else:
                report = None
            reply = run(report, args.state, config, time.time(), args.deliver, failed)
        print(json.dumps(reply, sort_keys=True, allow_nan=False))
        return 2 if reply.get('error') else 0
    except Refusal as error:
        print(json.dumps({'error': str(error)}))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print('{"error":"health_operation_refused"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
