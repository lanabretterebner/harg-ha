"""Minimal RFC 6455 client for the Home Assistant Core WebSocket.

Used only to receive the adapter context: `auth` with the Supervisor token,
one `mqtt/subscribe` on the context topic, and `ping`; and, from app 1.6.1,
one short read-only `config/auth/list` connection that tells the room page
whether its viewer is an administrator; and, from app 1.6.10, one short
read-only `recorder/statistics_during_period` connection for "Raum anlernen"
(hourly long-term statistics of one room sensor). Bounded: connect
timeout, message cap, fragment cap, pong deadline. No exception text is
logged by callers; it can contain addresses.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import select
import socket
import struct
import time

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
CONNECT_TIMEOUT_S = 10
MESSAGE_CAP = 65536
FRAGMENT_CAP = 16
FRAME_READ_TIMEOUT_S = 10
HEADER_CAP = 8192
PING_INTERVAL_S = 30
PONG_DEADLINE_S = 10
BACKOFF_S = (5, 10, 20, 40, 60)
CONTEXT_TOPIC = 'hargassner/+/house/context'


class WebSocketError(Exception):
    """Fixed category in args[0]; never formatted with untrusted text."""


def strict_loads(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result

    def constant(_value):
        raise ValueError('constant')

    return json.loads(text, object_pairs_hook=unique, parse_constant=constant)


class WebSocket:
    """Client side of one connection. Not thread-safe; one owner thread."""

    def __init__(self, sock):
        self.sock = sock
        self.buffer = b''
        self.closed = False

    @classmethod
    def connect(cls, host, port, path, *, timeout=CONNECT_TIMEOUT_S, create_connection=socket.create_connection):
        deadline = time.monotonic() + timeout
        try:
            sock = create_connection((host, port), timeout=timeout)
        except OSError:
            raise WebSocketError('connect') from None
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            request = (f'GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n'
                       f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                       'Sec-WebSocket-Version: 13\r\n\r\n')
            sock.sendall(request.encode('ascii'))
            raw = b''
            while b'\r\n\r\n' not in raw:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WebSocketError('handshake')
                sock.settimeout(remaining)
                chunk = sock.recv(1024)
                if not chunk:
                    raise WebSocketError('handshake')
                raw += chunk
                if len(raw) > HEADER_CAP:
                    raise WebSocketError('handshake')
            head, rest = raw.split(b'\r\n\r\n', 1)
            lines = head.decode('latin-1').split('\r\n')
            status = lines[0].split(' ', 2)
            if len(status) < 2 or status[0] not in ('HTTP/1.1', 'HTTP/1.0') or status[1] != '101':
                raise WebSocketError('handshake')
            headers = {}
            for line in lines[1:]:
                name, sep, value = line.partition(':')
                if not sep:
                    raise WebSocketError('handshake')
                name = name.strip().lower()
                if name in headers:
                    raise WebSocketError('handshake')
                headers[name] = value.strip()
            expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            if (headers.get('upgrade', '').lower() != 'websocket'
                    or 'upgrade' not in headers.get('connection', '').lower()
                    or headers.get('sec-websocket-accept') != expected
                    or headers.get('sec-websocket-extensions')):
                raise WebSocketError('handshake')
            connection = cls(sock)
            connection.buffer = rest
            return connection
        except WebSocketError:
            sock.close()
            raise
        except (OSError, UnicodeError, ValueError):
            sock.close()
            raise WebSocketError('handshake') from None

    # -- frames
    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack('!H', length)
        else:
            header += bytes([0x80 | 127]) + struct.pack('!Q', length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        try:
            self.sock.settimeout(FRAME_READ_TIMEOUT_S)
            self.sock.sendall(header + mask + masked)
        except OSError:
            self.closed = True
            raise WebSocketError('send') from None

    def send_json(self, document):
        payload = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
        if len(payload) > MESSAGE_CAP:
            raise WebSocketError('send')
        self._send_frame(0x1, payload)

    def _read_exact(self, count, deadline):
        while len(self.buffer) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebSocketError('timeout')
            try:
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(max(4096, count - len(self.buffer)))
            except (socket.timeout, TimeoutError):
                raise WebSocketError('timeout') from None
            except OSError:
                raise WebSocketError('receive') from None
            if not chunk:
                raise WebSocketError('closed')
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def _read_frame(self, deadline):
        first, second = self._read_exact(2, deadline)
        fin, rsv, opcode = first & 0x80, first & 0x70, first & 0x0F
        if rsv:
            raise WebSocketError('protocol')
        if second & 0x80:
            raise WebSocketError('protocol')  # a server never masks
        length = second & 0x7F
        if length == 126:
            length = struct.unpack('!H', self._read_exact(2, deadline))[0]
        elif length == 127:
            length = struct.unpack('!Q', self._read_exact(8, deadline))[0]
        if opcode >= 0x8 and (length > 125 or not fin):
            raise WebSocketError('protocol')
        if length > MESSAGE_CAP:
            raise WebSocketError('too_large')
        return bool(fin), opcode, self._read_exact(length, deadline)

    def _readable(self, timeout):
        if self.buffer:
            return True
        try:
            readable, _w, _x = select.select([self.sock], [], [], max(0.0, timeout))
        except (OSError, ValueError):
            raise WebSocketError('receive') from None
        return bool(readable)

    def receive(self, timeout):
        """Return one complete text message, or None when nothing arrived."""
        if self.closed:
            raise WebSocketError('closed')
        if not self._readable(timeout):
            return None
        deadline = time.monotonic() + FRAME_READ_TIMEOUT_S
        parts, fragments, message_opcode = [], 0, None
        while True:
            fin, opcode, payload = self._read_frame(deadline)
            if opcode == 0x8:
                self.closed = True
                try:
                    self._send_frame(0x8, payload[:2])
                except WebSocketError:
                    pass
                raise WebSocketError('closed')
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                if message_opcode is None and not self._readable(0):
                    return None
                continue
            if opcode == 0xA:
                if message_opcode is None and not self._readable(0):
                    return None
                continue
            if opcode in (0x1, 0x2):
                if message_opcode is not None:
                    raise WebSocketError('protocol')
                message_opcode = opcode
            elif opcode == 0x0:
                if message_opcode is None:
                    raise WebSocketError('protocol')
            else:
                raise WebSocketError('protocol')
            fragments += 1
            parts.append(payload)
            if fragments > FRAGMENT_CAP or sum(len(part) for part in parts) > MESSAGE_CAP:
                raise WebSocketError('too_large')
            if fin:
                break
        if message_opcode != 0x1:
            raise WebSocketError('protocol')
        try:
            return b''.join(parts).decode('utf-8')
        except UnicodeError:
            raise WebSocketError('protocol') from None

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self._send_frame(0x8, struct.pack('!H', 1000))
            except WebSocketError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


class ContextSubscription:
    """HA protocol on one WebSocket: auth, one mqtt/subscribe, pings.

    poll() returns (topic, payload) tuples of received MQTT messages.
    Any protocol failure raises WebSocketError with a fixed category.
    """

    def __init__(self, token, *, host='172.30.32.2', port=80, path='/core/websocket',
                 connect=WebSocket.connect, monotonic=time.monotonic, topic=CONTEXT_TOPIC):
        self.token, self.host, self.port, self.path = token, host, port, path
        self.connect, self.monotonic = connect, monotonic
        self.socket = None
        self.next_id = 2
        self.ping_at = None
        self.pong_wait = None
        self.topic = topic
        self.pending = []

    def _expect(self, deadline):
        while True:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise WebSocketError('timeout')
            text = self.socket.receive(remaining)
            if text is None:
                continue
            try:
                message = strict_loads(text)
            except (ValueError, RecursionError):
                raise WebSocketError('protocol') from None
            if isinstance(message, dict):
                return message
            raise WebSocketError('protocol')

    def open(self):
        self.socket = self.connect(self.host, self.port, self.path)
        try:
            deadline = self.monotonic() + CONNECT_TIMEOUT_S
            if self._expect(deadline).get('type') != 'auth_required':
                raise WebSocketError('protocol')
            self.socket.send_json({'type': 'auth', 'access_token': self.token})
            reply = self._expect(deadline)
            if reply.get('type') == 'auth_invalid':
                raise WebSocketError('auth_failed')
            if reply.get('type') != 'auth_ok':
                raise WebSocketError('protocol')
            self.pending = []
            self.socket.send_json({'id': 1, 'type': 'mqtt/subscribe', 'topic': self.topic})
            while True:
                reply = self._expect(deadline)
                if reply.get('id') == 1 and reply.get('type') == 'result':
                    if reply.get('success') is not True:
                        raise WebSocketError('subscribe_refused')
                    break
                if reply.get('id') == 1 and reply.get('type') == 'event':
                    if len(self.pending) >= 64:
                        raise WebSocketError('protocol')
                    self.pending.append(reply)
                    continue
                raise WebSocketError('protocol')
            self.ping_at = self.monotonic() + PING_INTERVAL_S
            self.pong_wait = None
        except WebSocketError:
            self.close()
            raise

    def poll(self, timeout):
        if self.socket is None:
            raise WebSocketError('closed')
        now = self.monotonic()
        # The pong deadline is checked after reading: a pong already waiting in
        # the socket after a slow cycle (sampling, saving) is not a timeout.
        if self.pong_wait is None and now >= self.ping_at:
            identity = self.next_id
            self.next_id += 1
            self.socket.send_json({'id': identity, 'type': 'ping'})
            self.pong_wait = (identity, now + PONG_DEADLINE_S)
        messages = []
        for message in self.pending:
            event = message.get('event')
            if isinstance(event, dict) and isinstance(event.get('topic'), str) and isinstance(event.get('payload'), str):
                messages.append((event['topic'], event['payload'], event.get('retain') is True))
        self.pending = []
        deadline = now + max(0.0, timeout)
        while True:
            text = self.socket.receive(max(0.0, deadline - self.monotonic()))
            if text is None:
                break
            try:
                message = strict_loads(text)
            except (ValueError, RecursionError):
                raise WebSocketError('protocol') from None
            if not isinstance(message, dict):
                raise WebSocketError('protocol')
            kind = message.get('type')
            if kind == 'pong' and self.pong_wait is not None and message.get('id') == self.pong_wait[0]:
                self.pong_wait = None
                self.ping_at = self.monotonic() + PING_INTERVAL_S
            elif kind == 'event' and message.get('id') == 1:
                event = message.get('event')
                if isinstance(event, dict) and isinstance(event.get('topic'), str) and isinstance(event.get('payload'), str):
                    messages.append((event['topic'], event['payload'], event.get('retain') is True))
            elif kind == 'result' and message.get('success') is not True:
                raise WebSocketError('protocol')
            if self.monotonic() >= deadline or len(messages) >= 64:
                break
        if self.pong_wait is not None and self.monotonic() > self.pong_wait[1]:
            raise WebSocketError('pong_timeout')
        return messages

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


USERS_COMMAND = 'config/auth/list'


def list_users(token, *, host='172.30.32.2', port=80, path='/core/websocket',
               connect=WebSocket.connect, monotonic=time.monotonic):
    """One short connection: auth, one `config/auth/list`, close.

    Used only by the room page to tell whether the ingress user is a Home
    Assistant administrator. Returns the list of user objects; any failure
    raises WebSocketError with a fixed category (callers then fail closed).
    """
    socket_ = connect(host, port, path)
    try:
        deadline = monotonic() + CONNECT_TIMEOUT_S

        def expect():
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise WebSocketError('timeout')
                text = socket_.receive(remaining)
                if text is None:
                    continue
                try:
                    message = strict_loads(text)
                except (ValueError, RecursionError):
                    raise WebSocketError('protocol') from None
                if not isinstance(message, dict):
                    raise WebSocketError('protocol')
                return message
        if expect().get('type') != 'auth_required':
            raise WebSocketError('protocol')
        socket_.send_json({'type': 'auth', 'access_token': token})
        reply = expect()
        if reply.get('type') == 'auth_invalid':
            raise WebSocketError('auth_failed')
        if reply.get('type') != 'auth_ok':
            raise WebSocketError('protocol')
        socket_.send_json({'id': 1, 'type': USERS_COMMAND})
        while True:
            reply = expect()
            if reply.get('id') == 1 and reply.get('type') == 'result':
                break
            if 'id' in reply:
                raise WebSocketError('protocol')
        if reply.get('success') is not True:
            raise WebSocketError('refused')
        users = reply.get('result')
        if not isinstance(users, list):
            raise WebSocketError('protocol')
        return users
    finally:
        socket_.close()


STATISTICS_COMMAND = 'recorder/statistics_during_period'
# One answer per piece stays well inside MESSAGE_CAP: 10 days are 240 hourly
# rows of about 120 bytes each.
STATISTICS_PIECE_S = 10 * 86400
STATISTICS_MAX_ROWS = 24 * 400
STATISTICS_DEADLINE_S = 60


def _epoch(value):
    """A statistics row's start: milliseconds (HA 2023.3 and later) or ISO text."""
    if type(value) in (int, float) and math.isfinite(value) and 0 < value < 1e14:
        return value / 1000.0
    if isinstance(value, str) and len(value) <= 40:
        try:
            moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return moment.timestamp() if moment.tzinfo is not None else None
    return None


