"""RFC 6455 client and HA context subscription against a loopback server."""
import base64
import hashlib
import json
import socket
import struct
import threading
import unittest

import ha_websocket as ws


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + ws.GUID).encode()).digest()).decode()


def frame(opcode, payload, fin=True, masked=False, rsv=0):
    head = bytes([(0x80 if fin else 0) | rsv | opcode])
    length = len(payload)
    mask_bit = 0x80 if masked else 0
    if length < 126:
        head += bytes([mask_bit | length])
    elif length < 65536:
        head += bytes([mask_bit | 126]) + struct.pack('!H', length)
    else:
        head += bytes([mask_bit | 127]) + struct.pack('!Q', length)
    if masked:
        mask = b'\x01\x02\x03\x04'
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        head += mask
    return head + payload


class Peer:
    """Server side of one connection, run by a test script in a thread."""

    def __init__(self, conn):
        self.conn, self.buffer = conn, b''

    def read(self, count):
        while len(self.buffer) < count:
            chunk = self.conn.recv(65536)
            if not chunk:
                raise ConnectionError
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def handshake(self, status='101 Switching Protocols', accept=None, extra=''):
        raw = b''
        while b'\r\n\r\n' not in raw:
            raw += self.conn.recv(4096)
        head = raw.split(b'\r\n\r\n')[0].decode()
        self.request = head
        key = [line.split(':', 1)[1].strip() for line in head.split('\r\n') if line.lower().startswith('sec-websocket-key')][0]
        self.conn.sendall((f'HTTP/1.1 {status}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           f'Sec-WebSocket-Accept: {accept or accept_key(key)}\r\n{extra}\r\n').encode())

    def receive(self):
        first, second = self.read(2)
        self.last_masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack('!H', self.read(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', self.read(8))[0]
        mask = self.read(4) if self.last_masked else b'\0\0\0\0'
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self.read(length)))
        return first & 0x0F, payload

    def receive_json(self):
        opcode, payload = self.receive()
        assert opcode == 1
        return json.loads(payload)

    def send(self, data):
        self.conn.sendall(data)

    def send_json(self, document):
        self.send(frame(1, json.dumps(document).encode()))


class Server:
    def __init__(self, script):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(4)
        self.port = self.listener.getsockname()[1]
        self.script, self.errors, self.peers = script, [], []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            conn, _ = self.listener.accept()
            conn.settimeout(5)
            peer = Peer(conn)
            self.peers.append(peer)
            try:
                self.script(peer)
            finally:
                conn.close()
        except Exception as error:  # surfaced by the test
            self.errors.append(error)

    def close(self):
        self.thread.join(5)
        self.listener.close()


def connect(server):
    return ws.WebSocket.connect('127.0.0.1', server.port, '/core/websocket', timeout=3)


class FrameTests(unittest.TestCase):
    def run_script(self, script):
        server = Server(script)
        self.addCleanup(server.close)
        return server

    def test_handshake_accept_and_masked_client_frames(self):
        got = []

        def script(peer):
            peer.handshake()
            got.append(peer.receive_json())
            got.append(peer.last_masked)
            peer.send_json({'type': 'auth_required'})
        server = self.run_script(script)
        client = connect(server)
        client.send_json({'type': 'hello'})
        self.assertEqual(json.loads(client.receive(3)), {'type': 'auth_required'})
        client.close()
        server.close()
        self.assertEqual(got, [{'type': 'hello'}, True])
        self.assertIn('GET /core/websocket HTTP/1.1', server.peers[0].request)

    def test_bad_handshakes_are_refused(self):
        for kwargs in ({'accept': 'wrong'}, {'status': '403 Forbidden'},
                       {'extra': 'Sec-WebSocket-Extensions: permessage-deflate\r\n'}):
            with self.subTest(kwargs=kwargs):
                server = self.run_script(lambda peer, kwargs=kwargs: peer.handshake(**kwargs))
                with self.assertRaises(ws.WebSocketError) as caught:
                    connect(server)
                self.assertEqual(caught.exception.args, ('handshake',))
                server.close()
        with self.assertRaises(ws.WebSocketError) as caught:
            ws.WebSocket.connect('127.0.0.1', 1, '/core/websocket', timeout=1)
        self.assertEqual(caught.exception.args, ('connect',))

    def expect_error(self, frames, category):
        def script(peer):
            peer.handshake()
            for data in frames:
                peer.send(data)
            try:
                peer.receive()
            except Exception:
                pass
        server = self.run_script(script)
        client = connect(server)
        with self.assertRaises(ws.WebSocketError) as caught:
            for _ in range(len(frames) + 1):
                client.receive(3)
        self.assertEqual(caught.exception.args, (category,))
        client.close()

    def test_message_and_fragment_caps(self):
        self.expect_error([frame(1, b'x' * (ws.MESSAGE_CAP + 1))], 'too_large')
        parts = [frame(1, b'a', fin=False)] + [frame(0, b'a', fin=False) for _ in range(15)] + [frame(0, b'a')]
        self.expect_error(parts, 'too_large')   # 17 fragments
        parts = [frame(1, b'"', fin=False)] + [frame(0, b'a', fin=False) for _ in range(14)] + [frame(0, b'"')]

        def script(peer):
            peer.handshake()
            for data in parts:
                peer.send(data)
        server = self.run_script(script)
        client = connect(server)
        self.assertEqual(client.receive(3), '"' + 'a' * 14 + '"')   # 16 fragments are fine
        client.close()

    def test_protocol_violations(self):
        self.expect_error([frame(1, b'{}', masked=True)], 'protocol')     # a server never masks
        self.expect_error([frame(1, b'{}', rsv=0x40)], 'protocol')
        self.expect_error([frame(0, b'{}')], 'protocol')                 # continuation first
        self.expect_error([frame(9, b'x' * 126)], 'protocol')            # long control frame
        self.expect_error([frame(2, b'\x00')], 'protocol')               # binary is not expected
        self.expect_error([frame(1, b'\xff\xfe')], 'protocol')           # not UTF-8

    def test_ping_pong_and_close(self):
        seen = []

        def script(peer):
            peer.handshake()
            peer.send(frame(9, b'abc'))
            seen.append(peer.receive())
            peer.send(frame(1, b'"after"'))
            peer.send(frame(8, struct.pack('!H', 1000)))
            seen.append(peer.receive()[0])
        server = self.run_script(script)
        client = connect(server)
        first = client.receive(3)        # None when only the ping was read
        if first is None:
            first = client.receive(3)
        self.assertEqual(first, '"after"')
        with self.assertRaises(ws.WebSocketError) as caught:
            client.receive(3)
        self.assertEqual(caught.exception.args, ('closed',))
        client.close()
        server.close()
        self.assertEqual(seen, [(0xA, b'abc'), 0x8])

    def test_nothing_arrives_returns_none(self):
        server = self.run_script(lambda peer: (peer.handshake(), peer.read(1)))
        client = connect(server)
        self.assertIsNone(client.receive(0.05))
        client.close()


class SubscriptionTests(unittest.TestCase):
    def test_event_before_subscription_ack_is_delivered(self):
        def script(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_ok'})
            peer.receive_json()
            peer.send_json({'id': 1, 'type': 'event', 'event': {'topic': 'hargassner/gw/house/context',
                                                            'payload': 'synthetic', 'retain': False}})
            peer.send_json({'id': 1, 'type': 'result', 'success': True, 'result': None})
            peer.read(1)
        server, sub = self.subscription(script)
        sub.open()
        self.assertEqual(sub.poll(.01), [('hargassner/gw/house/context', 'synthetic', False)])

    def subscription(self, script, clock=None):
        server = Server(script)
        self.addCleanup(server.close)
        sub = ws.ContextSubscription('synthetic-token', host='127.0.0.1', port=server.port,
                                     monotonic=clock or __import__('time').monotonic)
        self.addCleanup(sub.close)
        return server, sub

    def test_auth_subscribe_events_and_ping(self):
        sent = []
        now = [100.0]

        def script(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            sent.append(peer.receive_json())
            peer.send_json({'type': 'auth_ok'})
            sent.append(peer.receive_json())
            peer.send_json({'id': 1, 'type': 'result', 'success': True, 'result': None})
            peer.send_json({'id': 1, 'type': 'event', 'event': {'topic': 'hargassner/gw/house/context',
                                                                 'payload': '{"x":1}', 'qos': 0, 'retain': False}})
            sent.append(peer.receive_json())
            peer.send_json({'id': sent[-1]['id'], 'type': 'pong'})
            peer.read(1)
        server, sub = self.subscription(script, clock=lambda: now[0])
        sub.open()
        self.assertEqual(sub.poll(0.5), [('hargassner/gw/house/context', '{"x":1}', False)])
        now[0] += ws.PING_INTERVAL_S
        sub.poll(0.5)
        self.assertIsNone(sub.pong_wait)
        sub.close()
        self.assertEqual(sent[0], {'type': 'auth', 'access_token': 'synthetic-token'})
        self.assertEqual(sent[1], {'id': 1, 'type': 'mqtt/subscribe', 'topic': 'hargassner/+/house/context'})
        self.assertEqual(sent[2]['type'], 'ping')
        self.assertEqual(len(sent), 3)   # only auth, one subscribe and ping

    def test_pong_deadline(self):
        now = [0.0]

        def script(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_ok'})
            peer.receive_json()
            peer.send_json({'id': 1, 'type': 'result', 'success': True})
            peer.receive_json()
            peer.read(1)
        server, sub = self.subscription(script, clock=lambda: now[0])
        sub.open()
        now[0] = ws.PING_INTERVAL_S
        sub.poll(0.05)
        now[0] += ws.PONG_DEADLINE_S + 1
        with self.assertRaises(ws.WebSocketError) as caught:
            sub.poll(0.05)
        self.assertEqual(caught.exception.args, ('pong_timeout',))
        sub.close()

    def test_buffered_pong_after_a_slow_cycle_is_not_a_timeout(self):
        # Review 2026-11-23: the deadline was checked before reading, so a pong
        # already in the socket after a cycle longer than the deadline raised.
        now, pong_sent = [0.0], threading.Event()

        def script(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_ok'})
            peer.receive_json()
            peer.send_json({'id': 1, 'type': 'result', 'success': True})
            ping = peer.receive_json()
            peer.send_json({'id': ping['id'], 'type': 'pong'})
            pong_sent.set()
            peer.read(1)
        server, sub = self.subscription(script, clock=lambda: now[0])
        sub.open()
        now[0] = ws.PING_INTERVAL_S
        sub.poll(0)                              # sends the ping
        self.assertTrue(pong_sent.wait(5))
        now[0] += ws.PONG_DEADLINE_S + 1         # a slow cycle before the next poll
        self.assertEqual(sub.poll(0.05), [])
        self.assertIsNone(sub.pong_wait)
        sub.close()

    def test_auth_failure_and_subscribe_error(self):
        def refused_auth(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_invalid', 'message': 'no'})
        server, sub = self.subscription(refused_auth)
        with self.assertRaises(ws.WebSocketError) as caught:
            sub.open()
        self.assertEqual(caught.exception.args, ('auth_failed',))
        self.assertIsNone(sub.socket)

        def refused_subscribe(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_ok'})
            peer.receive_json()
            peer.send_json({'id': 1, 'type': 'result', 'success': False, 'error': {'code': 'unauthorized'}})
        server, sub = self.subscription(refused_subscribe)
        with self.assertRaises(ws.WebSocketError) as caught:
            sub.open()
        self.assertEqual(caught.exception.args, ('subscribe_refused',))

    def test_duplicate_keys_are_protocol_errors(self):
        def script(peer):
            peer.handshake()
            peer.send(frame(1, b'{"type":"auth_required","type":"auth_ok"}'))
        server, sub = self.subscription(script)
        with self.assertRaises(ws.WebSocketError) as caught:
            sub.open()
        self.assertEqual(caught.exception.args, ('protocol',))

    def test_backoff_sequence(self):
        backoff = ws.Backoff()
        self.assertEqual([backoff.failed() for _ in range(7)], [5, 10, 20, 40, 60, 60, 60])
        backoff.reset()
        self.assertEqual(backoff.failed(), 5)


if __name__ == '__main__':
    unittest.main()
