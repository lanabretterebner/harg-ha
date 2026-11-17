"""Run ingress, the optional VM-health monitor and the house monitor with owned shutdown.

The house monitor reads the configured room sensors through HA Core, receives
the VM context through one Core WebSocket subscription and reports through
Core's mqtt.publish. An invalid house configuration never stops ingress or the
VM-health monitor. The room page (room_picker.py) lists the temperature sensors
and lets a Home Assistant administrator change the app's own room options; the
running house monitor takes them over without a restart.
"""
import json
import re
import secrets
import signal
import threading
from ingress_proxy import ProxyServer, ProxyHandler, trusted_identity, one_header, Rejected
from app_monitor import MonitorSettings, Monitor, supervisor_token
from house_monitor import HouseMonitor, load_house_options
from room_picker import RoomPicker, SAVE_BODY_CAP, TOKEN_HEADER, same_origin, announce_room_page

STATUS_PATH = '/api/local-monitor/status'
ROOMS_PATH = '/api/local-monitor/raumsensoren'
# Inline style and script carry a fresh nonce per response; nothing external
# loads, the only connection is the page's own same-origin save.
PAGE_POLICY = ("default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


class MonitorProxyHandler(ProxyHandler):
    def proxy(self):
        target = self.requestline.split()[1]
        route = target.split('?', 1)[0]
        if route not in (STATUS_PATH, ROOMS_PATH):
            return super().proxy()
        try:
            user, prefix = trusted_identity(self.client_address[0], self.headers)
            if route == ROOMS_PATH and self.command == 'POST':
                return self.save_rooms(target, route, user, prefix)
            if self.command != 'GET':
                raise Rejected(405, 'Read-only status endpoint.')
            if target != route or self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Expect'):
                raise Rejected(400, 'Invalid status request.')
            if one_header(self.headers, 'Content-Length') not in (None, '0'):
                raise Rejected(400, 'A status body is not allowed.')
            if route == STATUS_PATH:
                document = self.server.monitor_status()
                self.reply(200, json.dumps(document, allow_nan=False).encode(), (
                    ('Content-Type', 'application/json'), ('Cache-Control', 'no-store'),
                    ('X-Content-Type-Options', 'nosniff')))
                return
            nonce = secrets.token_urlsafe(18)
            try:
                body = self.server.house_page(user, nonce)
            except Exception:
                # Fixed text only; the house monitor never affects ingress.
                self.server.diagnostics.emit('internal')
                raise Rejected(503, 'Room monitor status is not available.') from None
            self.reply(200, body, (
                ('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store'),
                ('Content-Security-Policy', PAGE_POLICY.format(nonce=nonce)),
                ('Referrer-Policy', 'same-origin'),
                ('X-Content-Type-Options', 'nosniff')))
        except Rejected as error:
            self.error(error.status, error.message)

    def save_rooms(self, target, route, user, prefix):
        """The only write: the app's own room options. JSON in, JSON out."""
        def answer(status, document):
            self.reply(status, json.dumps(document, ensure_ascii=False, allow_nan=False).encode('utf-8'), (
                ('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store'),
                ('X-Content-Type-Options', 'nosniff')))

        def refuse(status, message):
            answer(status, {'ok': False, 'message': message})
        if target != route or self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Expect'):
            return refuse(400, 'Ungültige Anfrage. Es wurde nichts gespeichert.')
        length = one_header(self.headers, 'Content-Length')
        if length is None or not re.fullmatch(r'[0-9]{1,9}', length):
            return refuse(411, 'Ungültige Anfrage. Es wurde nichts gespeichert.')
        if int(length) > SAVE_BODY_CAP:
            return refuse(413, 'Die Anfrage ist zu groß. Es wurde nichts gespeichert.')
        # The bounded body is read before any refusal; reading changes nothing.
        body = self.rfile.read(int(length))
        if len(body) != int(length):
            return refuse(400, 'Unvollständige Anfrage. Es wurde nichts gespeichert.')
        content_type = one_header(self.headers, 'Content-Type')
        if content_type is None or content_type.split(';', 1)[0].strip().lower() != 'application/json':
            return refuse(415, 'Nur JSON wird angenommen. Es wurde nichts gespeichert.')
        if not same_origin(self.headers, prefix):
            return refuse(403, 'Die Anfrage kommt nicht von dieser Seite. Es wurde nichts gespeichert.')
        token = one_header(self.headers, TOKEN_HEADER)
        if self.server.house_save is None:
            return refuse(503, 'Die Raumüberwachung ist gerade nicht verfügbar. Es wurde nichts gespeichert.')
        try:
            status, document = self.server.house_save(user, token, body)
        except Exception:
            self.server.diagnostics.emit('internal')
            return refuse(500, 'Interner Fehler der App. Es wurde nichts gespeichert.')
        answer(status, document)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = proxy


class MonitorProxyServer(ProxyServer):
    def __init__(self, address, settings, status, house_page=None, house_save=None, **kwargs):
        self.monitor_status = status
        self.house_page = house_page
        self.house_save = house_save
        super().__init__(address, settings, **kwargs)
        self.RequestHandlerClass = MonitorProxyHandler


def disabled_status():
    return {'schema_version': 1, 'enabled': False, 'running': False,
            'last_check_at': None, 'queue_count': None,
            'last_delivery_condition': None, 'last_error': None}


def start_house_monitor():
    """Never raises: ingress keeps running without the house monitor."""
    try:
        return HouseMonitor(load_house_options())
    except Exception:
        print('House monitor: internal.', flush=True)
        return None


def main():
    stop = threading.Event()
    server = None
    workers = []
    unexpected_stop = False
    stage = 'options'
    try:
        settings = MonitorSettings.load()
        # Disabled VM-health monitoring never reads Supervisor credentials or
        # its state. The house monitor reads the token lazily in its own thread.
        monitor = None
        if settings.enabled:
            stage = 'supervisor_token'
            token = supervisor_token()
            stage = 'monitor_state'
            monitor = Monitor(settings, token)
        house = start_house_monitor()
        picker = RoomPicker(house) if house is not None else None
        stage = 'ingress_listener'
        server = MonitorProxyServer(('0.0.0.0', 8099), settings.upstream, monitor.status if monitor else disabled_status,
                                    picker.page if picker else None, picker.save if picker else None)
        stage = 'signal_handlers'
        for name in ('SIGTERM', 'SIGINT'):
            signal.signal(getattr(signal, name), lambda *_args: stop.set())
        stage = 'worker_start'
        proxy = threading.Thread(target=server.serve_forever, name='ingress', daemon=False)
        workers.append(proxy); proxy.start()
        if monitor is not None:
            worker = threading.Thread(target=monitor.run, args=(stop,), name='health-monitor', daemon=False)
            workers.append(worker); worker.start()
        if house is not None:
            # Nothing to finish on shutdown: the house monitor stores nothing, so a
            # WebSocket connect still waiting must not delay the app's exit.
            worker = threading.Thread(target=house.run, args=(stop,), name='house-monitor', daemon=True)
            workers.append(worker); worker.start()
        print('Home Assistant ingress proxy listening.', flush=True)
        if picker is not None:
            # Best effort and off the main thread: the address is a convenience only.
            threading.Thread(target=announce_room_page, name='room-page-address', daemon=True).start()
        while not stop.wait(.5):
            if not all(worker.is_alive() for worker in workers):
                unexpected_stop = True
                stop.set()
    except Exception:
        # Only this fixed stage is logged; exception text can contain credentials.
        print(f'App startup unavailable: {stage}.', flush=True)
        return 1
    finally:
        stop.set()
        if server is not None:
            # shutdown requires its accept loop to have started.
            if workers and workers[0].is_alive(): server.shutdown()
            server.server_close()
        for worker in workers:
            if worker.ident is not None:
                worker.join(REQUEST_JOIN_TIMEOUT)
    return 1 if unexpected_stop else 0


REQUEST_JOIN_TIMEOUT = 8
if __name__ == '__main__':
    raise SystemExit(main())
