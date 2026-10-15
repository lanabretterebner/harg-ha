"""Synthetic app monitor/outbox tests; no HA, VM, broker or controller."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import app_monitor as m
import health_alerts as h
import run_app

NOW = 2_000_000_000.0
INGRESS = 'i' * 43
HEALTH = 'h' * 43
SUPERVISOR = 'synthetic-supervisor-token'


def report(now=NOW, certificate='healthy', telnet='healthy'):
    return {'schema_version': 1, 'sent_at': now, 'checks': {
        'certificate': {'checked_at': now, 'status': certificate},
        'telnet': {'checked_at': now, 'status': telnet}}}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.options = self.root / 'options.json'
        self.config = {'upstream_url': 'https://dashboard.example:8443', 'ingress_token': INGRESS,
                       'upstream_connect_host': '192.0.2.57', 'monitor_enabled': True, 'monitor_token': HEALTH}
        self.settings = self.load(); self.documents = report(); self.fail_get = False; self.fail_post = False; self.calls = []; self.now = NOW
        self.monitor = m.Monitor(self.settings, SUPERVISOR, self.root / 'state', network=self.network, clock=lambda: self.now)
    def tearDown(self): self.temp.cleanup()
    def load(self, **changes):
        options = {**self.config, **changes}; self.options.write_text(json.dumps(options))
        return m.MonitorSettings.load(self.options)
    def network(self, request):
        self.calls.append(request)
        if request['kind'] == 'heartbeat':
            if self.fail_get: raise h.Refusal('delivery_failed')
            return {'ok': True, 'document': self.documents}
        if self.fail_post: raise h.Refusal('delivery_failed')
        return {'ok': True}
    def state(self): return h.read_json(self.monitor.state_path, private=True)
    def posts(self): return [r for r in self.calls if r['kind'] == 'notification']
    def test_disabled_default_needs_no_monitor_token(self):
        self.config.pop('monitor_enabled'); self.config.pop('monitor_token')
        self.assertFalse(self.load().enabled)
    def test_enabled_options_are_strict_and_distinct(self):
        for changes in [{'monitor_enabled': 'yes'}, {'monitor_token': INGRESS}, {'monitor_token': 'short'},
                        {'monitor_poll_interval': True}, {'monitor_poll_interval': 14}, {'monitor_poll_interval': 61}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.load(**changes)
        self.assertEqual(self.load(monitor_poll_interval=15).interval, 15)
        self.assertEqual(self.load(monitor_poll_interval=60).interval, 60)

    def test_ingress_and_monitor_settings_come_from_one_options_snapshot(self):
        original_open = Path.open
        replacement = {**self.config, 'monitor_enabled': False, 'upstream_url': 'https://replacement.example'}
        def swap_after_read(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get('mode', 'r')
            if path == self.options and mode.startswith('r'):
                raw = stream.read(); stream.close()
                with original_open(self.options, 'w') as output: json.dump(replacement, output)
                return io.BytesIO(raw) if isinstance(raw, bytes) else io.StringIO(raw)
            return stream
        with patch.object(Path, 'open', new=swap_after_read):
            settings = m.MonitorSettings.load(self.options)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.upstream.host, 'dashboard.example')
        self.assertEqual(settings.token, HEALTH)
    def test_secrets_not_in_settings_repr(self):
        self.assertNotIn(HEALTH, repr(self.settings)); self.assertNotIn(INGRESS, repr(self.settings))
    def test_supervisor_token_env_validation(self):
        self.assertEqual(m.supervisor_token({'SUPERVISOR_TOKEN': SUPERVISOR}), SUPERVISOR)
        for token in [None, '', 'a\nb', 'a' * 8193]:
            with self.subTest(token_type=type(token).__name__), self.assertRaises(ValueError): m.supervisor_token({'SUPERVISOR_TOKEN': token})
    def test_first_healthy_baseline_and_repeated_timestamps_do_not_notify(self):
        self.monitor.poll_once(); self.now += 30; self.documents = report(self.now); self.monitor.poll_once()
        self.assertEqual(self.posts(), []); self.assertEqual(self.state()['queue'], [])
        self.assertEqual(stat.S_IMODE(self.monitor.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.monitor.state_path.stat().st_mode), 0o600)
    def test_heartbeat_request_never_contains_other_credentials(self):
        self.monitor.poll_once(); request = self.calls[0]
        self.assertEqual(request['kind'], 'heartbeat'); self.assertEqual(request['token'], HEALTH)
        self.assertEqual(request['connect_host'], '192.0.2.57'); self.assertNotIn(INGRESS, json.dumps(request)); self.assertNotIn(SUPERVISOR, json.dumps(request))
    def test_missing_vm_is_unknown_and_one_delivery_per_poll(self):
        self.fail_get = True
        for _ in range(3):
            result = self.monitor.poll_once(); self.assertEqual(result['delivered'], 1)
        self.assertEqual(len(self.posts()), 3); self.assertEqual(self.state()['queue'], [])
        self.assertTrue(all(r['reason'] == 'endpoint_unavailable' for r in self.state()['conditions'].values()))
        self.monitor.poll_once(); self.assertEqual(len(self.posts()), 3)
    def test_failed_delivery_retains_durable_original_event_across_restart(self):
        self.fail_get = self.fail_post = True; first = self.monitor.poll_once(); self.assertEqual(first['delivered'], 0)
        event = self.state()['queue'][0]
        self.fail_post = False; restored = m.Monitor(self.settings, SUPERVISOR, self.root / 'state', network=self.network, clock=lambda: self.now)
        restored.poll_once(); self.assertEqual(self.posts()[-1]['event'], event)
        self.assertEqual(self.state()['sequence'], 3)
    def test_recovery_is_ordered_after_fault_and_deduplicated(self):
        self.fail_get = True; self.fail_post = True; self.monitor.poll_once()
        self.fail_get = self.fail_post = False; self.now += 30; self.documents = report(self.now)
        for _ in range(6): self.monitor.poll_once()
        events = [r['event'] for r in self.posts()][1:]
        self.assertEqual([r['transition'] for r in events], ['alert'] * 3 + ['recovery'] * 3)
        self.assertEqual(len({r['event_id'] for r in events}), 6)
        self.assertEqual(self.state()['queue'], [])
    def test_fresh_heartbeat_does_not_renew_stale_checker_runs(self):
        self.documents['checks']['certificate']['checked_at'] = NOW - 90001
        self.documents['checks']['telnet']['checked_at'] = NOW - 46
        self.monitor.poll_once(); self.now += 30; self.documents['sent_at'] = self.now; self.monitor.poll_once()
        self.assertEqual(self.state()['sequence'], 2)
        self.assertEqual(self.state()['conditions']['telnet']['reason'], 'run_stale')
        self.assertEqual(self.state()['conditions']['certificate']['observed_at'], NOW - 90001)
    def test_invalid_payload_is_unknown_not_healthy(self):
        self.documents = {'healthy': True, 'private': 'not copied'}; self.monitor.poll_once()
        self.assertTrue(all(r['status'] == 'unknown' for r in self.state()['conditions'].values()))
        self.assertNotIn('private', json.dumps(self.state()))
    def test_maintenance_suppression_and_stable_component_id_contract(self):
        self.documents = report(telnet='maintenance'); self.monitor.poll_once()
        event = self.posts()[0]['event']; self.assertEqual(event['component'], 'telnet'); self.assertEqual(event['transition'], 'suppression')
    def test_corrupt_state_is_preserved_and_never_delivered(self):
        self.monitor.state_path.write_bytes(b'{broken'); self.monitor.state_path.chmod(0o600)
        with self.assertRaises(h.Refusal): self.monitor.poll_once()
        self.assertEqual(self.monitor.state_path.read_bytes(), b'{broken'); self.assertEqual(self.posts(), [])
    def test_state_lock_refuses_concurrent_poller(self):
        with h.state_lock(self.monitor.state_path), self.assertRaises(h.Refusal): self.monitor.poll_once()
        self.assertEqual(self.posts(), [])
    def test_private_directory_unsafe_mode_or_symlink_refused(self):
        path = self.root / 'bad'; path.mkdir(mode=0o755)
        with self.assertRaises(h.Refusal): m.private_directory(path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        link = self.root / 'link'; link.symlink_to(self.monitor.directory, target_is_directory=True)
        with self.assertRaises(h.Refusal): m.private_directory(link)
    def test_state_fifo_symlink_and_hardlink_refused_without_block(self):
        for kind in ['fifo', 'link', 'hardlink']:
            path = self.monitor.state_path
            if path.exists() or path.is_symlink(): path.unlink()
            target = self.root / ('target-' + kind); target.write_text('{}'); target.chmod(0o600)
            if kind == 'fifo': os.mkfifo(path)
            elif kind == 'link': path.symlink_to(target)
            else: os.link(target, path)
            with self.subTest(kind=kind), self.assertRaises(h.Refusal): self.monitor.poll_once()
            path.unlink()
        self.assertEqual(self.posts(), [])
    def test_clock_rollback_does_not_claim_recovery(self):
        self.monitor.poll_once(); self.now -= 1; self.documents = report(self.now); self.monitor.poll_once()
        self.assertTrue(all(r['reason'] == 'clock_invalid' for r in self.state()['conditions'].values()))
    def test_shutdown_after_get_leaves_outbox_without_post(self):
        stop = threading.Event(); self.fail_get = True
        def network(request): stop.set(); return self.network(request)
        self.monitor.network = network; self.monitor.poll_once(stop)
        self.assertEqual(self.posts(), []); self.assertEqual(len(self.state()['queue']), 3)
    def test_one_bounded_iteration_uses_stop_wait_not_busy_retry(self):
        owner = self
        class Stop:
            calls = 0
            def is_set(self): return self.calls > 0
            def wait(self, value): owner.assertEqual(value, 30); self.calls += 1
        with redirect_stdout(io.StringIO()): self.monitor.run(Stop())
        self.assertEqual(len(self.calls), 1)
    def test_sanitized_status_excludes_credentials_and_safe_delivery_condition(self):
        self.fail_get = True; self.monitor.poll_once(); status = self.monitor.status()
        self.assertEqual(status['queue_count'], 2); self.assertEqual(status['last_check_at'], NOW)
        self.assertEqual(status['last_delivery_condition']['status'], 'unknown')
        self.assertEqual(set(status), {'schema_version', 'enabled', 'running', 'last_check_at', 'queue_count', 'last_delivery_condition', 'last_error'})
        for secret in [INGRESS, HEALTH, SUPERVISOR, self.settings.upstream.host]: self.assertNotIn(secret, json.dumps(status))
    def full_outbox(self):
        row = h.transition({kind: h.result('unknown', 'run_missing') for kind in h.KINDS}, h.initial_state(), NOW)
        template = row['queue'][0]; row['queue'] = [{**template, 'event_id': f'{i:064x}'} for i in range(64)]
        h.atomic_json(self.monitor.state_path, row)
        return row
    def test_full_outbox_drains_one_per_poll_and_then_records_current_conditions(self):
        row = self.full_outbox()
        for index in range(3):
            result = self.monitor.poll_once()
            self.assertEqual(result['delivered'], 1)
            self.assertEqual(len(self.posts()), index + 1)
            self.assertEqual(self.posts()[-1]['event'], row['queue'][index])
            if index < 2:
                self.assertTrue(all(item['status'] == 'unknown' for item in self.state()['conditions'].values()))
        self.assertTrue(all(item['status'] == 'healthy' for item in self.state()['conditions'].values()))
        self.assertEqual(len(self.state()['queue']), 64)
        for _ in range(64): self.monitor.poll_once()
        self.assertEqual(self.state()['queue'], [])
        self.assertEqual(len({item['event']['event_id'] for item in self.posts()}), 67)
    def test_full_outbox_failed_delivery_keeps_every_original_event(self):
        self.full_outbox(); before = self.monitor.state_path.read_bytes(); self.fail_post = True
        result = self.monitor.poll_once()
        self.assertEqual(result['error'], 'delivery_failed')
        self.assertEqual(self.monitor.state_path.read_bytes(), before)
        self.assertEqual(len(self.posts()), 1)
    def test_full_outbox_shutdown_does_not_send_or_discard(self):
        self.full_outbox(); before = self.monitor.state_path.read_bytes(); stop = threading.Event(); stop.set()
        self.assertEqual(self.monitor.poll_once(stop)['delivered'], 0)
        self.assertEqual(self.monitor.state_path.read_bytes(), before)
        self.assertEqual(self.posts(), [])
    def test_child_transport_is_wall_bounded_and_environment_strips_credentials(self):
        with patch.object(m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic', 5)) as run:
            with self.assertRaises(h.Refusal): m.bounded_request({'kind': 'heartbeat'})
        self.assertEqual(run.call_args.kwargs['timeout'], 5)
        self.assertNotIn('SUPERVISOR_TOKEN', run.call_args.kwargs['env'])
        self.assertNotIn('shell', run.call_args.kwargs)


class RunnerTests(unittest.TestCase):
    def test_disabled_proxy_shutdown_never_reads_supervisor_token_or_monitor_state(self):
        stop_server = threading.Event()
        class Server:
            closed = False
            def __init__(self, *_): self.__class__.instance = self
            def serve_forever(self): stop_server.wait(1)
            def shutdown(self): stop_server.set()
            def server_close(self): self.closed = True
        def install(_name, callback): threading.Timer(.03, callback).start()
        disabled = m.MonitorSettings(False, m.Settings('dashboard.example', 443, 'dashboard.example', INGRESS))
        with patch.object(run_app.MonitorSettings, 'load', return_value=disabled), patch.object(run_app, 'MonitorProxyServer', Server), patch.object(run_app, 'Monitor') as monitor, patch.object(run_app, 'supervisor_token') as token, patch.object(run_app.signal, 'signal', side_effect=install), redirect_stdout(io.StringIO()):
            self.assertEqual(run_app.main(), 0)
        monitor.assert_not_called(); token.assert_not_called(); self.assertTrue(Server.instance.closed)


    def test_enabled_worker_and_proxy_stop_together(self):
        proxy_stop = threading.Event(); monitor_stopped = threading.Event()
        class Server:
            def __init__(self, *_): pass
            def serve_forever(self): proxy_stop.wait(1)
            def shutdown(self): proxy_stop.set()
            def server_close(self): pass
        class Worker:
            def __init__(self, settings, token):
                if token != SUPERVISOR: raise AssertionError('Wrong synthetic token')
            def status(self): return run_app.disabled_status()
            def run(self, stop): stop.wait(1); monitor_stopped.set()
        enabled = m.MonitorSettings(True, m.Settings('dashboard.example', 443, 'dashboard.example', INGRESS), HEALTH)
        def install(_name, callback): threading.Timer(.03, callback).start()
        with patch.object(run_app.MonitorSettings, 'load', return_value=enabled), patch.object(run_app, 'MonitorProxyServer', Server), patch.object(run_app, 'Monitor', Worker), patch.object(run_app, 'supervisor_token', return_value=SUPERVISOR), patch.object(run_app.signal, 'signal', side_effect=install), redirect_stdout(io.StringIO()):
            self.assertEqual(run_app.main(), 0)
        self.assertTrue(proxy_stop.is_set()); self.assertTrue(monitor_stopped.is_set())


if __name__ == '__main__': unittest.main()
