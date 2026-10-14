"""Small, bounded HTTPS proxy reachable only through Home Assistant Ingress."""

from dataclasses import dataclass
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import ssl
import sys
import threading
from urllib.parse import urlsplit


SUPERVISOR_IP = "172.30.32.2"
MAX_BODY = 256 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
SOCKET_TIMEOUT = 15
REQUEST_HEADERS = (
    "Accept", "Accept-Language", "Content-Type", "Origin", "X-CSRF-Token",
    "If-None-Match", "If-Modified-Since",
)
RESPONSE_HEADERS = frozenset({
    "content-type", "content-encoding", "cache-control", "etag", "last-modified",
    "content-security-policy", "x-frame-options", "referrer-policy",
    "x-content-type-options", "vary",
})
USER_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
PATH_PATTERN = re.compile(r"/api/hassio_ingress/[A-Za-z0-9_-]{16,256}/?")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,128}")


class Rejected(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    authority: str
    token: str

    @classmethod
    def load(cls, filename="/data/options.json"):
        with Path(filename).open(encoding="utf-8") as handle:
            options = json.load(handle)
        if not isinstance(options, dict):
            raise ValueError("Invalid app configuration")
        url, token = options.get("upstream_url"), options.get("ingress_token")
        if not isinstance(url, str) or re.search(r"[\s\\\x00-\x1f\x7f]", url):
            raise ValueError("Invalid upstream origin")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("An HTTPS origin without credentials or a path is required")
        port = 443 if parsed.port is None else parsed.port
        if not 1 <= port <= 65535:
            raise ValueError("Invalid upstream port")
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("A random URL-safe ingress token is required")
        return cls(parsed.hostname, port, parsed.netloc, token)


def one_header(headers, name, required=False):
    values = headers.get_all(name, [])
    if not values and not required:
        return None
    if (len(values) != 1 or len(values[0]) > 8192
            or re.search(r"[^\x20-\x7e]", values[0])):
        raise Rejected(400, "Invalid request headers.")
    return values[0]


def trusted_identity(peer, headers):
    # Use the TCP peer, never X-Forwarded-For or a user-controlled source header.
    if peer != SUPERVISOR_IP:
        raise Rejected(403, "Home Assistant Ingress is required.")
    user = one_header(headers, "X-Remote-User-Id", required=True)
    path = one_header(headers, "X-Ingress-Path", required=True)
    if not USER_PATTERN.fullmatch(user) or not PATH_PATTERN.fullmatch(path):
        raise Rejected(403, "An authenticated Home Assistant user is required.")
    return user, path.rstrip("/")


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, settings, *, context=None):
        self.settings = settings
        self.tls = context or ssl.create_default_context()
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, ProxyHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Tracebacks and request logs can contain authenticated ingress paths.
        print("Ingress request failed.", file=sys.stderr)


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "HargassnerIngress"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(SOCKET_TIMEOUT)

    def log_message(self, *_args):
        pass

    def reply(self, status, body, headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        # HEAD has no body to measure, and 204/304 must not advertise a
        # synthetic zero-length representation. Connection close frames them.
        if self.command != "HEAD" and status not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def error(self, status, message):
        self.reply(status, (message + "\n").encode(), (
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ))

    def proxy(self):
        upstream = None
        try:
            user, prefix = trusted_identity(self.client_address[0], self.headers)
            target = self.requestline.split()[1]
            parsed = urlsplit(target)
            if (not target.startswith("/") or target.startswith("//")
                    or parsed.scheme or parsed.netloc or parsed.fragment
                    or re.search(r"[\\\x00-\x20\x7f]", target)):
                raise Rejected(400, "Invalid request path.")
            if self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Expect"):
                raise Rejected(400, "An explicit bounded request body is required.")
            length = one_header(self.headers, "Content-Length")
            if length is not None and not re.fullmatch(r"[0-9]{1,9}", length):
                raise Rejected(400, "Invalid request body length.")
            size = int(length or 0)
            if size > MAX_BODY:
                raise Rejected(413, "Request body is too large.")
            headers = {}
            for name in REQUEST_HEADERS:
                value = one_header(self.headers, name)
                if value is not None:
                    headers[name] = value
            body = self.rfile.read(size)
            if len(body) != size:
                raise Rejected(400, "Incomplete request body.")
            settings = self.server.settings
            # A fresh allowlist discards Cookie, Authorization, Forwarded and
            # every supplied ingress/authentication header before adding ours.
            headers.update({
                "Host": settings.authority,
                "Content-Length": str(size),
                "Accept-Encoding": "identity",
                "Connection": "close",
                "X-Hargassner-Ingress": settings.token,
                "X-Hargassner-User": user,
                "X-Ingress-Path": prefix,
            })
            upstream = http.client.HTTPSConnection(
                settings.host, settings.port, timeout=SOCKET_TIMEOUT,
                context=self.server.tls,
            )
            upstream.request(self.command, target, body=body, headers=headers)
            response = upstream.getresponse()
            redirect = None
            if 300 <= response.status < 400 and response.status != 304:
                redirect = one_header(response.headers, "Location", required=True)
                # The trusted backend may redirect its /login route back to the
                # ingress root. No upstream origin, credential or other route
                # may escape the authenticated Home Assistant frame.
                if (response.status not in (301, 302, 303, 307, 308)
                        or redirect != prefix + "/"):
                    raise Rejected(502, "The dashboard returned an unexpected redirect.")
            if response.status < 200:
                raise Rejected(502, "The dashboard returned an unsupported response.")
            content = response.read(MAX_RESPONSE + 1)
            if len(content) > MAX_RESPONSE:
                raise Rejected(502, "The dashboard response is too large.")
            response_headers = [
                (name, value) for name, value in response.getheaders()
                if name.lower() in RESPONSE_HEADERS
            ]
            if redirect is not None:
                response_headers.append(("Location", redirect))
            self.reply(response.status, content, response_headers)
        except Rejected as exc:
            self.error(exc.status, exc.message)
        except (OSError, http.client.HTTPException, ValueError):
            self.error(502, "Cannot reach the local dashboard securely.")
        finally:
            if upstream is not None:
                upstream.close()

    do_GET = proxy
    do_HEAD = proxy
    do_POST = proxy
    do_PUT = proxy
    do_PATCH = proxy
    do_DELETE = proxy
    do_OPTIONS = proxy


def main():
    try:
        server = ProxyServer(("0.0.0.0", 8099), Settings.load())
    except (OSError, ValueError):
        print("Invalid app configuration or HTTPS trust store; check the app options.", file=sys.stderr)
        return 1
    print("Home Assistant ingress proxy listening.", flush=True)
    with server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
