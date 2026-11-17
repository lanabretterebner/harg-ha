# Changelog

## 1.6.3

- Home Assistant now owns the existing Betriebsüberwachung verdict: a VM outage or missing live reports becomes Störung within 90 seconds, even while the VM is off. An enabled health check can detect it sooner.
- Retained Normal reports never establish liveness after a restart. If the HA app stops, the verdict expires after 90 seconds. Entity identity and saved HA views remain unchanged.
- Preserve MQTT messages that arrive before the subscription acknowledgement, allowing room-monitor context to recover after restarts.

## 1.6.2

- Saving on the room page asks Home Assistant again whether the user is an
  administrator, every time. Before, the answer was kept for a minute, so a
  user demoted in that minute could still save. If Home Assistant does not
  answer, saving is refused. Showing the page still uses the one-minute answer.
- A sensor assignment that is not text (for example a list) is now refused
  with "Unbekannte Zuordnung eines Sensors." instead of causing an internal error.
- Nothing else changed: the same options, rules, permissions and report.

## 1.6.1

- Sensors are now chosen on the app's room page instead of typed into the
  options: the page lists every temperature sensor of Home Assistant with
  name, entity ID, value and area, has a search field, and offers per sensor
  "nicht überwachen", Heizkreis 1, Heizkreis 2 or Weitere Räume with the
  flags "darf kühler sein", "Ofen oder viel Sonne" and "Frostwarnung", plus the
  mode and the two circuit types. Sensors that are selected but gone from
  Home Assistant stay listed so they can be removed.
- Saving writes the app's own options through the Supervisor and keeps every
  other option. The running room monitoring takes the new selection over
  without a restart (its waiting times start again). If Home Assistant refuses
  or does not answer, the page says so and nothing is assumed saved.
- The app log names the path of the room page at start ("Raumsensoren
  auswählen"), so the address no longer has to be taken from the browser.
- Only Home Assistant administrators can save; the app asks Home Assistant
  (`config/auth/list`) whether the logged-in user is one. Other users, and
  everyone while that check fails, see the page read-only. Saving is accepted
  only from the page itself (same origin, page token, JSON).
- Permissions: none added. Every app may read and change its own options at
  the Supervisor with the token it already has, so `hassio_api` stays off. The
  existing Home Assistant API permission now also serves the administrator
  check and one fixed read-only template for the sensor areas.
- The page loads no external resources; its inline style and script carry a
  fresh nonce per response.
- The options stay visible and editable in the Configuration tab.

## 1.6.0

- Add optional room monitoring with Home Assistant temperature sensors per
  heating circuit (options `heizkreis_1_sensoren`, `heizkreis_2_sensoren`,
  `haus_sensoren`, `kuehlere_raeume`, `ohne_ueberwaermung`,
  `haus_frost_sensoren`, `heizkreis_1_art`, `heizkreis_2_art`,
  `raumueberwachung_modus`), with German and English option texts.
- Check rooms too cold or too warm against the circuit's current day/setback
  target from the VM, rooms that do not warm up after heating starts, frost and
  sensor health, with waiting times per radiator or underfloor circuit. Unknown
  or old data gives Unbekannt, never Normal; every notice ends by itself.
- Send codes and numbers to the VM through Core's `mqtt.publish` and receive
  the VM context through one Core WebSocket subscription. The app now reads the
  selected sensor states; it still has no broker password, no controller
  connection and no control call. Nothing is stored.
- Start in `beobachten` mode: room notices are shown but do not change the
  overall state of the Betriebsüberwachung until `melden` is chosen.
- Add the read-only page `api/local-monitor/raumsensoren` with the temperature
  sensors of Home Assistant and the room monitoring status. The status endpoint
  is unchanged.
- An invalid room selection never stops ingress or the VM-health monitor; the
  circuits named in it are reported as not judged (Räume Unbekannt).
- Accept only VM contexts with the exact version 1 field set.
- The warm checks wait after a change of the day target or mode, not after
  every day/setback change, so underfloor circuits are judged at night too.
  A heat-up check waits up to 30 minutes for the pump and the flow to start.
- Align the Dockerfile build version with the app version.
- The app works before the VM is updated. Room monitoring needs the VM release
  with the Betriebsüberwachung (operation-monitor-20261117 or later). Until
  that release runs, the VM sends no context: the room page says "Noch keine
  Daten von der VM empfangen", no room is judged, and no room finding appears
  in the Betriebsüberwachung, in Home Assistant or on the dashboard. Ingress
  and the VM-health notifications are unaffected.

## 1.5.6

- Show readable German problem notifications with a next step. Internal status
  codes and event hashes stay out of the message.
- Clear resolved warnings silently instead of creating healthy/recovery alerts.
  Maintenance does not create a notification.
- Report a missing common dashboard connection once, while retaining independent
  certificate and measurement problems.
- Ignore obsolete outbox events for presentation so a recovered problem cannot
  raise a stale alert or dismiss a newer fault. Durable state and retry IDs stay.
- Clear legacy healthy banners after startup only when current checks confirm
  recovery; never dismiss unknown/failed checks or unrelated notifications.


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
