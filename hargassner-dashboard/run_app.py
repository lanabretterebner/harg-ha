"""Run ingress and optional independent monitor with owned shutdown."""
import json
import signal
import threading
from ingress_proxy import ProxyServer, ProxyHandler, trusted_identity, one_header, Rejected
from app_monitor import MonitorSettings, Monitor, supervisor_token


class MonitorProxyHandler(ProxyHandler):
    def proxy(self):
        target = self.requestline.split()[1]
        if target.split('?', 1)[0] != '/api/local-monitor/status':
            return super().proxy()
        try:
            trusted_identity(self.client_address[0], self.headers)
            if self.command != 'GET':
                raise Rejected(405, 'Read-only status endpoint.')
            if target != '/api/local-monitor/status' or self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Expect'):
                raise Rejected(400, 'Invalid status request.')
            if one_header(self.headers, 'Content-Length') not in (None, '0'):
                raise Rejected(400, 'A status body is not allowed.')
            document = self.server.monitor_status()
            self.reply(200, json.dumps(document, allow_nan=False).encode(), (
                ('Content-Type', 'application/json'), ('Cache-Control', 'no-store'),
                ('X-Content-Type-Options', 'nosniff')))
        except Rejected as error:
            self.error(error.status, error.message)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = proxy


class MonitorProxyServer(ProxyServer):
    def __init__(self, address, settings, status, **kwargs):
        self.monitor_status = status
        super().__init__(address, settings, **kwargs)
        self.RequestHandlerClass = MonitorProxyHandler


def disabled_status():
    return {'schema_version': 1, 'enabled': False, 'running': False,
            'last_check_at': None, 'queue_count': None,
            'last_delivery_condition': None, 'last_error': None}


def main():
    stop = threading.Event()
    server = None
    workers = []
    unexpected_stop = False
    try:
        settings = MonitorSettings.load()
        # Disabled monitoring never reads Supervisor credentials or state.
        monitor = Monitor(settings, supervisor_token()) if settings.enabled else None
        server = MonitorProxyServer(('0.0.0.0', 8099), settings.upstream, monitor.status if monitor else disabled_status)
        for name in ('SIGTERM', 'SIGINT'):
            signal.signal(getattr(signal, name), lambda *_args: stop.set())
        proxy = threading.Thread(target=server.serve_forever, name='ingress', daemon=False)
        workers.append(proxy); proxy.start()
        if monitor is not None:
            worker = threading.Thread(target=monitor.run, args=(stop,), name='health-monitor', daemon=False)
            workers.append(worker); worker.start()
        print('Home Assistant ingress proxy listening.', flush=True)
        while not stop.wait(.5):
            if not all(worker.is_alive() for worker in workers):
                unexpected_stop = True
                stop.set()
    except Exception:
        print('App configuration or service unavailable.', flush=True)
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
