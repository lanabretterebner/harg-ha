"""Synthetic loopback tests; no Home Assistant, dashboard or boiler connection."""

from email.message import Message
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

import ingress_proxy as proxy


SYNTHETIC_TOKEN = "synthetic_test_token_" + "x" * 43
PREFIX = "/api/hassio_ingress/synthetic_test_path_1234"
IDENTITY = {"X-Remote-User-Id": "synthetic-user", "X-Ingress-Path": PREFIX}


def headers(values):
    result = Message()
    for name, value in values:
        result[name] = value
    return result


class ValidationTests(unittest.TestCase):
    def settings(self, **options):
        config = {"upstream_url": "https://dashboard.example:8443", "ingress_token": SYNTHETIC_TOKEN}
        config.update(options)
        with tempfile.TemporaryDirectory() as temp:
            filename = Path(temp) / "options.json"
            filename.write_text(json.dumps(config))
            return proxy.Settings.load(filename)

    def test_https_origin_only_and_strong_token(self):
        result = self.settings()
        self.assertEqual((result.host, result.port), ("dashboard.example", 8443))
        for origin in ("http://dashboard.example", "https://u:p@dashboard.example",
                       "https://dashboard.example/path", "https://dashboard.example/?x=1",
                       "https://dashboard.example/#fragment", "https://dashboard.example:0",
                       "https://dashboard.example:99999", "https://dashboard.example\n",
                       "https://dashboard.example\\bad", None):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.settings(upstream_url=origin)
        for token in ("short", "x" * 129, "x" * 42 + "\n", None):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.settings(ingress_token=token)

    def test_only_actual_supervisor_peer_can_assert_identity(self):
        incoming = headers([*IDENTITY.items(), ("X-Forwarded-For", proxy.SUPERVISOR_IP)])
        for peer in ("127.0.0.1", "172.30.32.3", "192.0.2.5", "::ffff:172.30.32.2"):
            with self.subTest(peer=peer), self.assertRaises(proxy.Rejected):
                proxy.trusted_identity(peer, incoming)
        self.assertEqual(proxy.trusted_identity(proxy.SUPERVISOR_IP, incoming),
                         ("synthetic-user", PREFIX))

    def test_duplicate_missing_and_invalid_identity_rejected(self):
        cases = [[], list(IDENTITY.items()) + [("x-remote-user-id", "another")],
                 list(IDENTITY.items()) + [("x-ingress-path", PREFIX)]]
        for user in ("", "someone,another", "<script>", "user name", "x" * 129):
            cases.append([("X-Remote-User-Id", user), ("X-Ingress-Path", PREFIX)])
        for prefix in ("/", "//evil.example", "/api/hassio_ingress/short",
                       PREFIX + "/../", PREFIX + "?x=1", PREFIX + "\r\nX: y"):
            cases.append([("X-Remote-User-Id", "user"), ("X-Ingress-Path", prefix)])
        for values in cases:
            with self.subTest(values=values), self.assertRaises(proxy.Rejected):
                proxy.trusted_identity(proxy.SUPERVISOR_IP, headers(values))

    def test_ingress_path_trailing_slash_normalized(self):
        incoming = headers([("X-Remote-User-Id", "user"), ("X-Ingress-Path", PREFIX + "/")])
        self.assertEqual(proxy.trusted_identity(proxy.SUPERVISOR_IP, incoming), ("user", PREFIX))


class UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append((self.command, self.path, self.headers, body))
        self.send_response(self.server.status)
        for name, value in self.server.response_headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(self.server.body)

    do_GET = do_POST = do_HEAD = do_PUT = do_PATCH = do_DELETE = reply


class HTTPSProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert = Path(cls.temp.name) / "synthetic-cert.pem"
        key = Path(cls.temp.name) / "synthetic-key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-sha256", "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost", "-keyout", str(key),
            "-out", str(cls.cert),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        cls.upstream.socket = context.wrap_socket(cls.upstream.socket, server_side=True)
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.upstream_thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.upstream.requests = []
        self.upstream.body = b'{"authenticated":true,"csrfToken":"synthetic-csrf"}'
        self.upstream.status = 200
        self.upstream.response_headers = [("Content-Type", "application/json")]
        port = self.upstream.server_address[1]
        settings = proxy.Settings("localhost", port, f"localhost:{port}", SYNTHETIC_TOKEN)
        context = ssl.create_default_context(cafile=str(self.cert))
        self.server = proxy.ProxyServer(("127.0.0.1", 0), settings, context=context)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.peer = patch.object(proxy, "SUPERVISOR_IP", "127.0.0.1")
        self.peer.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.peer.stop()

    def request(self, method="GET", target="/api/v1/session", extra=(), body=b"", identity=True):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        client.putrequest(method, target)
        if identity:
            for name, value in IDENTITY.items():
                client.putheader(name, value)
        for name, value in extra:
            client.putheader(name, value)
        if body and not any(name.lower() == "content-length" for name, _ in extra):
            client.putheader("Content-Length", str(len(body)))
        client.endheaders(body)
        response = client.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        client.close()
        return result

    def test_secure_forwarding_path_user_origin_csrf_and_body(self):
        payload = b'{"synthetic":"request"}'
        status, _, body = self.request("POST", "/api/v1/controls?synthetic=1", (
            ("Origin", "https://ha.example"), ("X-CSRF-Token", "synthetic-csrf"),
            ("Content-Type", "application/json"), ("Cookie", "sensitive-cookie"),
            ("Authorization", "Bearer synthetic-browser-token"),
            ("X-Hargassner-Ingress", "forged"), ("X-Hargassner-User", "forged-user"),
            ("X-Forwarded-For", "192.0.2.1"), ("Forwarded", "for=192.0.2.1"),
        ), payload)
        self.assertEqual(status, 200)
        self.assertIn(b"csrfToken", body)
        method, path, forwarded, received = self.upstream.requests[0]
        self.assertEqual((method, path, received), ("POST", "/api/v1/controls?synthetic=1", payload))
        self.assertEqual(forwarded["X-Hargassner-Ingress"], SYNTHETIC_TOKEN)
        self.assertEqual(forwarded["X-Hargassner-User"], "synthetic-user")
        self.assertEqual(forwarded["X-Ingress-Path"], PREFIX)
        self.assertEqual(forwarded["Origin"], "https://ha.example")
        self.assertEqual(forwarded["X-CSRF-Token"], "synthetic-csrf")
        for name in ("Cookie", "Authorization", "X-Remote-User-Id", "X-Forwarded-For", "Forwarded"):
            self.assertIsNone(forwarded.get(name))

    def test_browser_receives_no_upstream_cookies_or_credentials(self):
        self.upstream.response_headers.extend([
            ("Set-Cookie", "sensitive-upstream-cookie"),
            ("X-Hargassner-Ingress", SYNTHETIC_TOKEN),
            ("Content-Security-Policy", "frame-ancestors 'self'"),
        ])
        status, returned, body = self.request()
        self.assertEqual(status, 200)
        self.assertNotIn("Set-Cookie", returned)
        self.assertNotIn("X-Hargassner-Ingress", returned)
        self.assertNotIn(SYNTHETIC_TOKEN.encode(), body)
        self.assertEqual(returned["Content-Security-Policy"], "frame-ancestors 'self'")

    def test_unauthenticated_or_untrusted_peer_never_reaches_upstream(self):
        self.assertEqual(self.request(identity=False)[0], 400)
        with patch.object(proxy, "SUPERVISOR_IP", "172.30.32.2"):
            self.assertEqual(self.request(extra=(("X-Forwarded-For", "172.30.32.2"),))[0], 403)
        self.assertEqual(self.upstream.requests, [])

    def test_ambiguous_and_oversized_requests_rejected(self):
        for extra, expected in (
            ((("Content-Length", "0"), ("Content-Length", "0")), 400),
            ((("Origin", "https://ha.example"), ("origin", "https://other.example")), 400),
            ((("X-CSRF-Token", "a"), ("x-csrf-token", "b")), 400),
            ((("Transfer-Encoding", "chunked"),), 400),
            ((("Expect", "100-continue"),), 400),
            ((("Content-Length", str(proxy.MAX_BODY + 1)),), 413),
        ):
            with self.subTest(extra=extra):
                self.assertEqual(self.request("POST", extra=extra)[0], expected)
        self.assertEqual(self.upstream.requests, [])

    def test_proxy_does_not_accept_absolute_or_ambiguous_paths(self):
        for target in ("https://other.example/path", "//other.example/path", "/a#fragment", "/a\\b"):
            with self.subTest(target=target):
                self.assertEqual(self.request(target=target)[0], 400)
        self.assertEqual(self.upstream.requests, [])

    def test_upstream_redirect_is_not_followed_or_sent_to_browser(self):
        self.upstream.status = 302
        self.upstream.response_headers.append(("Location", "https://other.example/login"))
        status, returned, _ = self.request()
        self.assertEqual(status, 502)
        self.assertNotIn("Location", returned)
        self.assertEqual(len(self.upstream.requests), 1)

    def test_only_redirect_to_authenticated_ingress_root_is_allowed(self):
        self.upstream.status = 302
        self.upstream.response_headers.append(("Location", PREFIX + "/"))
        status, returned, _ = self.request(target="/login")
        self.assertEqual(status, 302)
        self.assertEqual(returned["Location"], PREFIX + "/")
        for unsafe in (PREFIX + "/../login", PREFIX + "/%2e%2e/login", "/login"):
            self.upstream.response_headers[-1] = ("Location", unsafe)
            with self.subTest(unsafe=unsafe):
                self.assertEqual(self.request()[0], 502)

    def test_upstream_login_failure_does_not_trigger_password_fallback(self):
        self.upstream.status = 401
        self.upstream.response_headers.append(("WWW-Authenticate", 'Basic realm="dashboard"'))
        status, returned, _ = self.request()
        self.assertEqual(status, 401)
        self.assertNotIn("WWW-Authenticate", returned)
        self.assertEqual(len(self.upstream.requests), 1)

    def test_untrusted_upstream_certificate_is_rejected(self):
        self.server.tls = ssl.create_default_context()
        self.assertEqual(self.request()[0], 502)
        self.assertEqual(self.upstream.requests, [])

    def test_upstream_hostname_is_verified(self):
        port = self.upstream.server_address[1]
        self.server.settings = proxy.Settings("127.0.0.1", port, f"127.0.0.1:{port}", SYNTHETIC_TOKEN)
        self.assertEqual(self.request()[0], 502)
        self.assertEqual(self.upstream.requests, [])

    def test_bounded_response(self):
        self.upstream.body = b"x" * 1025
        with patch.object(proxy, "MAX_RESPONSE", 1024):
            self.assertEqual(self.request()[0], 502)

    def test_head_and_empty_statuses_do_not_invent_representation_length(self):
        status, returned, body = self.request("HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertNotIn("Content-Length", returned)
        for upstream_status in (204, 304):
            self.upstream.status = upstream_status
            with self.subTest(status=upstream_status):
                status, returned, body = self.request()
                self.assertEqual(status, upstream_status)
                self.assertEqual(body, b"")
                self.assertNotIn("Content-Length", returned)


if __name__ == "__main__":
    unittest.main()
