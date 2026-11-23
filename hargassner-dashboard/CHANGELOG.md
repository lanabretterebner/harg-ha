# Changelog

## 1.6.12

Review fixes (static review 2026-11-23) and an owner decision. Control
behaviour, the saved room state (rule digest unchanged, a 1.6.11 state
continues) and the content of the reports to the VM are unchanged apart from
the version; a failed report is retried sooner (below).

- **Heizkreis zu kalt is judged in the day phase only, for radiators too**
  (owner decision 2026-11-23, as for underfloor since 1.6.5). A failed pump's
  Störung now stays raised overnight while the circuit keeps heating during
  setback: during setback, or while the phase, the VM's data or Home Assistant
  is unavailable (also across an app restart), the check is not judged at all,
  no new finding starts and an existing one holds (up to 24 hours; for
  underfloor circuits such gaps were limited to 6 hours before). If setback
  switches the circuit off (heating limit or inactive), the finding still ends
  that evening and returns the next day, as for underfloor since 1.6.5. In the
  day phase it is judged against the day target after the 2-hour heat-up and
  ends when the rooms are back within 2.5 degrees. Before, it ended at night
  once the rooms were within 2.5 degrees of the setback target, or at a dial
  tap or mode change, and came back the next day. A new finding is raised only
  in a day phase (before, also at night against the setback target): for
  radiators it needs one day phase of at least 5 hours (2-hour heat-up plus
  3 hours too cold), as underfloor needs 7 hours (4-hour heat-up plus 3 hours)
  since 1.6.5. With shorter radiator day phases (for example 06:00-08:00 and 17:00-21:00) it is
  not raised; "Raum zu kalt" and "Heizkreis wird nicht wärmer" still report a
  failed pump, later. The detection times of the tested pump failures are
  unchanged.
- **The heating-limit notice holds through the night** ("... ist wegen
  Heizgrenze oder Sommerbetrieb aus, aber ... ist zu kalt", `limit_cold`). It is
  judged in the day phase only: during setback no new one starts and an
  existing one holds (up to 24 hours, also while Home Assistant is away),
  instead of ending every evening and needing another 6 hours (12 hours
  underfloor) the next day.
- **Raum anlernen settles after a target change too**, as the room checks do:
  the hours a room needs to follow an evening raise of the day target no
  longer count as a shortfall and inflate the proposed allowance.
- **Room page search stays quick on a phone:** each sensor's search text is
  prepared once when the page loads, not again on every keystroke.
- **German chat texts in the off-host monitor** (`health_alerts.py` webhook
  formats slack, discord and ntfy): the same wording as the Home Assistant
  notification, for example "Heizung: Messwerte prüfen. ..." or "Heizung:
  Messwerte wieder in Ordnung.", with the event id.
- **Betriebsüberwachung follows the newest state.** Each poll delivered one
  queued notification event, also outdated ones that only made way for a
  newer state of the same check, so with a VM that kept dropping out the HA
  notification could be up to about 30 minutes behind. An event superseded by
  a later one of the same check is now never delivered; each check's newest
  state follows within three polls. Order between the checks, one delivery per
  poll and the unchanged outbox after a failed delivery are kept.
- **Raum anlernen keeps the other rooms** when one entry of
  `/data/room_learning.json` is damaged: only that entry is dropped (before,
  every recording, proposal and learned date was lost).
- **Zurücksetzen needs a second tap:** the first tap asks "Wirklich auf
  Standardwerte zurücksetzen?" and changes nothing; the question lapses after
  5 seconds. A mis-tap on a phone no longer resets a room's settings.
- **Room page: any whole influence can be saved.** The influence fields
  accepted only multiples of 5 in the browser, so a value such as 33 % set in
  the Configuration tab blocked saving the whole page (silently when that room
  was hidden by the search). They now take any whole number from 0 to 100, as
  the app always did.
- **No old VM confirmation shown as current.** While the VM sends no current
  data, the room page says "Keine aktuelle Bestätigung der VM" instead of
  repeating the last "Die VM nimmt die Raummeldungen an".
- **A failed room report is retried after 10 s** when its findings differ from
  the last report that went out, instead of after the 2-minute period; an
  unchanged report, or one failing since the app started, still waits for the
  period.
- **The room page says when the VM's version does not fit** instead of an old
  confirmation.
- **No false reconnect after a slow cycle:** a pong that is already waiting
  is read before the WebSocket's pong deadline is checked (house monitor and
  Betriebsüberwachung).
