# Changelog

## 1.6.7

- The VM status check no longer flaps at its 5 s clock tolerance. Before, a
  status report stamped 5.1 s and then 4.9 s ahead of Home Assistant's clock
  raised "Uhrzeiten stimmen nicht überein" and cleared it again on every
  poll. Now that finding stays until the report is no more than half the
  tolerance (2.5 s) ahead, or has stayed within the tolerance for 5 minutes,
  so a clock hovering at the limit gives one notice and one recovery, and a
  clock settling just inside it is judged normally again after 5 minutes.
  The tolerance itself, the state file format and the reason codes are
  unchanged; an older state file loads as before.
- The same file in the off-host evaluator (`tools/monitoring`, where the
  tolerance can be lowered) now refuses a tolerance below 1 s. At 0, hosts
  with synchronised clocks sit at the boundary and the finding flapped on
  every poll. The app always uses 5 s; this second change does not affect it.

## 1.6.6

- Room checks continue after an app restart (owner question 2026-11-18: "why
  do they need days?"). Before, the checks kept what they had seen in memory
  only, so an app update, an app restart or a host reboot started every
  waiting time and every pattern over: a room that does not get warmer needed starts
  without effect on three new days, the notification service sent "wieder in
  Ordnung" and days later the same problem as new. Now the app saves the
  checks' state in its own `/data` (at most every 5 minutes and when it
  stops, owner decision 2026-11-18; written as a whole, below 4 MB; after a
  crash or power cut the minutes since the last save count as stopped time) and continues from it as if Home Assistant
  had been unavailable for the time it was stopped: open notices keep their
  start time, waiting times run on. A first start starts fresh as before;
  a stop of more than 24 hours, a clock more than 5 minutes before the save
  after a reboot, other check rules or an unreadable file also start fresh,
  with one log line. A changed room selection starts again only the rooms it
  changes (and the circuit-wide checks of a circuit whose rooms changed), both
  after a restart and when the room page saves; beobachten or melden keeps
  everything. DOCS.md "Room checks after an app restart".
- Frost (Störung): a room already counted in frost stays counted while its
  sensor drops out for a while, so one sensor dropout no longer ends the fault
  or gives it a new start time.
- Betriebsüberwachung: the 15-minute grace for a silent VM applies only when
  the last VM report said maintenance; a VM that left maintenance and then
  falls silent is a Störung after the usual 90 seconds. With `notify_watch`,
  a silent notification service is shown once (the VM's own row), not twice.
- Options, room page, report format and codes are unchanged.

## 1.6.5

- Room checks report a heating problem that lasts, not normal life in the
  house (owner, 2026-11-18). Before, one aired or open-door morning, a whole
  house aired at once (as a fault) or a valve turned down for an afternoon
  could raise a notice.
- Airing: a drop of 1 degree within 15 minutes starts an airing episode that
  lasts until the room is back (at most 12 hours), not a fixed hour. Neither
  "too cold" nor "does not get warmer" counts in it, and a heating start in it
  does not count.
- "Raum wird nicht wärmer": a heating start is judged on the trend of the
  whole watch (median of its last against its first quarter), only for a room
  that starts 1.5 degrees or more below target. The notice needs starts without
  effect on three different days within four days; a start the room warms on,
  or the room getting back near its day target, clears the count. It ends at
  such a start, near the day target, or two days after the last start without
  effect.
- "Heizkreis wird nicht wärmer" (fault): two starts on different days on which
  at least two rooms and at least half of the circuit's rooms stayed flat and
  none warmed. Aired rooms do not count.
- "Raum zu kalt": a room that is still getting warmer is not "staying cold";
  after one cold spell (90 min radiator, 4 h underfloor) the room must also
  stay more than 1 degree below the day target for 12 hours. It then stays
  through the night (setback or heating limit) until the room is warm again,
  or 26 hours after the last cold spell.
- "Heizkreis zu kalt" (fault) waits while the rooms are getting warmer (after
  a holiday or a cold morning).
- "Sensor springt": three jumps within 24 hours (a single HA restart value is
  no longer enough); an excursion that returns counts only when its shorter
  leg is faster than room air can change (1.5 degrees a minute), so a sensor at
  an open window is not jumping.
- Underfloor "Raum zu kalt" follows the underfloor circuit check of 1.6.4:
  judged only in a day phase after 4 hours of heat-up, its cold spell paused
  (not restarted) by setback and heat-up for up to 24 hours. Before, with a
  programme like 06:00-09:00 and 15:00-23:00, an 8-hour settle filled every
  day phase and a cold underfloor room was never reported. The underfloor
  circuit check also waits while the rooms are getting warmer.
- Owner decisions (2026-11-18): a room kept cooler on purpose, or with its
  sensor at a cold spot, gets "darf kühler sein" or is not monitored; the app
  does not learn rooms' usual levels. The latencies for real faults (DOCS.md
  "What is checked") are accepted as designed.
- Frost checks, the other sensor checks, options, report format and codes are
  unchanged. Room thresholds in the VM's rule catalogue are unchanged; its rule
  descriptions explain the new conditions.

## 1.6.4

- The VM's signature on its verdict (`authorization`) is not copied into the app's checked verdict or the HA attributes.
- Betriebsüberwachung: no Störung after a Home Assistant restart or reconnect while the VM was reporting (90-second grace as at start), and up to 15 minutes of silence after a VM report in maintenance (release restart) show Unbekannt; a VM that was already silent stays Störung. Report freshness is measured on the app's clock; the VM timestamp may differ by up to 5 minutes and may step back, only an exact repeat is refused (a VM clock a few seconds ahead, a step back or one report from the future gave a lasting Störung).
- Room monitoring: a failed sensor read no longer completes a running waiting time (a pause passed a zero dwell, so one failed read made a sensor finding immediate). Underfloor circuits (owner decision): the circuit-wide Störung is now judged only in a day phase, against the day target, after 4 hours of heat-up and for 3 hours (was 8 + 8 hours in one phase, which a daily programme never reached); never during setback, and an active Störung holds through the night.
- Start-up: until the heartbeat monitor has polled once it answers "unknown"; within the 90-second start-up grace that no longer counts as a failure, so starting or updating the app no longer publishes a false "VM nicht erreichbar" (Störung).
- Optional option `notify_watch` (off by default): with the harg-notify notification container installed, the Betriebsüberwachung shows its status freshness as the check Benachrichtigungsdienst. After 90 seconds without a live status it becomes a Hinweis; it never lowers a worse state. Without the option nothing changes.

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
