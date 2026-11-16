# Changelog

## 1.5.5

- Ignore a check kind the evaluator does not know instead of rejecting the whole
  report. Earlier versions required the report's check set to equal exactly
  `certificate` and `telnet`, so a single extra key sent by a later relay
  version made all three components `report_invalid` — a blanket unknown plus a
  notification for each.
- Keep both known checks required: a report missing `certificate` or `telnet` is
  still `report_invalid` for all three components, and an unknown kind in its
  place does not satisfy it.
- An unknown kind is neither evaluated nor reported, whatever it contains.
  Verdicts for current reports, the status schema, reason codes, report format
  and state format are unchanged, and the staleness and clock rules of the known
  checks are untouched.
- Tests cover an unknown extra key, a missing required key and both together,
  and assert that an extra key changes no current verdict. Monitoring tests
  87 -> 89, app tests 169 -> 171.
- Document that none of the three components can see a controller that has left
  the relay's broker, that the one-time charge trigger's `0` report is its own
  reset and that the controller's telemetry cadence is a changeable default.

## 1.5.4

- Installed by the owner on 2026-11-16.

- Accept a VM clock up to 5 s ahead of the HA host instead of 2 s, so two hosts
  a few seconds apart no longer raise `run_future` for all three components.
  The tolerance never shortens an age and cannot be raised.
- Recover from a backward step of the HA host's clock at the next poll. Earlier
  versions held every component at `clock_invalid` until the saved stamp had
  passed, however far ahead it was, and reported no VM outage in that time.
- During that one poll withhold only healthy and maintenance; an unreachable
  VM, a stale or failed check and a reported fault keep their own reason and
  are neither hidden nor notified again.
- Measure the step against monotonic time while the app runs; after a restart
  use the saved wall-clock stamp and replace it when it lies in the future.
- Keep the state schema, reason codes and report format; the state loads after
  an update and after a downgrade.

## 1.5.3

- Treat an empty optional `upstream_connect_host` as unset instead of failing
  startup.
- Reject raw non-ASCII request paths with HTTP400 instead of a 502
  "Cannot reach" response; existing identity and path checks are unchanged.
- Correct the documented `health_alerts.py` pin; the evaluator is unchanged.

## 1.5.2

- Drain a full notification outbox before recording current condition changes,
  preserving original events, stable IDs and at most one delivery per poll.
- Read ingress and monitor options from one strict, bounded configuration
  snapshot; reject duplicate options and ambiguous webhook header names.
- Reject incomplete upstream responses before forwarding dashboard content or
  accepting heartbeat evidence.

## 1.5.1

- Start Python through S6 with-contenv so enabled monitoring receives the Supervisor-provided credential.
- Report fixed startup-stage categories without option values, credentials or exception text.
- Preserve the disabled default, verified TLS, private monitor state and ingress permissions.

## 1.5.0

- Add optional disabled-by-default HA-host health monitoring and persistent notifications through the Supervisor-provided token.
- Preserve verified HTTPS/SNI/connect-IP ingress and non-admin panel behavior; add authenticated read-only monitor status.
- Keep durable private dedup/outbox state across restart; no controller, broker or heating action is part of monitoring.


## 1.4.0 (candidate)

- Make the Ingress sidebar panel available to all authenticated HA users.
- Add optional literal upstream_connect_host for LAN TCP routing while preserving
  the original HTTPS hostname, SNI, certificate verification and Host header.
- Add fixed, rate-limited diagnostic categories without secrets or request data.
- Reconnect once only for bodyless GET/HEAD transient failures before response
  headers; never replay writes, TLS failures, HTTP errors or failed response bodies.
- Keep this app update independent of the installed thermostat frontend version.

## 1.3.0

Track the combined HA client release. App proxy runtime, required options,
permissions and digest-pinned base image remain unchanged.

## 1.2.0

Publish the existing ingress proxy as an installable HA app-store repository
component alongside the HACS popup. Pin the verified multi-architecture base
image by digest and provide generic installation instructions. Proxy runtime
behavior is unchanged from the earlier0.1.0 local-source package.