- **German panel texts:** the ingress messages (for example "Keine sichere
  Verbindung zum Dashboard der VM. Bitte prüfen, ob die VM läuft und erreichbar
  ist." instead of "Cannot reach the local dashboard securely." when the VM is
  down) and the room page's error texts are German; "(3-mal)" in the learning
  evidence.
- The room page's time display no longer fails when Home Assistant's time zone
  changes while the page is rendered.

## 1.6.11

- **Telnet health no longer flaps on a slow VM disk** (incident 2026-11-22:
  snapshots arrived every 3-15 s instead of about every second). The VM's
  Telnet monitor now reports `fresh` while its newest frame is up to 50 s old
  (the dashboard's and HA's display window for Telnet readings), instead of
  5 s. The health check (`health_alerts.py`, the same file as
  `tools/monitoring`) accepts such a report as healthy; a `fresh` report older
  than 50 s is still refused. Install this app before the VM release that
  changes the monitor: an older app would refuse the VM's held `fresh` reports
  and raise an alert.
- Control behaviour is unchanged: commands and control decisions keep their
  strict 5 s Telnet lifetime.

## 1.6.10

- **Raum anlernen reads the long-kept data** (owner request 2026-11-19:
  "didn't we implement long time statistics in our own app?"). 1.6.9 read
  only Home Assistant's recorder history, which keeps 10 days by default.
  - Heating circuits (phase Tag/Absenken, target, status) now come from the
    VM's own history: the new read-only VM endpoint
    `GET /api/dashboard/v1/circuit-history` (5-minute state codes and flow
    from the VM's channel history, day and setback targets with room
    correction from its parameter journal; 30 days; phase from the state
    code: 1 day, 2/3/4 setback, as the VM's phase mapping). The app reads it as
    the administrator who pressed the button, through the Ingress
    credential it already has (an ordinary dashboard session, read-only).
    Days older than that, or every day when the VM cannot give it (an older
    VM release, refused, unreachable, no target known), come from Home Assistant's recorder
    history of the VM's climate entities, as before.
  - Room temperature: Home Assistant's long-term statistics (hourly mean,
    min and max, kept indefinitely for sensors with `state_class:
    measurement`) through `recorder/statistics_during_period` for the
    allowance; the recorder history (every change) only for the heat-up
    response. A sensor without long-term statistics falls back to the
    recorder history, and the evidence says so.
  - Periods: alles Verfügbare (up to one year, the default), 3, 7, 14, 30
    or 90 days. The recorder is read newest first and only as far back as it
    keeps data (stops after two empty 12-hour pieces, at most 90 days).
  - The evidence names every source and its resolution, for example
    "Raumtemperatur: Langzeitstatistik von Home Assistant, stündlich, 63
    Tage vorhanden; feiner Verlauf (jede Änderung) 10 Tage für das Aufheizen." and
    "Heizkreise: VM-Verlauf, alle 5 Minuten, 30 Tage." A VM that cannot be
    read is named with the reason ("ältere VM-Version", "Zugriff
    abgelehnt", "nicht erreichbar").
  - Unchanged: accept/discard/reset, warnings that need "geprüft", the
    failing-radiator warning, the recording fallback, the learning file
    (`/data/room_learning.json`, same schema; 1.6.9 reads it after a
    downgrade) and no writes to Home Assistant or the VM.
- Needs VM release with `circuit-history` for the VM source; without it the
  app works as 1.6.9 and says "ältere VM-Version" in the evidence. Install
  order is free.

## 1.6.9

- Free room settings (owner request 2026-11-19: "why only fixed options?").
  On the room page each room on a circuit has number fields instead of the
  fixed choice: **Darf kühler sein um** 0 to 10 °C in 0.5 steps, and
  **Einfluss Heizkreis 1** and **Einfluss Heizkreis 2** 0 to 100 %, so a room
  can be on both circuits. New option `raum_einstellungen` (one entry per
  room that the 1.6.8 lists cannot express). 1.6.8 settings are taken over
  unchanged: 2, 4 or 6 °C stay that number with 100 % on the own circuit,
  "nur Frost" becomes 0 % on both circuits.
- Influence semantics (DOCS.md "Choosing the sensors"): below 30 % a circuit's
  heating starts do not judge the room (below 30 % in total it is never "too
  cold"); from 30 % the expected rise after a heating start is proportional
  to the influence (never below 0.2 degrees); from 50 % the room counts for
  the circuit checks with cold limits scaled by its influence; with two or
  more rooms of 80 % or more (at most 2 °C allowance) only those decide
  "Heizkreis zu kalt" and "Heizkreis wird nicht wärmer", so a real circuit
  failure is still found through the strongly heated rooms and a weakly
  heated room that warms by the sun never ends a circuit fault. A room the
  other circuit heats too (30 % or more there) neither decides nor holds off
  a circuit's checks while two or more rooms are heated by that circuit
  alone. With default values every check is exactly that of 1.6.8.
- **Raum anlernen** (owner request 2026-11-19, history first): for one room,
  started only by an administrator, the app reads the room sensor's history
  and the two circuits' climate entities from Home Assistant's recorder (up to
  30 days, in bounded 12-hour pieces) and proposes allowance and influence
  with its evidence (days used, day-phase mean against the target, rise per
  heating start and circuit, number of starts). Nothing applies before
  **Übernehmen**; **Verwerfen** discards; "Angelernt am ..." with
  **Zurücksetzen**. With too little history (fewer than 3 day-phase days and
  fewer than 3 heating starts) it offers to record from now on for 3, 7 or 14
  days; the room's own heating notices pause meanwhile, frost and sensor
  checks stay on. Every proposal that loosens the checks noticeably (target
  never or rarely reached, much more allowance, the room leaving the strong
  rooms of its circuit, hardly warmer on its circuit, a second circuit found)
  carries a warning and is accepted only after an explicit "geprüft":
  learning would otherwise take a failing radiator for normal. Proposals
  older than 7 days are refused. State in `/data/room_learning.json`.
- Alert texts name the next step (alert text audit 2026-11-19, released here;
  it was "Unreleased" on main). The Betriebsüberwachung Störung this app
  raises when the VM is silent now reads "VM nicht erreichbar oder keine
  aktuellen Betriebsdaten. Heizungszustand unbekannt. Prüfen, ob die VM läuft
  und im Netzwerk erreichbar ist."; the notice of a silent notification
  service (`notify_watch`) adds "Container harg-notify und Netzwerk prüfen."
  Wording only: nothing fires differently.
- Reports to the VM are unchanged (a free allowance is sent as before, the VM
  of room-allowance-20261119 accepts 0.5 to 10 degrees); influence and
  learning stay in the app. No VM update needed.
- Saved room state of 1.6.8 continues (same rule constants; a room set as a
  1.6.8 choice keeps its mark). A downgrade to 1.6.8 drops
  `raum_einstellungen`: rooms fall back to the mirrored lists (allowance
  rounded down to 0/2/4/6, 100 % on the own circuit); see DOCS.md
  "Downgrade from 1.6.9".

## 1.6.8

- Room allowance per room (owner request 2026-11-19). The owner marked the
  Wintergarten "darf kühler sein" and still got "zu kalt (16,5 °C statt
  23,0 °C)": the allowance was a fixed 2 degrees, and the text did not show
  it. The checkbox is now a choice **Raumtemperatur** for each room on a
  circuit: **normal**, **darf 2 °C kühler sein** (the old checkbox; existing
  rooms keep it), **darf 4 °C kühler sein**, **darf 6 °C kühler sein** or
  **nur Frost und Sensor prüfen** (frost and sensor checks only, the room
  stays on its circuit). New options `kuehler_4_raeume`, `kuehler_6_raeume`,
  `nur_frost_raeume`; every room with an allowance stays in
  `kuehlere_raeume` too, so a downgrade to 1.6.7 still treats it as
  2 degrees cooler.
- "All rooms of a circuit too cold" (fault) is judged over the rooms with
  normal or 2 degrees, as before. Rooms with 4 or 6 degrees count only on a
  circuit with fewer than two such rooms, each 3 degrees below its own limit
  (a reference sensor that drops out pauses the check, as before). The
  notice names the room furthest below its own limit.
  Frost-only rooms never count for a circuit check. So a room the owner
  exempted neither raises the fault nor hides a failed circuit.
- The room report tells the VM the allowance of a "zu kalt" room (room cold
  and cold despite the heating limit), so the VM text names the allowed
  temperature: "(16,5 °C; erlaubt ab 21,0 °C, Ziel 23,0 °C)". The app sends
  it only when the VM's context says it reads it (context major 2, which the
  VM sends only to an app that lists it). With an older VM the report is
  exactly that of 1.6.7 and the text stays "(16,5 °C statt 23,0 °C)".
- The saved room state of 1.6.6/1.6.7 continues (same rule constants, same
  fingerprints for normal and 2 degrees). A room whose choice changes starts
  its checks fresh, as a changed checkbox did.

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
- Reject raw non-ASCII request paths with HTTP 400 instead of a 502
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
behavior is unchanged from the earlier 0.1.0 local-source package.
