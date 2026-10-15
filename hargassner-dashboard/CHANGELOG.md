# Changelog

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
