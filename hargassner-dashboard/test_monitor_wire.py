"""Actual disposable loopback wire tests; never contact HA/VM/controller."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import app_monitor as m
import health_alerts as h
import run_app
import ingress_proxy

TOKEN = 'h' * 43
INGRESS = 'i' * 43
SUPERVISOR_TOKEN = 'synthetic-supervisor-token'


def event():
    return h.transition({kind: h.result('unknown', 'endpoint_unavailable') for kind in h.KINDS}, h.initial_state(), 2e9)['queue'][0]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def reply(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        self.send_response(self.server.status)
        for name, value in self.server.headers: self.send_header(name, value)
        declared = getattr(self.server, 'declared_length', None)
        self.send_header('Content-Length', str(len(self.server.body) if declared is None else declared)); self.end_headers()
        try: self.wfile.write(self.server.body)
        except (OSError, ssl.SSLError): pass
    do_GET = do_POST = reply


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.cert = Path(cls.temp.name) / 'cert.pem'; key = Path(cls.temp.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost', '-keyout', str(key), '-out', str(cls.cert)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(cls.cert, key)
        cls.sni = []; tls.set_servername_callback(lambda _s, name, _c: cls.sni.append(name))
        cls.tls = ThreadingHTTPServer(('127.0.0.1', 0), Handler); cls.tls.socket = tls.wrap_socket(cls.tls.socket, server_side=True)
        cls.tls_thread = threading.Thread(target=cls.tls.serve_forever, daemon=True); cls.tls_thread.start()
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler); cls.http_thread = threading.Thread(target=cls.http.serve_forever, daemon=True); cls.http_thread.start()
    @classmethod
    def tearDownClass(cls):
        for server, thread in [(cls.tls, cls.tls_thread), (cls.http, cls.http_thread)]: server.shutdown(); server.server_close(); thread.join()
        cls.temp.cleanup()
    def setUp(self):
        self.sni.clear()
        for server in [self.tls, self.http]: server.requests = []; server.status = 200; server.body = b'{}'; server.headers = []; server.declared_length = None
    def heartbeat(self, host='localhost'):
        port = self.tls.server_address[1]
        context = ssl.create_default_context(cafile=str(self.cert))
        self.assertTrue(context.check_hostname); self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        with patch.object(m.ssl, 'create_default_context', return_value=context):
            return m.request_worker({'kind': 'heartbeat', 'host': host, 'port': port, 'authority': f'{host}:{port}', 'token': TOKEN, 'connect_host': '127.0.0.1'})
    def test_https_get_exact_route_sni_host_and_only_health_credential(self):
        result = self.heartbeat(); self.assertTrue(result['ok']); self.assertEqual(self.sni, ['localhost'])
        method, path, headers, body = self.tls.requests[0]
        self.assertEqual((method, path, body), ('GET', m.HEARTBEAT_PATH, b''))
        self.assertEqual(headers['X-Hargassner-Health'], TOKEN); self.assertEqual(headers['Host'], f'localhost:{self.tls.server_address[1]}')
        for forbidden in ['Authorization', 'Origin', 'X-Hargassner-Ingress', 'Cookie']: self.assertNotIn(forbidden, headers)
    def test_hostname_failure_and_redirect_are_never_bypassed(self):
        self.assertFalse(self.heartbeat('other.invalid')['ok']); self.assertEqual(self.tls.requests, [])
        self.tls.status = 302; self.tls.headers = [('Location', 'https://other.invalid/private')]
        self.assertFalse(self.heartbeat()['ok']); self.assertEqual(len(self.tls.requests), 1)
    def test_oversized_or_duplicate_json_and_compression_are_refused(self):
        for body, headers in [(b' ' * (m.MAX_RESPONSE + 1), []), (b'{"a":1,"a":2}', []), (b'{}', [('Content-Encoding', 'gzip')])]:
            self.tls.body = body; self.tls.headers = headers
            with self.subTest(bytes=len(body)): self.assertFalse(self.heartbeat()['ok'])
    def test_truncated_heartbeat_body_is_refused_even_when_json_is_complete(self):
        self.tls.declared_length = len(self.tls.body) + 10
        self.assertFalse(self.heartbeat()['ok'])
        self.assertEqual(len(self.tls.requests), 1)
    def test_notification_exact_local_supervisor_path_and_stable_id_on_wire(self):
        original = http.client.HTTPConnection
        def connect(host, port, **kwargs):
            self.assertEqual((host, port), ('172.30.32.2', 80))
            return original('127.0.0.1', self.http.server_address[1], **kwargs)
        with patch.object(m.http.client, 'HTTPConnection', side_effect=connect):
            for _ in range(2): self.assertTrue(m.request_worker({'kind': 'notification', 'token': SUPERVISOR_TOKEN, 'event': event()})['ok'])
        first, second = self.http.requests
        self.assertEqual(first[:2], ('POST', '/core/api/services/persistent_notification/create'))
        self.assertEqual(first[2]['Authorization'], 'Bearer ' + SUPERVISOR_TOKEN)
        payload = json.loads(first[3]); self.assertEqual(payload['notification_id'], 'harg_health_heartbeat'); self.assertIn('does not prove the heater is down', payload['message'])
        self.assertEqual(first[3], second[3]); self.assertNotIn(TOKEN, first[3].decode())
    def test_real_heartbeat401_then_restore_queues_unknown_and_recovery_not_heater_down(self):
        self.tls.status = 401
        now = 2_000_000_000.0; delivered = []
        settings = m.MonitorSettings(True, m.Settings('localhost', self.tls.server_address[1], 'localhost', INGRESS, '127.0.0.1'), TOKEN)
        def network(request):
            if request['kind'] == 'heartbeat':
                reply = self.heartbeat()
                if not reply['ok']: raise h.Refusal('delivery_failed')
                return reply
            delivered.append(request['event']); return {'ok': True}
        with tempfile.TemporaryDirectory() as temporary:
            monitor = m.Monitor(settings, SUPERVISOR_TOKEN, Path(temporary) / 'state', network=network, clock=lambda: now)
            monitor.poll_once()
            self.assertTrue(all(r['status'] == 'unknown' for r in h.read_json(monitor.state_path, private=True)['conditions'].values()))
            self.tls.status = 200; self.tls.body = json.dumps({'schema_version': 1, 'sent_at': now, 'checks': {kind: {'checked_at': now, 'status': 'healthy'} for kind in ('certificate', 'telnet')}}).encode()
            for _ in range(5): monitor.poll_once()
            self.assertEqual([e['transition'] for e in delivered], ['alert'] * 3 + ['recovery'] * 3)
            self.assertTrue(all(e['status'] == 'unknown' and e['reason'] == 'endpoint_unavailable' for e in delivered[:3]))
    def test_notification_redirect_is_not_followed(self):
        original = http.client.HTTPConnection; self.http.status = 302
        with patch.object(m.http.client, 'HTTPConnection', side_effect=lambda _host, _port, **kw: original('127.0.0.1', self.http.server_address[1], **kw)):
            self.assertFalse(m.request_worker({'kind': 'notification', 'token': SUPERVISOR_TOKEN, 'event': event()})['ok'])
        self.assertEqual(len(self.http.requests), 1)


class StatusWireTests(unittest.TestCase):
    def setUp(self):
        self.settings = ingress_proxy.Settings('unused.example', 443, 'unused.example', INGRESS)
        self.document = run_app.disabled_status()
        self.server = run_app.MonitorProxyServer(('127.0.0.1', 0), self.settings, lambda: self.document)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.peer = patch.object(ingress_proxy, 'SUPERVISOR_IP', '127.0.0.1'); self.peer.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.peer.stop()
    def request(self, method='GET', path='/api/local-monitor/status', identity=True):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        headers = {'X-Remote-User-Id': 'synthetic-nonadmin', 'X-Ingress-Path': '/api/hassio_ingress/synthetic_test_path_1234'} if identity else {}
        client.request(method, path, headers=headers); result = client.getresponse(); body = result.read(); status = result.status; client.close(); return status, body
    def test_authenticated_nonadmin_status_returns_fixed_sanitized_fields(self):
        status, body = self.request(); self.assertEqual(status, 200); self.assertEqual(json.loads(body), self.document)
        for secret in [INGRESS, TOKEN, SUPERVISOR_TOKEN, self.settings.host, 'synthetic-nonadmin']: self.assertNotIn(secret, body.decode())
    def test_unauthenticated_wrong_peer_query_and_mutation_refused(self):
        self.assertEqual(self.request(identity=False)[0], 400)
        with patch.object(ingress_proxy, 'SUPERVISOR_IP', '172.30.32.2'): self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.request('POST')[0], 405); self.assertEqual(self.request(path='/api/local-monitor/status?x=1')[0], 400)


if __name__ == '__main__': unittest.main()
