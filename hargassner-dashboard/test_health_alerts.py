"""Synthetic health/outbox contracts and actual loopback-only HTTPS delivery."""
from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import health_alerts as h

NOW = 2000000000.0
# Bytes as app 1.5.3 persisted them after its clock was corrected backward by an
# hour: the stamp is still an hour ahead, every component is held at
# clock_invalid and one of those three alerts is not yet delivered.
STATE_1_5_3 = (b'{"conditions":{"certificate":{"observed_at":null,"reason":"clock_invalid","status":"unknown"},'
               b'"heartbeat":{"observed_at":null,"reason":"clock_invalid","status":"unknown"},'
               b'"telnet":{"observed_at":null,"reason":"clock_invalid","status":"unknown"}},'
               b'"last_evaluated_at":2000003600.0,"queue":[{"component":"telnet","evaluated_at":2000000000.0,'
               b'"event_id":"344d350d2b3ba6066c87fa10bba986e17bfc3833540fa4eb4d6f29d24529d2ff","observed_at":null,'
               b'"reason":"clock_invalid","schema_version":1,"status":"unknown","transition":"alert"}],'
               b'"schema_version":1,"sequence":4}')


def heartbeat(now=NOW, certificate='healthy', telnet='healthy'):
    return {'schema_version': 1, 'sent_at': now, 'checks': {
        'certificate': {'checked_at': now, 'status': certificate},
        'telnet': {'checked_at': now, 'status': telnet}}}


def unreachable(now):
    return h.evaluate(None, now=now, endpoint_failed=True)


def step(state, now, **measured):
    return h.transition(h.evaluate(heartbeat(now), now=now), state, now, **measured)


def reasons(state):
    return {kind: row['reason'] for kind, row in state['conditions'].items()}


def certificate(status='pass'):
    return {'status': status, 'exit_code': {'pass': 0, 'warn': 1, 'fail': 2}[status],
            'mode': 'endpoint', 'network_attempted': True,
            'checks': {'chain_trust': {'status': 'pass', 'method': 'verified_tls_handshake'},
                       'verified_tls_handshake': {'status': 'pass', 'version': 'TLSv1.2', 'cipher': 'ECDHE-RSA-AES128-GCM-SHA256'},
                       'hostname': {'status': 'pass'}, 'rsa_compatibility': {'status': 'pass', 'required_algorithm': 'rsaEncryption'},
                       'leaf_validity': {'status': 'pass', 'reason': 'valid'},
                       'leaf_expiry_threshold': {'status': status, 'warn_days': 30}},
            'checked_at_utc': '2033-05-18T03:33:20+00:00',
            'expected_hostname': 'PRIVATE_HOST', 'leaf': {'private_serial': 'DO_NOT_COPY'}}