def _figure(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def statistics_during_period(token, statistic_id, start, end, *, host='172.30.32.2', port=80, path='/core/websocket',
                             connect=WebSocket.connect, monotonic=time.monotonic, piece_s=STATISTICS_PIECE_S):
    """Hourly mean/min/max of one statistic over [start, end) (epoch seconds).

    One connection: auth, one `recorder/statistics_during_period` per piece,
    close. Temperatures are asked in °C. Returns [(start, mean, min, max)] in
    time order, empty when Home Assistant keeps no long-term statistics for
    the sensor; raises WebSocketError with a fixed category on any failure.
    """
    socket_ = connect(host, port, path)
    try:
        deadline = monotonic() + STATISTICS_DEADLINE_S

        def expect():
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise WebSocketError('timeout')
                text = socket_.receive(remaining)
                if text is None:
                    continue
                try:
                    message = strict_loads(text)
                except (ValueError, RecursionError):
                    raise WebSocketError('protocol') from None
                if not isinstance(message, dict):
                    raise WebSocketError('protocol')
                return message
        if expect().get('type') != 'auth_required':
            raise WebSocketError('protocol')
        socket_.send_json({'type': 'auth', 'access_token': token})
        reply = expect()
        if reply.get('type') == 'auth_invalid':
            raise WebSocketError('auth_failed')
        if reply.get('type') != 'auth_ok':
            raise WebSocketError('protocol')
        rows, number, when = {}, 0, start
        while when < end:
            upper = min(end, when + piece_s)
            number += 1
            socket_.send_json({'id': number, 'type': STATISTICS_COMMAND,
                               'start_time': datetime.fromtimestamp(when, timezone.utc).isoformat(),
                               'end_time': datetime.fromtimestamp(upper, timezone.utc).isoformat(),
                               'statistic_ids': [statistic_id], 'period': 'hour',
                               'types': ['mean', 'min', 'max'], 'units': {'temperature': '°C'}})
            while True:
                reply = expect()
                if reply.get('id') == number and reply.get('type') == 'result':
                    break
                if 'id' in reply:
                    raise WebSocketError('protocol')
            if reply.get('success') is not True:
                raise WebSocketError('refused')
            result = reply.get('result')
            if not isinstance(result, dict):
                raise WebSocketError('protocol')
            series = result.get(statistic_id, [])
            if not isinstance(series, list):
                raise WebSocketError('protocol')
            for row in series:
                if not isinstance(row, dict):
                    continue
                moment = _epoch(row.get('start'))
                if moment is None or not when - 3600 <= moment < upper:
                    continue
                rows[moment] = (moment, _figure(row.get('mean')), _figure(row.get('min')), _figure(row.get('max')))
                if len(rows) > STATISTICS_MAX_ROWS:
                    raise WebSocketError('too_large')
            when = upper
        return [rows[moment] for moment in sorted(rows)]
    finally:
        socket_.close()


class Backoff:
    def __init__(self):
        self.failures = 0

    def delay(self):
        return BACKOFF_S[min(self.failures, len(BACKOFF_S) - 1)]

    def failed(self):
        delay = self.delay()
        self.failures += 1
        return delay

    def reset(self):
        self.failures = 0