def telnet(state='fresh'):
    return {'schema_version': 1, 'source': 'telnet', 'state': state,
            'alert': state not in {'fresh', 'maintenance'}, 'suppressed': state == 'maintenance',
            'checked_at': NOW, 'values': {'PRIVATE_VALUE': 1234}, 'profile_id': 'a' * 64,
            'last_observed_at': NOW, 'age_seconds': 0.0, 'state_changed_at': NOW,
            'expected_metric_count': 40, 'available_metric_count': 40 if state == 'fresh' else 0,
            'check_duration_seconds': 2.0,
            'reasons': [] if state == 'fresh' else ['maintenance_active'] if state == 'maintenance' else ['snapshot_stale']}


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state.json'
        self.config = h.settings({'webhook': {'url': 'https://example.invalid/private-token'}})
        self.addCleanup(self.temporary.cleanup)

    def write(self, name, value, mode=0o600):
        p = self.root / name
        p.write_text(json.dumps(value))
        p.chmod(mode)
        return p

    def test_webhook_header_names_are_unique_ignoring_case(self):
        with self.assertRaises(h.Refusal):
            h.settings({'webhook': {'url': 'https://example.invalid/hook',
                                   'headers': {'Authorization': 'synthetic-a', 'authorization': 'synthetic-b'}}})

    def test_truncated_get_body_is_not_accepted_as_a_complete_report(self):
        class Socket:
            def makefile(self, *_args):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\n{}')
        response = h.http.client.HTTPResponse(Socket()); response.begin()
        class Connection:
            def request(self, *_args, **_kwargs): pass
            def getresponse(self): return response
            def close(self): response.close()
        with patch.object(h.http.client, 'HTTPSConnection', return_value=Connection()), self.assertRaises(h.Refusal):
            h.network_worker({'url': 'https://example.invalid/health', 'method': 'GET', 'timeout': 1})

    def test_capture_strips_all_nonallowlisted_fields_and_preserves_times(self):
        result = h.capture(self.write('certificate', certificate()), self.write('telnet', telnet()), NOW + 10)
        self.assertEqual(result['checks']['certificate']['checked_at'], NOW)
        self.assertEqual(result['checks']['telnet']['checked_at'], NOW)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('DO_NOT_COPY', json.dumps(result))

    def test_certificate_rejects_inconsistent_required_checks_and_aggregate(self):
        required = ['hostname', 'rsa_compatibility', 'leaf_validity', 'leaf_expiry_threshold',
                    'chain_trust', 'verified_tls_handshake']
        for name in required:
            row = certificate(); row['checks'][name]['status'] = 'fail'
            with self.subTest(name=name), self.assertRaises(h.Refusal): h.normalize('certificate', row)
            row = certificate(); del row['checks'][name]
            with self.subTest(missing=name), self.assertRaises(h.Refusal): h.normalize('certificate', row)
        row = certificate('warn'); row['checks']['leaf_expiry_threshold']['status'] = 'pass'
        with self.assertRaises(h.Refusal): h.normalize('certificate', row)

    def test_certificate_complete_actual_success_warning_and_preflight_failure_shapes(self):
        self.assertEqual(h.normalize('certificate', certificate())['status'], 'healthy')
        self.assertEqual(h.normalize('certificate', certificate('warn'))['status'], 'warning')
        row = certificate('fail'); row['checks'] = {'preflight': {'status': 'fail', 'reason': 'file_or_connection_operation_failed'}}
        row['network_attempted'] = False
        self.assertEqual(h.normalize('certificate', row)['status'], 'fault')

    def test_telnet_rejects_mixed_stale_or_reasoned_fresh_report(self):
        for changes in [{'last_observed_at': NOW-500, 'age_seconds': 500},
                        {'reasons': ['snapshot_stale']}, {'age_seconds': 1},
                        {'last_observed_at': None, 'age_seconds': None},
                        {'last_observed_at': NOW+3, 'age_seconds': 0},
                        {'profile_id': None}, {'expected_metric_count': 0, 'available_metric_count': 0}]:
            row = telnet(); row.update(changes)
            with self.subTest(changes=changes), self.assertRaises(h.Refusal): h.normalize('telnet', row)

    def test_telnet_fresh_all_sensor_nulls_stays_healthy_without_fabrication(self):
        row = telnet(); row['available_metric_count'] = 0
        normalized = h.normalize('telnet', row)
        self.assertEqual(normalized, {'status': 'healthy', 'checked_at': NOW})
        self.assertNotIn('values', normalized)

    def test_telnet_accepts_source_five_second_and_two_second_future_boundaries(self):
        for observed, age in [(NOW-5, 5), (NOW+2, 0)]:
            row = telnet(); row.update(last_observed_at=observed, age_seconds=age)
            self.assertEqual(h.normalize('telnet', row)['status'], 'healthy')

    def test_telnet_fresh_holds_late_samples_within_display_window(self):
        # Incident 2026-11-22: snapshots arrived every 3-15 s. The monitor
        # reports fresh up to the 50 s display window (telnet_source.DISPLAY_AFTER).
        for age in (15.6, 30, 50):
            row = telnet(); row.update(last_observed_at=NOW-age, age_seconds=age)
            self.assertEqual(h.normalize('telnet', row)['status'], 'healthy')
        row = telnet(); row.update(last_observed_at=NOW-50.5, age_seconds=50.5)
        with self.assertRaises(h.Refusal): h.normalize('telnet', row)

    def test_telnet_maintenance_without_profile_or_prior_sample_is_suppression(self):
        row = telnet('maintenance')
        row.update(profile_id=None, expected_metric_count=0, available_metric_count=0,
                   last_observed_at=None, age_seconds=None)
        self.assertEqual(h.normalize('telnet', row)['status'], 'maintenance')
        row['reasons'] = ['snapshot_stale']
        with self.assertRaises(h.Refusal): h.normalize('telnet', row)

    def test_telnet_refuses_invalid_count_and_timing_shapes(self):
        for changes in [{'available_metric_count': 41}, {'available_metric_count': True},
                        {'expected_metric_count': 513},
                        {'expected_metric_count': False}, {'state_changed_at': float('nan')},
                        {'check_duration_seconds': -1}, {'profile_id': 'PRIVATE_PROFILE'},
                        {'age_seconds': None}, {'last_observed_at': False}, {'reasons': 'SECRET'}]:
            row = telnet(); row.update(changes)
            with self.subTest(changes=changes), self.assertRaises(h.Refusal): h.normalize('telnet', row)

    def test_contradictory_producer_capture_stays_unknown_not_healthy(self):
        c = certificate(); c['checks']['hostname']['status'] = 'fail'
        t = telnet(); t.update(last_observed_at=NOW-500, age_seconds=500)
        doc = h.capture(self.write('c', c), self.write('t', t), NOW)
        rows = h.evaluate(doc, now=NOW)
        for component in ('certificate', 'telnet'):
            self.assertEqual(rows[component]['status'], 'unknown')
            self.assertEqual(rows[component]['reason'], 'report_invalid')

    def test_capture_missing_paths_stay_unknown_without_new_run_time(self):
        result = h.capture(self.root / 'absent', None, NOW)
        self.assertIsNone(result['checks']['certificate']['checked_at'])
        self.assertIsNone(result['checks']['telnet'])

    def test_capture_does_not_refresh_frozen_runs(self):
        c = self.write('c', certificate())
        t = self.write('t', telnet())
        result = h.capture(c, t, NOW + 100)
        evaluated = h.evaluate(result, now=NOW + 100)
        self.assertEqual(evaluated['telnet']['reason'], 'run_stale')

    def test_local_pem_check_cannot_prove_served_endpoint(self):
        c = certificate(); c['mode'] = 'local_public_pem'
        with self.assertRaises(h.Refusal): h.normalize('certificate', c)

    def test_certificate_pass_requires_actual_tls_checks(self):
        for checks in [None, {}, {'chain_trust': None}, {'chain_trust': {'status': 'fail'}}]:
            c = certificate(); c['checks'] = checks
            with self.subTest(checks=checks), self.assertRaises(h.Refusal): h.normalize('certificate', c)

    def test_certificate_exit_mismatch_refused(self):
        c = certificate(); c['exit_code'] = 2
        with self.assertRaises(h.Refusal): h.normalize('certificate', c)

    def test_boolean_timestamp_refused(self):
        with self.assertRaises(h.Refusal): h.stamp(True)

    def test_naive_or_invalid_timestamp_refused(self):
        for value in ['2026-10-15T01:01:00', 'SECRET', float('nan'), -1, None]:
            with self.subTest(value=value), self.assertRaises(h.Refusal): h.stamp(value)

    def test_telnet_fresh_inconsistent_flags_refused(self):
        row = telnet(); row['alert'] = True
        with self.assertRaises(h.Refusal): h.normalize('telnet', row)

    def test_duplicate_json_keys_nan_and_huge_inputs_refused(self):
        for raw in [b'{"a":1,"a":2}', b'{"a":NaN}', b' ' * (h.MAX_DOCUMENT + 1)]:
            with self.subTest(raw=raw[:25]), self.assertRaises(h.Refusal): h.decode(raw)

    def test_symlinks_and_fifo_never_block_file_read(self):
        target = self.write('target', {})
        link = self.root / 'link'; link.symlink_to(target)
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        for p in (link, fifo):
            with self.subTest(path=p.name), self.assertRaises(h.Refusal): h.read_json(p)

    def test_private_config_mode_refused(self):
        with self.assertRaises(h.Refusal): h.read_json(self.write('public', {}, 0o644), private=True)

    def test_healthy_and_fresh_heartbeats(self):
        self.assertTrue(all(r['status'] == 'healthy' for r in h.evaluate(heartbeat(), now=NOW).values()))

    def test_missed_certificate_execution(self):
        row = heartbeat(); row['checks']['certificate']['checked_at'] -= 90001
        self.assertEqual(h.evaluate(row, now=NOW)['certificate']['reason'], 'run_stale')

    def test_missing_telnet_execution(self):
        row = heartbeat(); row['checks']['telnet'] = None
        self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], 'run_missing')

    def test_missing_heartbeat_and_stale_whole_host(self):
        self.assertEqual(h.evaluate(None, now=NOW)['heartbeat']['reason'], 'report_missing')
        self.assertEqual(h.evaluate(heartbeat(NOW - 121), now=NOW)['certificate']['reason'], 'heartbeat_stale')

    def test_future_heartbeat_invalidates_all_components(self):
        self.assertTrue(all(r['reason'] == 'run_future' for r in h.evaluate(heartbeat(NOW + 6), now=NOW).values()))

    def test_future_execution_cannot_hide_behind_current_heartbeat(self):
        row = heartbeat(); row['checks']['telnet']['checked_at'] += 6
        self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], 'run_future')

    def test_future_skew_inside_the_tolerance_is_fresh_and_beyond_it_unverified(self):
        for skew in (2.001, 3, 5):
            with self.subTest(inside=skew):
                rows = h.evaluate(heartbeat(NOW + skew), now=NOW)
                self.assertTrue(all(r['status'] == 'healthy' for r in rows.values()))
                self.assertEqual(rows['heartbeat']['observed_at'], NOW + skew)
        for skew in (5.001, 6, 3600):
            with self.subTest(outside=skew):
                self.assertTrue(all(r['reason'] == 'run_future' for r in h.evaluate(heartbeat(NOW + skew), now=NOW).values()))
        row = heartbeat(NOW - 30)
        for ahead, reason in ((5, 'ok'), (5.001, 'run_future')):
            row['checks']['telnet']['checked_at'] = NOW - 30 + ahead
            with self.subTest(check_after_its_heartbeat=ahead):
                self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], reason)

    def test_skew_hovering_at_the_tolerance_does_not_flap(self):
        # A report stamp alternating 4.9/5.1 s ahead raises run_future once and
        # holds it: one alert, no recovery/alert pair per evaluation.
        state, now = h.initial_state(), NOW
        for index, skew in enumerate((4.9, 5.1) * 6):
            now += 30
            rows = h.effective_conditions(h.evaluate(heartbeat(now + skew), now=now, saved=state), state, now)
            state = h.transition(rows, state, now)
            if index:
                self.assertEqual(set(reasons(state).values()), {'run_future'}, (index, skew))
        self.assertEqual([(e['component'], e['reason']) for e in state['queue']],
                         [(kind, 'run_future') for kind in h.KINDS])

    def test_a_skew_settling_inside_the_tolerance_releases_after_the_hold_time(self):
        # One stamp 5.1 s ahead, then a steady 4 s: held for FUTURE_HOLD_SECONDS
        # after the last over-limit evaluation, then judged again, so a real
        # fault cannot stay masked for ever.
        self.assertEqual(h.FUTURE_HOLD_SECONDS, 300)
        state = h.transition(h.evaluate(heartbeat(NOW + 5.1), now=NOW), h.initial_state(), NOW)
        self.assertEqual(state['conditions']['heartbeat']['observed_at'], NOW)
        for elapsed, reason in ((30, 'run_future'), (299, 'run_future'), (300, 'telemetry_unhealthy')):
            with self.subTest(elapsed=elapsed):
                rows = h.evaluate(heartbeat(NOW + elapsed + 4, telnet='fault'), now=NOW + elapsed, saved=state)
                self.assertEqual(rows['telnet']['reason'], reason)
                if reason == 'run_future':
                    self.assertEqual(rows['telnet']['observed_at'], NOW)  # the hold does not renew itself

    def test_each_stamp_beyond_the_tolerance_renews_the_hold(self):
        state, now = h.initial_state(), NOW
        for skew in (5.1, 4.9) * 20:  # 20 minutes hovering: one alert, no release
            now += 30
            state = h.transition(h.evaluate(heartbeat(now + skew), now=now, saved=state), state, now)
            self.assertEqual(set(reasons(state).values()), {'run_future'})
        self.assertEqual(len(state['queue']), 3)
        self.assertEqual(state['conditions']['heartbeat']['observed_at'], now - 30)

    def test_a_held_state_without_a_hold_stamp_starts_the_hold_now(self):
        # State written before the hold existed: run_future with observed_at None.
        old = {'conditions': {kind: h.result('unknown', 'run_future') for kind in h.KINDS}}
        rows = h.evaluate(heartbeat(NOW + 4), now=NOW, saved=old)
        self.assertEqual({(r['reason'], r['observed_at']) for r in rows.values()}, {('run_future', NOW)})
        state = h.transition(rows, h.initial_state(), NOW)
        self.assertEqual(h.evaluate(heartbeat(NOW + 304), now=NOW + 300, saved=state)['heartbeat']['reason'], 'ok')
        # A hold stamp ahead of the evaluator (its clock stepped back) restarts the hold too.
        ahead = {'conditions': {kind: h.result('unknown', 'run_future', NOW + 3600) for kind in h.KINDS}}
        self.assertEqual(h.evaluate(heartbeat(NOW + 4), now=NOW, saved=ahead)['heartbeat']['observed_at'], NOW)

    def test_raised_future_releases_only_at_half_the_tolerance(self):
        held = {'conditions': {kind: h.result('unknown', 'run_future') for kind in h.KINDS}}
        for skew, reason in ((4.9, 'run_future'), (2.501, 'run_future'), (2.5, 'ok'), (0, 'ok')):
            with self.subTest(skew=skew):
                rows = h.evaluate(heartbeat(NOW + skew), now=NOW, saved=held)
                self.assertEqual({row['reason'] for row in rows.values()}, {reason})
        # A configured tolerance keeps its own release point.
        for tolerance, skew, reason in ((2, 1.001, 'run_future'), (2, 1, 'ok'), (1, 0.501, 'run_future'), (1, 0.5, 'ok')):
            with self.subTest(tolerance=tolerance, skew=skew):
                rows = h.evaluate(heartbeat(NOW + skew), {'future_tolerance': tolerance}, now=NOW, saved=held)
                self.assertEqual(rows['heartbeat']['reason'], reason)
        # Without a raised finding the full tolerance applies as before.
        for saved in (None, h.initial_state(), {'conditions': {kind: h.result('healthy', 'ok', NOW) for kind in h.KINDS}}):
            self.assertEqual(h.evaluate(heartbeat(NOW + 4.9), now=NOW, saved=saved)['heartbeat']['reason'], 'ok')

    def test_a_check_ahead_of_its_report_has_its_own_release(self):
        row = heartbeat(NOW - 30); row['checks']['telnet']['checked_at'] = NOW - 30 + 4
        self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], 'ok')
        held = {'conditions': {'heartbeat': h.result('healthy', 'ok', NOW - 30),
                               'certificate': h.result('healthy', 'ok', NOW - 30),
                               'telnet': h.result('unknown', 'run_future')}}
        rows = h.evaluate(row, now=NOW, saved=held)
        self.assertEqual({kind: rows[kind]['reason'] for kind in h.KINDS},
                         {'heartbeat': 'ok', 'certificate': 'ok', 'telnet': 'run_future'})
        row['checks']['telnet']['checked_at'] = NOW - 30 + 2.5
        self.assertEqual(h.evaluate(row, now=NOW, saved=held)['telnet']['reason'], 'ok')

    def test_unreadable_saved_state_gives_no_release_margin(self):
        for saved in ('x', [], {'conditions': 'x'}, {'conditions': {'heartbeat': 'run_future'}},
                      {'conditions': {'heartbeat': {'reason': ['run_future']}}}):
            with self.subTest(saved=saved):
                self.assertEqual(h.evaluate(heartbeat(NOW + 4.9), now=NOW, saved=saved)['heartbeat']['reason'], 'ok')

    def test_run_keeps_a_raised_future_report_from_its_saved_state(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            path = Path(folder) / 'state.json'
            state = h.transition(h.evaluate(heartbeat(NOW + 5.1), now=NOW), h.initial_state(), NOW)
            h.atomic_json(path, state)
            for skew, reason in ((4.9, 'run_future'), (2.5, 'ok')):
                with self.subTest(skew=skew):
                    result = h.run(heartbeat(NOW + 30 + skew), path, {}, NOW + 30)
                    self.assertEqual(result['conditions']['heartbeat']['reason'], reason)

    def test_future_tolerance_is_small_against_every_staleness_limit_and_adds_no_age(self):
        config = h.settings({})
        self.assertEqual((config['future_tolerance'], h.FUTURE_TOLERANCE), (5, 5))
        self.assertLessEqual(9 * config['future_tolerance'], min(config[kind + '_max_age'] for kind in h.KINDS))
        self.assertEqual(h.settings({'future_tolerance': 2})['future_tolerance'], 2)
        with self.assertRaises(h.Refusal): h.settings({'future_tolerance': 5.001})
        # Review low: below 1 s synchronised hosts sit at the boundary and a
        # raised run_future had no band to release into (it flapped per poll).
        self.assertEqual(h.FUTURE_TOLERANCE_MIN, 1)
        self.assertEqual(h.settings({'future_tolerance': 1})['future_tolerance'], 1)
        for tolerance in (0, 0.999, -1):
            with self.assertRaises(h.Refusal): h.settings({'future_tolerance': tolerance})
        # Ages are never shortened by the tolerance: the limits stay exact.
        self.assertEqual(h.evaluate(heartbeat(NOW - 120.001), now=NOW)['heartbeat']['reason'], 'heartbeat_stale')
        row = heartbeat(); row['checks']['telnet']['checked_at'] = NOW - 45.001
        self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], 'run_stale')
        # A producer the full tolerance ahead delays staleness by that much and no more.
        self.assertEqual(h.evaluate(heartbeat(NOW - 120 + 5), now=NOW + 5)['heartbeat']['status'], 'healthy')
        self.assertEqual(h.evaluate(heartbeat(NOW - 120 + 5), now=NOW + 5.001)['heartbeat']['reason'], 'heartbeat_stale')

    def test_execution_cannot_postdate_old_heartbeat(self):
        row = heartbeat(NOW - 10); row['checks']['telnet']['checked_at'] = NOW
        self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], 'run_future')

    def test_time_threshold_boundaries(self):
        row = heartbeat(NOW - 120); row['checks']['telnet']['checked_at'] = NOW - 120
        self.assertEqual(h.evaluate(row, now=NOW)['heartbeat']['status'], 'healthy')
        row = heartbeat(); row['checks']['telnet']['checked_at'] = NOW - 45
        self.assertEqual(h.evaluate(row, now=NOW)['telnet']['status'], 'healthy')

    def test_invalid_clock_and_endpoint_unavailable(self):
        self.assertEqual(h.evaluate(heartbeat(), now=float('nan'))['heartbeat']['reason'], 'clock_invalid')
        self.assertEqual(h.evaluate(heartbeat(), now=NOW, endpoint_failed=True)['heartbeat']['reason'], 'endpoint_unavailable')

    def test_warn_fault_and_maintenance_propagate_without_authority(self):
        rows = h.evaluate(heartbeat(certificate='warning', telnet='maintenance'), now=NOW)
        self.assertEqual(rows['certificate']['reason'], 'certificate_expiring')
        self.assertEqual(rows['telnet']['status'], 'maintenance')
        self.assertEqual(h.evaluate(heartbeat(telnet='fault'), now=NOW)['telnet']['reason'], 'telemetry_unhealthy')

    def test_unsupported_producer_states_are_not_healthy(self):
        self.assertEqual(h.evaluate(heartbeat(certificate='maintenance'), now=NOW)['certificate']['reason'], 'report_invalid')
        self.assertEqual(h.evaluate(heartbeat(telnet='warning'), now=NOW)['telnet']['reason'], 'report_invalid')

    def test_invalid_heartbeat_schema(self):
        for row in [[], {}, {'schema_version': 1}, dict(heartbeat(), schema_version=True), dict(heartbeat(), secret='PRIVATE')]:
            with self.subTest(row=row):
                self.assertEqual(h.evaluate(row, now=NOW)['heartbeat']['reason'], 'report_invalid')

    def test_unknown_check_kind_is_ignored_and_changes_no_current_verdict(self):
        # A newer producer may report a fourth check. The evaluator does not know
        # that kind, so it neither evaluates nor reports it, and the three known
        # components keep exactly the verdict they have without it.
        extra = {'checked_at': NOW, 'status': 'fault'}
        for certificate, telnet in [('healthy', 'healthy'), ('warning', 'maintenance'),
                                    ('healthy', 'fault'), ('maintenance', 'warning')]:
            with self.subTest(certificate=certificate, telnet=telnet):
                current = h.evaluate(heartbeat(certificate=certificate, telnet=telnet), now=NOW)
                row = heartbeat(certificate=certificate, telnet=telnet)
                row['checks']['pellet_store'] = extra
                extended = h.evaluate(row, now=NOW)
                self.assertEqual(extended, current)
                self.assertEqual(set(extended), set(h.KINDS))
        # Unknown kinds are ignored whatever they contain, including junk.
        for value in [None, {}, 'SECRET', {'checked_at': NOW + 10 ** 6, 'status': 'nonsense'}]:
            with self.subTest(unknown=value):
                row = heartbeat(); row['checks']['future_kind'] = value
                self.assertEqual(h.evaluate(row, now=NOW), h.evaluate(heartbeat(), now=NOW))
        # Staleness and clock rules of the known kinds are untouched by an extra key.
        row = heartbeat(); row['checks']['future_kind'] = extra
        row['checks']['telnet']['checked_at'] = NOW - 45.001
        self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], 'run_stale')

    def test_missing_required_check_kind_stays_invalid(self):
        for missing in h.EVALUATED:
            with self.subTest(missing=missing):
                row = heartbeat(); del row['checks'][missing]
                self.assertTrue(all(r['reason'] == 'report_invalid' for r in h.evaluate(row, now=NOW).values()))
                # Still invalid when an unknown kind is present in its place.
                row['checks']['future_kind'] = {'checked_at': NOW, 'status': 'healthy'}
                self.assertTrue(all(r['reason'] == 'report_invalid' for r in h.evaluate(row, now=NOW).values()))
        row = heartbeat(); row['checks'] = {'future_kind': {'checked_at': NOW, 'status': 'healthy'}}
        self.assertTrue(all(r['reason'] == 'report_invalid' for r in h.evaluate(row, now=NOW).values()))
        self.assertEqual(h.EVALUATED, ('certificate', 'telnet'))
        self.assertTrue(set(h.EVALUATED) < set(h.KINDS))

    def test_config_refuses_headers_url_overrides_and_unknown_options(self):
        cases = [{'webhook': {'url': 'http://example.invalid'}}, {'timeout': 60},
                 {'webhook': {'url': 'https://a.invalid', 'headers': {'Host': 'other'}}},
                 {'webhook': {'url': 'https://a.invalid', 'headers': {'Authorization': 'x\r\ny'}}},
                 {'webhook': {'url': 'https://name:secret@a.invalid'}}, {'unknown': True}]
        for config in cases:
            with self.subTest(config=config), self.assertRaises(h.Refusal): h.settings(config)

    def test_no_event_on_initial_healthy_baseline(self):
        s = h.transition(h.evaluate(heartbeat(), now=NOW), h.initial_state(), NOW)
        self.assertFalse(s['queue'])

    def test_alert_dedup_and_recovery(self):
        s = h.transition(h.evaluate(heartbeat(telnet='fault'), now=NOW), h.initial_state(), NOW)
        self.assertEqual(len(s['queue']), 1)
        identity = s['queue'][0]['event_id']
        s = h.transition(h.evaluate(heartbeat(NOW + 1, telnet='fault'), now=NOW + 1), s, NOW + 1)
        self.assertEqual(len(s['queue']), 1)
        self.assertEqual(s['queue'][0]['event_id'], identity)
        s = h.transition(h.evaluate(heartbeat(NOW + 2), now=NOW + 2), s, NOW + 2)
        self.assertEqual([e['transition'] for e in s['queue']], ['alert', 'recovery'])

    def test_fault_recovery_fault_gets_distinct_event_ids(self):
        s = h.initial_state()
        for i, state in enumerate(['fault', 'healthy', 'fault']):
            s = h.transition(h.evaluate(heartbeat(NOW+i, telnet=state), now=NOW+i), s, NOW+i)
        self.assertEqual(len({e['event_id'] for e in s['queue']}), 3)

    def test_maintenance_is_suppression_not_fresh_reading(self):
        s = h.transition(h.evaluate(heartbeat(telnet='maintenance'), now=NOW), h.initial_state(), NOW)
        self.assertEqual(s['queue'][0]['transition'], 'suppression')

    def test_backward_clock_cannot_emit_recovery(self):
        s = h.transition(h.evaluate(heartbeat(telnet='fault'), now=NOW), h.initial_state(), NOW)
        s = h.transition(h.evaluate(heartbeat(NOW-1), now=NOW-1), s, NOW-1)
        self.assertTrue(all(r['reason'] == 'clock_invalid' for r in s['conditions'].values()))
        self.assertFalse(any(e['transition'] == 'recovery' for e in s['queue']))

    def test_backward_step_recovers_on_the_next_evaluation_not_when_the_old_stamp_passes(self):
        s = step(h.initial_state(), NOW + 3600)  # healthy baseline while an hour ahead
        s = step(s, NOW)
        self.assertEqual(set(reasons(s).values()), {'clock_invalid'})
        self.assertEqual(s['last_evaluated_at'], NOW)  # rebased, not kept an hour ahead
        s = step(s, NOW + 30)
        self.assertEqual(set(reasons(s).values()), {'ok'})
        self.assertEqual([e['transition'] for e in s['queue']], ['alert'] * 3 + ['recovery'] * 3)
        s = step(s, NOW + 60)
        self.assertEqual(len(s['queue']), 6)

    def test_state_written_by_1_5_3_loads_and_leaves_its_future_stamp_without_duplicates(self):
        s = h.validate_state(h.decode(STATE_1_5_3))
        pending = s['queue'][0]
        s = step(s, NOW + 30)  # 3570 s before the persisted stamp
        self.assertEqual(s['queue'], [pending])  # same condition, original event, nothing repeated
        self.assertEqual((s['last_evaluated_at'], s['sequence']), (NOW + 30, 4))
        s = step(s, NOW + 60)
        self.assertEqual(set(reasons(s).values()), {'ok'})
        self.assertEqual([e['transition'] for e in s['queue']], ['alert'] + ['recovery'] * 3)

    def test_backward_step_never_hides_or_repeats_a_real_alert(self):
        s = h.transition(unreachable(NOW + 3600), h.initial_state(), NOW + 3600)
        alerts = list(s['queue'])
        self.assertEqual(len(alerts), 3)
        for now in (NOW, NOW + 30, NOW + 60):  # the outage continues across the step
            s = h.transition(unreachable(now), s, now)
        self.assertEqual(s['queue'], alerts)
        self.assertEqual(set(reasons(s).values()), {'endpoint_unavailable'})
        # An outage that begins while the persisted stamp is still ahead alerts at once.
        s = h.transition(unreachable(NOW), step(h.initial_state(), NOW + 3600), NOW)
        self.assertEqual([(e['transition'], e['reason']) for e in s['queue']], [('alert', 'endpoint_unavailable')] * 3)
        s = h.transition(unreachable(NOW + 30), h.validate_state(h.decode(STATE_1_5_3)), NOW + 30)
        self.assertEqual([e['reason'] for e in s['queue']], ['clock_invalid'] + ['endpoint_unavailable'] * 3)
        # Only healthy and maintenance are withheld; a reported fault or stale run keeps its reason.
        row = heartbeat(telnet='fault'); row['checks']['certificate']['checked_at'] = NOW - 90001
        s = h.transition(h.evaluate(row, now=NOW), step(h.initial_state(), NOW + 3600), NOW)
        self.assertEqual(reasons(s), {'heartbeat': 'clock_invalid', 'certificate': 'run_stale', 'telnet': 'telemetry_unhealthy'})
        s = h.transition(h.evaluate(heartbeat(telnet='maintenance'), now=NOW), step(h.initial_state(), NOW + 3600), NOW)
        self.assertEqual(reasons(s)['telnet'], 'clock_invalid')
        self.assertFalse(any(e['transition'] in {'recovery', 'suppression'} for e in s['queue']))

    def test_repeated_backward_steps_do_not_multiply_notifications(self):
        s = step(h.initial_state(), NOW + 3600)
        for index in range(1, 40):
            s = step(s, NOW + 3600 - 60 * index)
        self.assertEqual([(e['transition'], e['reason']) for e in s['queue']], [('alert', 'clock_invalid')] * 3)
        self.assertEqual(s['last_evaluated_at'], NOW + 3600 - 60 * 39)

    def test_in_process_step_is_measured_against_monotonic_time(self):
        self.assertFalse(h.stepped_back(None, (NOW, 40.0)))  # another process: no evidence
        for wall, lost in ((NOW + 30, False), (NOW + 25, False), (NOW + 24.999, True), (NOW - 3570, True),
                           (NOW + 3630, False), (float('nan'), False)):
            with self.subTest(wall=wall):
                self.assertEqual(h.stepped_back((NOW, 10.0), (wall, 40.0)), lost)
        # A step shorter than the interval never shows against the persisted stamp.
        s = step(step(h.initial_state(), NOW), NOW + 10, stepped=True)
        self.assertEqual(set(reasons(s).values()), {'clock_invalid'})
        self.assertEqual(set(reasons(step(s, NOW + 40)).values()), {'ok'})

    def test_state_keeps_the_schema_and_reasons_that_1_5_3_accepts(self):
        # 1.5.3 refuses a state with any other key or reason, so a downgrade needs exactly these.
        self.assertEqual(h.REASONS, {'ok', 'report_missing', 'report_invalid', 'clock_invalid', 'heartbeat_stale',
                                     'run_missing', 'run_stale', 'run_future', 'result_failed', 'certificate_expiring',
                                     'telemetry_unhealthy', 'maintenance', 'endpoint_unavailable', 'state_invalid'})
        s = step(step(step(h.initial_state(), NOW + 3600), NOW, stepped=True), NOW + 30)
        self.assertEqual(set(s), {'schema_version', 'sequence', 'conditions', 'queue', 'last_evaluated_at'})
        self.assertEqual(s['schema_version'], 1)
        self.assertTrue(all(set(row) == {'status', 'reason', 'observed_at'} for row in s['conditions'].values()))
        self.assertTrue(all(set(event) == {'schema_version', 'event_id', 'component', 'transition', 'status',
                                           'reason', 'observed_at', 'evaluated_at'} for event in s['queue']))
        self.assertEqual(h.initial_state(), {'schema_version': 1, 'sequence': 0, 'conditions': {}, 'queue': [],
                                             'last_evaluated_at': None})

    def test_delivering_run_leaves_a_future_stamp_and_a_dry_run_never_rewrites_it(self):
        self.state.write_bytes(STATE_1_5_3); self.state.chmod(0o600)
        self.assertEqual(h.run(heartbeat(), self.state, self.config, NOW)['pending_events'], [h.decode(STATE_1_5_3)['queue'][0]])
        self.assertEqual(self.state.read_bytes(), STATE_1_5_3)
        with patch.object(h, 'delivery') as send:
            self.assertEqual(h.run(heartbeat(), self.state, self.config, NOW, True)['delivered'], 1)
            reply = h.run(heartbeat(NOW + 60), self.state, self.config, NOW + 60, True)
        self.assertEqual(reply['delivered'], 3)
        self.assertEqual([call.args[0]['transition'] for call in send.call_args_list], ['alert'] + ['recovery'] * 3)
        self.assertEqual(set(reasons(reply).values()), {'ok'})

    def test_dry_run_makes_no_files_and_sends_nothing(self):
        with patch.object(h, 'delivery', side_effect=AssertionError('must not send')):
            reply = h.run(heartbeat(telnet='fault'), self.state, self.config, NOW)
        self.assertTrue(reply['dry_run']); self.assertFalse(list(self.root.iterdir()))

    def test_dry_run_does_not_consume_real_pending_notifications(self):
        with patch.object(h, 'delivery', side_effect=h.Refusal('delivery_failed')):
            h.run(heartbeat(telnet='fault'), self.state, self.config, NOW, True)
        original = self.state.read_bytes()
        h.run(heartbeat(), self.state, self.config, NOW+1)
        self.assertEqual(self.state.read_bytes(), original)

    def test_outbox_precedes_attempt_and_failure_retains_event_id(self):
        ids = []
        def fail(event, config):
            saved = h.read_json(self.state, private=True)
            self.assertEqual(saved['queue'][0], event)
            ids.append(event['event_id'])
            raise h.Refusal('delivery_failed')
        with patch.object(h, 'delivery', side_effect=fail):
            self.assertEqual(h.run(heartbeat(telnet='fault'), self.state, self.config, NOW, True)['delivered'], 0)
            h.run(heartbeat(NOW+1, telnet='fault'), self.state, self.config, NOW+1, True)
        self.assertEqual(ids[0], ids[1])

    def test_success_persists_then_deduplicates(self):
        with patch.object(h, 'delivery') as send:
            self.assertEqual(h.run(heartbeat(telnet='fault'), self.state, self.config, NOW, True)['delivered'], 1)
            self.assertEqual(h.run(heartbeat(NOW+1, telnet='fault'), self.state, self.config, NOW+1, True)['delivered'], 0)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_corrupt_state_refuses_before_any_delivery(self):
        self.state.write_text('{'); self.state.chmod(0o600)
        with patch.object(h, 'delivery') as send, self.assertRaises(h.Refusal):
            h.run(heartbeat(), self.state, self.config, NOW, True)
        send.assert_not_called()

    def test_state_symlink_refuses_without_modifying_target(self):
        target = self.write('original', h.initial_state()); original = target.read_bytes()
        self.state.symlink_to(target)
        with self.assertRaises(h.Refusal): h.run(heartbeat(), self.state, self.config, NOW, True)
        self.assertEqual(target.read_bytes(), original)

    def test_parallel_delivery_state_lock_refused(self):
        with h.state_lock(self.state), self.assertRaises(h.Refusal):
            h.run(heartbeat(), self.state, self.config, NOW, True)

    def test_queue_capacity_refuses_without_discarding_events(self):
        state = h.initial_state()
        for i in range(h.MAX_QUEUE):
            state = h.transition(h.evaluate(heartbeat(NOW+i, telnet='fault' if i%2 == 0 else 'healthy'), now=NOW+i), state, NOW+i)
        original = copy.deepcopy(state)
        with self.assertRaises(h.Refusal):
            h.transition(h.evaluate(heartbeat(NOW+100, telnet='fault'), now=NOW+100), state, NOW+100)
        self.assertEqual(state, original)

    def full_outbox(self):
        state = h.initial_state()
        for index in range(h.MAX_QUEUE):
            state = h.transition(h.evaluate(heartbeat(NOW + index, telnet='fault' if index % 2 == 0 else 'healthy'), now=NOW + index),
                                 state, NOW + index)
        h.atomic_json(self.state, state)
        return state

    def test_full_outbox_drains_before_admitting_the_next_transition(self):
        saved = self.full_outbox()
        delivered = []
        def send(event, config):
            self.assertEqual(h.read_json(self.state, private=True)['queue'][0], event)
            delivered.append(event['event_id'])
        with patch.object(h, 'delivery', side_effect=send):
            result = h.run(heartbeat(NOW + 64, telnet='fault'), self.state, self.config, NOW + 64, True)
        self.assertEqual(delivered[:64], [event['event_id'] for event in saved['queue']])
        self.assertEqual(len(delivered), 65)
        self.assertEqual(result['pending_count'], 0)
        self.assertEqual(h.read_json(self.state, private=True)['conditions']['telnet']['status'], 'fault')

    def test_full_outbox_failed_delivery_preserves_the_existing_queue(self):
        self.full_outbox(); before = self.state.read_bytes()
        with patch.object(h, 'delivery', side_effect=h.Refusal('delivery_failed')) as send:
            result = h.run(heartbeat(NOW + 64, telnet='fault'), self.state, self.config, NOW + 64, True)
        send.assert_called_once()
        self.assertEqual(result['error'], 'delivery_failed')
        self.assertEqual(self.state.read_bytes(), before)

    def test_full_outbox_dry_run_never_drains_or_rewrites_state(self):
        self.full_outbox(); before = self.state.read_bytes()
        with patch.object(h, 'delivery') as send, self.assertRaises(h.Refusal):
            h.run(heartbeat(NOW + 64, telnet='fault'), self.state, self.config, NOW + 64)
        send.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)

    def test_destination_required_for_explicit_delivery(self):
        with self.assertRaises(h.Refusal): h.run(heartbeat(), self.state, {}, NOW, True)
        self.assertFalse(self.state.exists())

    def test_format_adapters_contain_only_sanitized_event_text(self):
        event = h.transition(h.evaluate(heartbeat(telnet='fault'), now=NOW), h.initial_state(), NOW)['queue'][0]
        for format_, field in [('generic', 'event_id'), ('slack', 'text'), ('discord', 'content'), ('ntfy', None)]:
            c = h.settings({'webhook': {'url': 'https://example.invalid/topic', 'format': format_}})
            with self.subTest(format_=format_), patch.object(h, 'request_https') as send:
                h.delivery(event, c)
                body = send.call_args.args[4]
                self.assertIn(event['event_id'], body)
                if field: self.assertIn(field, json.loads(body))
                self.assertNotIn('example.invalid', body)

    def test_webhook_text_is_german(self):
        # Review 2026-11-23: the chat formats said "Hargassner health: telnet alert (...)".
        faulted = h.transition(h.evaluate(heartbeat(telnet='fault'), now=NOW), h.initial_state(), NOW)
        recovered = h.transition(h.evaluate(heartbeat(now=NOW + 30), now=NOW + 30), faulted, NOW + 30)
        for event, expected in ((faulted['queue'][0], 'Heizung: Messwerte prüfen. '),
                                (recovered['queue'][-1], 'Heizung: Messwerte wieder in Ordnung.')):
            c = h.settings({'webhook': {'url': 'https://example.invalid/topic', 'format': 'slack'}})
            with self.subTest(transition=event['transition']), patch.object(h, 'request_https') as send:
                h.delivery(event, c)
                text = json.loads(send.call_args.args[4])['text']
                self.assertTrue(text.startswith(expected), text)
                self.assertTrue(text.endswith('(Ereignis ' + event['event_id'] + ')'))
                self.assertNotRegex(text, r'Hargassner health|alert|recovery|[\u2010-\u2015]')

    def test_malformed_container_status_values_are_refused(self):
        for value in ([], {}, None, True):
            for kind, row, field in [('certificate', certificate(), 'status'), ('telnet', telnet(), 'state')]:
                row[field] = value
                with self.subTest(kind=kind, value=value), self.assertRaises(h.Refusal):
                    h.normalize(kind, row)
            row = heartbeat(); row['checks']['telnet']['status'] = value
            self.assertEqual(h.evaluate(row, now=NOW)['telnet']['reason'], 'report_invalid')

    def test_writable_shared_state_directory_refused(self):
        self.root.chmod(0o777)
        try:
            with self.assertRaises(h.Refusal):
                h.run(heartbeat(), self.state, self.config, NOW, True)
            self.assertFalse(self.state.exists())
        finally:
            self.root.chmod(0o700)

    def test_symlink_parent_refused_without_state_creation(self):
        real = self.root/'real'; real.mkdir(mode=0o700)
        link = self.root/'alias'; link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(h.Refusal):
            h.run(heartbeat(), link/'state', self.config, NOW, True)
        self.assertFalse((real/'state').exists())

    def test_report_input_is_unchanged_by_explicit_delivery(self):
        path = self.write('report', heartbeat(telnet='fault'))
        original = path.read_bytes()
        with patch.object(h, 'delivery'):
            h.run(h.read_json(path), self.state, self.config, NOW, True)
        self.assertEqual(path.read_bytes(), original)

    def test_malformed_state_cannot_inject_secret_notification(self):
        saved = h.initial_state(); saved['conditions'] = {'telnet': {'status': 'fault', 'reason': 'SECRET', 'observed_at': NOW}}
        self.write('state.json', saved)
        with patch.object(h, 'delivery') as send, self.assertRaises(h.Refusal):
            h.run(heartbeat(), self.state, self.config, NOW, True)
        send.assert_not_called()

    def ha(self, url='https://ha.example.invalid', **options):
        token = self.root/'ha-token'; token.write_text('SYNTHETIC_PRIVATE_TOKEN\n'); token.chmod(0o600)
        return h.settings({'home_assistant': {'url': url, 'token_file': str(token), **options}})

    def test_home_assistant_dry_run_never_reads_token_or_calls_service(self):
        config = self.ha()
        with patch.object(h, 'read_token', side_effect=AssertionError('must not load token')), patch.object(h, 'delivery') as send:
            reply = h.run(heartbeat(telnet='fault'), self.state, config, NOW)
        send.assert_not_called(); self.assertTrue(reply['dry_run'])

    def test_home_assistant_fixed_service_and_stable_notification_identity(self):
        config = self.ha(); state = h.initial_state()
        for i, status in enumerate(['fault', 'healthy']):
            state = h.transition(h.evaluate(heartbeat(NOW+i, telnet=status), now=NOW+i), state, NOW+i)
        identities = []
        for index, event in enumerate(state['queue']):
            with patch.object(h, 'request_https') as send:
                h.delivery(event, config)
                args = send.call_args.args
                self.assertEqual(args[1], 'https://ha.example.invalid/api/services/persistent_notification/' + ('create' if index == 0 else 'dismiss'))
                self.assertEqual(args[3]['Authorization'], 'Bearer SYNTHETIC_PRIVATE_TOKEN')
                body = json.loads(args[4]); identities.append(body['notification_id'])
                self.assertNotIn('SYNTHETIC_PRIVATE_TOKEN', args[4])
        self.assertEqual(identities, ['harg_health_telnet', 'harg_health_telnet'])

    def test_ha_problem_copy_is_actionable_and_has_no_internal_event_identity(self):
        state = h.transition(h.evaluate(heartbeat(telnet='fault'), now=NOW), h.initial_state(), NOW)
        event = state['queue'][0]
        action, payload = h.ha_notification(event)
        self.assertEqual(action, 'create')
        self.assertIn('Messwerte', payload['title']); self.assertIn('Prüfe', payload['message'])
        self.assertNotIn(event['event_id'], json.dumps(payload))
        for status in ('healthy', 'maintenance'):
            self.assertEqual(h.ha_notification({**event, 'status': status}), ('dismiss', {'notification_id': 'harg_health_telnet'}))

    def test_common_endpoint_outage_is_one_ha_problem_but_checker_faults_remain_visible(self):
        events = h.transition(h.evaluate(None, now=NOW, endpoint_failed=True), h.initial_state(), NOW)['queue']
        self.assertEqual([h.ha_notification(event)[0] for event in events], ['create', None, None])
        for component in ('certificate', 'telnet'):
            for reason in ('run_future', 'run_stale', 'result_failed'):
                self.assertEqual(h.ha_notification({**events[0], 'component': component, 'reason': reason})[0], 'create')

    def test_ha_delivery_skips_obsolete_problem_from_a_recovered_backlog(self):
        config = self.ha()
        saved = h.transition(h.evaluate(heartbeat(telnet='fault'), now=NOW), h.initial_state(), NOW)
        h.atomic_json(self.state, saved)
        with patch.object(h, 'request_https') as send:
            reply = h.run(heartbeat(NOW + 1), self.state, config, NOW + 1, True)
        self.assertEqual(reply['pending_count'], 0)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(send.call_args.args[1].endswith('/persistent_notification/dismiss'))

    def test_individually_invalid_checks_still_create_ha_problem_notifications(self):
        for component in ('certificate', 'telnet'):
            document = heartbeat(); document['checks'][component] = {'checked_at': NOW, 'status': 'unsupported'}
            rows = h.evaluate(document, now=NOW)
            self.assertEqual(rows['heartbeat']['status'], 'healthy')
            events = h.transition(rows, h.initial_state(), NOW)['queue']
            self.assertEqual(len(events), 1)
            action, payload = h.ha_notification(events[0])
            self.assertEqual(action, 'create')
            self.assertEqual(payload['notification_id'], 'harg_health_' + component)

    def test_effective_conditions_refuses_valid_json_malformed_state(self):
        for saved in ({}, [], {'queue': []}):
            with self.subTest(saved=saved), self.assertRaisesRegex(h.Refusal, 'state_invalid'):
                h.effective_conditions(h.evaluate(heartbeat(), now=NOW), saved, NOW)

    def test_home_assistant_token_requires_private_regular_file(self):
        config = self.ha(); token = Path(config['home_assistant']['token_file'])
        token.chmod(0o644)
        with self.assertRaises(h.Refusal): h.read_token(token)
        token.chmod(0o600); link = self.root/'token-link'; link.symlink_to(token)
        with self.assertRaises(h.Refusal): h.read_token(link)
        token.write_text('INJECTION\r\nsecret')
        with self.assertRaises(h.Refusal): h.read_token(token)

    def test_home_assistant_ambiguous_token_failure_retains_outbox_no_secret(self):
        config = self.ha(); Path(config['home_assistant']['token_file']).unlink()
        reply = h.run(heartbeat(telnet='fault'), self.state, config, NOW, True)
        self.assertEqual(reply['error'], 'delivery_failed')
        self.assertEqual(reply['pending_count'], 1)
        self.assertNotIn('token', self.state.read_text())

    def test_home_assistant_http_requires_explicit_private_literal(self):
        for url in ['http://192.168.1.10:8123', 'http://127.0.0.1:8123', 'http://[::1]:8123', 'http://[fd00::1]:8123']:
            with self.subTest(url=url):
                with self.assertRaises(h.Refusal): self.ha(url)
                self.assertIn('home_assistant', self.ha(url, allow_insecure_local=True))
        for url in ['http://ha.example.invalid', 'http://localhost', 'http://8.8.8.8', 'http://169.254.169.254',
                    'http://0.0.0.0', 'http://[::]', 'http://[fe80::1]', 'http://[ff02::1]']:
            with self.subTest(url=url), self.assertRaises(h.Refusal): self.ha(url, allow_insecure_local=True)

    def test_home_assistant_refuses_inline_token_path_query_and_multiple_destinations(self):
        configs = [
            {'home_assistant': {'url': 'https://ha.invalid', 'token_file': 'private', 'token': 'SECRET'}},
            {'home_assistant': {'url': 'https://ha.invalid/api/states', 'token_file': 'private'}},
            {'home_assistant': {'url': 'https://ha.invalid?secret=x', 'token_file': 'private'}},
            {'home_assistant': {'url': 'https://ha.invalid', 'token_file': 'private', 'allow_insecure_local': 1}},
            {'home_assistant': {'url': 'https://ha.invalid', 'token_file': 'private'}, 'webhook': {'url': 'https://hook.invalid'}}]
        for config in configs:
            with self.subTest(config=config), self.assertRaises(h.Refusal): h.settings(config)

    def test_explicit_zero_port_is_rejected_instead_of_using_default(self):
        for url, local in (("https://ha.invalid:0", False), ("http://127.0.0.1:0", True)):
            with self.subTest(url=url), self.assertRaises(h.Refusal):
                h.validate_url(url, local)

    def test_unrepresentable_timestamp_is_unknown_not_an_exception(self):
        reply = h.evaluate(heartbeat(10 ** 1000), now=NOW)
        self.assertTrue(all(row['reason'] == 'report_invalid' for row in reply.values()))
        reply = h.evaluate(heartbeat(), now=10 ** 1000)
        self.assertTrue(all(row['reason'] == 'clock_invalid' for row in reply.values()))

    def test_cli_invalid_private_config_never_logs_secret(self):
        config = self.write('config', {'webhook': {'url': 'http://secret.invalid/TOKEN'}})
        result = subprocess.run([sys.executable, h.__file__, 'evaluate', '--state', str(self.state), '--config', str(config)], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b'TOKEN', result.stdout + result.stderr)
        self.assertNotIn(b'secret.invalid', result.stdout + result.stderr)


class LoopbackHTTPSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); cls.root = Path(cls.temporary.name)
        cls.key = cls.root/'key.pem'; cls.cert = cls.root/'cert.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(cls.key), '-out', str(cls.cert), '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
        cls.records = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def respond(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                cls.records.append({'method': self.command, 'path': self.path,
                                    'id': self.headers.get('Idempotency-Key'), 'authorization': self.headers.get('Authorization'), 'body': body})
                if self.path.startswith('/slow'):
                    time.sleep(1.2)
                code = 302 if self.path.startswith('/redirect') else 503 if self.path.startswith('/fail') else 200
                try:
                    self.send_response(code)
                    if code == 302: self.send_header('Location', 'https://NEVER_CONTACT.invalid/SECRET')
                    self.end_headers()
                    if self.command == 'GET': self.wfile.write(json.dumps(heartbeat()).encode())
                except OSError: pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cls.cert, cls.key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.url = f'https://localhost:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temporary.cleanup()

    def hook(self, path):
        return h.settings({'webhook': {'url': self.url+path}, 'ca_file': str(self.cert), 'timeout': 2})

    def event(self):
        return h.transition(h.evaluate(heartbeat(telnet='fault'), now=NOW), h.initial_state(), NOW)['queue'][0]

    def test_real_verified_tls_post_carries_stable_id_and_sanitized_json(self):
        event = self.event(); h.delivery(event, self.hook('/receive?secret=TOKEN'))
        record = self.records[-1]
        self.assertEqual(record['id'], event['event_id'])
        self.assertEqual(json.loads(record['body']), event)

    def test_real_home_assistant_https_notification_and_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); token = root/'token'; token.write_text('SYNTHETIC_HA_TOKEN'); token.chmod(0o600)
            config = h.settings({'home_assistant': {'url': self.url, 'token_file': str(token)}, 'ca_file': str(self.cert)})
            state = root/'state'; before = len(self.records)
            first = h.run(heartbeat(telnet='fault'), state, config, NOW, True)
            second = h.run(heartbeat(NOW+1), state, config, NOW+1, True)
            self.assertEqual((first['delivered'], second['delivered']), (1, 1))
            for index, record in enumerate(self.records[before:]):
                self.assertEqual(record['path'], '/api/services/persistent_notification/' + ('create' if index == 0 else 'dismiss'))
                self.assertEqual(record['authorization'], 'Bearer SYNTHETIC_HA_TOKEN')
                self.assertEqual(json.loads(record['body'])['notification_id'], 'harg_health_telnet')
            self.assertNotIn('SYNTHETIC_HA_TOKEN', state.read_text())

    def test_explicit_loopback_home_assistant_http_delivery(self):
        records = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                records.append((self.path, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200); self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                token = Path(temporary)/'token'; token.write_text('SYNTHETIC_HA_TOKEN'); token.chmod(0o600)
                config = h.settings({'home_assistant': {'url': f'http://127.0.0.1:{server.server_port}',
                                     'token_file': str(token), 'allow_insecure_local': True}})
                h.delivery(self.event(), config)
                self.assertEqual(records[0][0], '/api/services/persistent_notification/create')
                self.assertEqual(json.loads(records[0][1])['notification_id'], 'harg_health_telnet')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_real_503_delivery_failure(self):
        with self.assertRaises(h.Refusal): h.delivery(self.event(), self.hook('/fail'))

    def test_redirect_is_refused_and_never_followed(self):
        before = len(self.records)
        with self.assertRaises(h.Refusal): h.delivery(self.event(), self.hook('/redirect'))
        self.assertEqual(len(self.records), before + 1)

    def test_untrusted_ca_refused_before_http_application_data(self):
        config = self.hook('/receive'); del config['ca_file']; before = len(self.records)
        with self.assertRaises(h.Refusal): h.delivery(self.event(), config)
        self.assertEqual(len(self.records), before)

    def test_wrong_hostname_refused_before_http_application_data(self):
        config = self.hook('/receive'); config['webhook']['url'] = config['webhook']['url'].replace('localhost', '127.0.0.1')
        before = len(self.records)
        with self.assertRaises(h.Refusal): h.delivery(self.event(), config)
        self.assertEqual(len(self.records), before)

    def test_full_deadline_includes_slow_headers(self):
        config = self.hook('/slow'); config['timeout'] = 0.35; started = time.monotonic()
        with self.assertRaises(h.Refusal): h.delivery(self.event(), config)
        self.assertLess(time.monotonic() - started, 1)

    def test_endpoint_heartbeat_get(self):
        self.assertEqual(h.request_https('GET', self.url+'/heartbeat', 2, ca_file=str(self.cert)), heartbeat())

    def test_cli_endpoint_failure_reports_sanitized_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); config = folder/'config.json'
            config.write_text(json.dumps({'heartbeat_url': self.url+'/fail?secret=TOKEN', 'ca_file': str(self.cert)})); config.chmod(0o600)
            r = subprocess.run([sys.executable, h.__file__, 'evaluate', '--state', str(folder/'state'), '--config', str(config)], capture_output=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)['conditions']['heartbeat']['reason'], 'endpoint_unavailable')
            self.assertNotIn(b'TOKEN', r.stdout+r.stderr)
            self.assertFalse((folder/'state').exists())

    def test_real_failure_then_recovery_drain_and_dedup(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)/'state'
            first = h.run(heartbeat(telnet='fault'), state, self.hook('/fail'), NOW, True)
            self.assertEqual(first['pending_count'], 1)
            pending_id = h.read_json(state, private=True)['queue'][0]['event_id']
            count = len(self.records)
            second = h.run(heartbeat(NOW+1), state, self.hook('/receive'), NOW+1, True)
            self.assertEqual(second['delivered'], 2)
            self.assertEqual(self.records[count]['id'], pending_id)
            self.assertEqual(json.loads(self.records[count+1]['body'])['transition'], 'recovery')
            third = h.run(heartbeat(NOW+2), state, self.hook('/receive'), NOW+2, True)
            self.assertEqual(third['delivered'], 0)


if __name__ == '__main__':
    unittest.main()
