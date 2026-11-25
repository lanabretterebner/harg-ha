# Installation

This app opens the existing dashboard through Home Assistant Ingress. Home
Assistant authenticates the user, and a private credential identifies the app
to the dashboard server. Direct dashboard access keeps its current login.

## 1. Enable the installed backend for your HA instance

A separately installed Hargassner local-control backend must support the
existing ingress contract and configuration helper. This app is an HTTPS proxy;
it does not install that backend or connect to the boiler. Ask its administrator
to enable ingress for this HA host and browser origin. The ordinary dashboard
password login stays available.

Run the following commands as an administrator on that backend host, adapting
installation paths to your installation. These are backend configuration and
API service operations, separate from installing the HA app.

Replace `192.0.2.25` with the actual source IP that the dashboard server observes
for the HA app's outbound connections. Usually this is the HA host's LAN address
after container NAT; it is different from the Supervisor address checked inside
the app. The known MQTT broker address alone does not establish the HA host IP.

Replace the example origin with the exact HA browser origin: scheme, hostname
and explicit nondefault port, without a path or trailing slash. Repeat
`--origin` for your external HA browser URL if you use one. Writes from an
unlisted origin fail even when reads work.

First check the proposed settings without applying them:

```sh
/opt/hargassner-mitm/ha-venv/bin/python \
  /opt/hargassner-mitm/configure_dashboard_ingress.py enable \
  --proxy-ip 192.0.2.25 \
  --origin http://homeassistant.local:8123 \
  --dry-run
```

Then repeat the same command without `--dry-run`. The helper accepts individual
IP addresses only, granting `/32` for IPv4 or `/128` for IPv6. It preserves the
existing dashboard settings and local login, validates the new configuration
using the installed API, and backs up the prior configuration as
`/etc/hargassner-offline/dashboard-api.json.ingress-backup`.

The helper creates or reuses
`/etc/hargassner-offline/dashboard-ingress-token`, owned by root:hargmitm with
mode 0640. It does not print the token or restart services. Activate the new
ingress settings by restarting only the dashboard API:

```sh
systemctl restart hargassner-dashboard-api
systemctl is-active hargassner-dashboard-api
```

The MQTT relay, HA adapter and controller do not need restarting. If the source
IP is uncertain, inspect connection addresses on the VM while opening the app
after step 2, then correct the individual IP and rerun the helper:

```sh
ss -tn '( sport = :8443 )'
```

Keep the allowlist restricted to the actual source host rather than the whole
home network. Restart only the API again after changing the settings.

## 2. Install the app from this repository

In **Settings → Apps → App store → three dots → Repositories**, add:

```text
https://github.com/lanabretterebner/harg-ha
```

Find **Hargassner Dashboard**, choose **Install**, and wait for the local image
build. This needs Home Assistant OS or a Supervisor installation, `amd64` or
`aarch64`, and registry network access during installation. A HACS installation
of the popup does not install this app; the two stores use the same repository
URL independently. Container build, upgrade and monitor commissioning are
separate acceptance steps.

For an offline source transfer, copy this entire folder to the HA local apps
share (`/local_apps/hargassner-dashboard`), then refresh the App store and
install it under Local apps. The repository contains source rather than a
prebuilt application image; Supervisor builds the Dockerfile during installation.
[Official app repositories](https://developers.home-assistant.io/docs/apps/repository/).

Copy the matching token separately. On the relay VM, explicitly display it:

```sh
cat /etc/hargassner-offline/dashboard-ingress-token
```

Keep this value private; do not put it in chat, URLs or shell command arguments.
Set these options in the app's Configuration tab:

```yaml
upstream_url: https://dashboard.example:8443
ingress_token: PASTE_THE_PRIVATE_TOKEN_HERE
```

`upstream_url` is the HTTPS origin of your existing dashboard, with no path,
query or login credentials. Use the hostname covered by its TLS certificate,
and make sure that hostname resolves to the local dashboard from Home
Assistant. Certificate chain and hostname verification are always enabled;
there is no option to disable verification. The app uses the image's system
trust store, so a private certificate authority needs to be installed into a
custom image before it can be used.

If the hostname resolves intermittently or to a public router address inside
Home Assistant, an administrator may set the optional literal TCP destination:

```yaml
upstream_connect_host: 192.0.2.57
```

Use the actual dashboard server LAN IP, not this example. IPv4 and unscoped
IPv6 unicast literals are supported; hostnames, URLs, ports and zone IDs are
rejected. Omit this option to retain normal DNS. Keep `upstream_url` unchanged:
its hostname still supplies TLS SNI, certificate hostname verification and the
HTTP Host header. This option changes only where the TCP socket connects; it
never weakens certificate validation. A moved dashboard needs this IP updated.
No DNS fallback to a different server is attempted.

`ingress_token` is the exact secret from the backend token file. This is not
your dashboard password and not a Home Assistant API token. It is stored in
the app's private options, never in browser URLs or frontend JavaScript. Protect
Home Assistant backups and administrator access, which can include app options.
Options are read once per load, limited to 64 KiB and rejected on duplicate keys
or nonfinite numbers. Ingress and monitoring use the same configuration snapshot.

Start the app, choose **Open Web UI**, and enable **Show in sidebar** if desired.
Home Assistant will open the dashboard inside its authenticated interface.
Use that sidebar entry or Open Web UI for access; do not hardcode the temporary
`/api/hassio_ingress/...` address in a website card.

`panel_admin: false` makes the sidebar panel available to all authenticated
Home Assistant users, including nonadministrators. Ingress authentication is
still required; this does not expose an anonymous or LAN port. The backend
gives every admitted Home Assistant user, administrators included, an ordinary
dashboard session; the dashboard administrator functions need the direct
dashboard login. The app does not map Home Assistant entity permissions onto
individual boiler controls.

## 3. Verify after installation

1. While logged into Home Assistant, open the app and confirm that the
   dashboard loads without its separate login prompt. Repeat with a
   nonadministrator account to verify sidebar and ingress access.
2. Confirm telemetry and page navigation work, including a browser refresh.
3. Open the original dashboard address directly in a private browser window;
   it should still require the local dashboard login.
4. Confirm an unauthenticated Home Assistant browser cannot open the app.
5. A permitted operator can verify an intended setting change through the
   normal dashboard workflow. A real HA ingress session and control through
   this candidate remain separate acceptance steps.

A `502` can indicate DNS, routing, certificate verification, or an unreachable
backend. The app logs a fixed diagnostic category such as `dns`, `timeout`,
`connection_refused`, `connection_reset`, `tls_verification`, `tls_handshake`,
`transport`, `http_protocol`, `response_policy` or `configuration_or_response`.
Each failure/retry category is limited to one line per 30 seconds. Logs contain
no URL, ingress path, IP address, user ID, token, header or raw exception text.
A category identifies a failure class; it does not establish the cause of an
earlier intermittent failure.

Bodyless GET and HEAD requests may reconnect once after a transient DNS or
transport failure before response headers are available. A retry is
allowed only within the initial 15-second retry window and uses a fresh socket
with its timeout reduced to the remaining window. Socket timeouts do not impose
a total deadline on a slowly streamed response. Other
methods, GET/HEAD with a body, TLS failures, HTTP status responses, response
policy failures and response-body failures are never retried. In particular,
POST/PUT/PATCH/DELETE operations are sent at most once by this proxy; a read
retry is not evidence that an earlier control operation failed or completed.
An authentication failure can indicate a mismatched token or source address.
A write rejected after successful reads can indicate an unlisted browser origin. Reopen the app if the Home Assistant
session expired. No fallback credentials are sent to the upstream dashboard.

To remove password-free Home Assistant access, stop the app, then run on the
relay VM:

```sh
/opt/hargassner-mitm/ha-venv/bin/python \
  /opt/hargassner-mitm/configure_dashboard_ingress.py disable
systemctl restart hargassner-dashboard-api
```

The helper removes only the optional ingress block and retains the private
token for deliberate reuse. Direct password login remains configured.

## Implementation and local tests

The app publishes no LAN ports or Supervisor-management/host/Docker privileges.
The HA API permission serves the optional VM-health monitor, the
Betriebsüberwachung and the room monitoring described below (it reads the
selected sensors, opens one Home Assistant Core WebSocket and publishes through
Core's `mqtt.publish` service), and the room page: it checks whether its viewer
is an administrator (`config/auth/list`), reads sensor areas through one fixed
template, reads history for **Raum anlernen** and reads and saves the app's own
options at the Supervisor. The room page's actions (saving the selection and
**Raum anlernen**) are the app's only writes; they are accepted only from a
Home Assistant administrator, same-origin from that page, with the page's
token and a JSON body. Ordinary
ingress accepts requests only from the actual TCP peer `172.30.32.2`.
It requires a single valid `X-Remote-User-Id` and `X-Ingress-Path` supplied by
Ingress. All incoming cookies, authorization and forwarding headers are
discarded. The HTTPS upstream receives only the shared credential, validated
user/path, and an explicit allowlist including the browser Origin and CSRF
token. The backend separately checks its source network, shared credential,
and mutation origin/CSRF. Session bootstrap returns an in-memory CSRF token.

The proxy bounds request and response sizes, concurrent requests and socket
timeouts. It does not log request paths, header values or app options. Upstream
cookies and authentication challenges are removed. Redirects are allowed only
to the exact authenticated ingress root, for the backend's `/login` shortcut;
other redirects fail. The dashboard's response body and framing policy come
from the updated backend.

Run the synthetic tests from the repository root:

```bash
python3 -B -m unittest discover -s hargassner-dashboard -p 'test_*.py' -v
```

These tests need Python 3 and OpenSSL. They create a temporary local HTTPS
server and certificate, and check peer enforcement, header replacement,
request/response bounds, duplicate header rejection, redirect handling, and
certificate/hostname verification. The room-monitoring tests use an injected
clock, a synthetic room model over several weeks and loopback HTTP and
WebSocket servers. They make no live Home Assistant, broker or controller
requests. The current container build, app upgrade and actual
nonadministrator access remain unverified by these synthetic tests.

The separately installed backend has its own ingress and browser verification.
This source package does not contain a controller identity, credential, live
configuration or backend deployment. No successful local test establishes that
a particular HA ingress session or backend setting is commissioned.

The app uses the official `base-python:3.14-alpine3.24` image, which supports
the two declared architectures. Its OCI multi-architecture index was verified
on 2026-10-14 and is pinned by digest in the Dockerfile; see
[base-image verification](../validation/base-image.json). Packaging and security decisions follow the
official [base image list](https://github.com/home-assistant/docker-base),
[app configuration](https://developers.home-assistant.io/docs/apps/configuration/),
[Ingress requirements](https://developers.home-assistant.io/docs/apps/presentation/#ingress),
and [authenticated user headers](https://developers.home-assistant.io/docs/apps/security/#authenticating-a-user-when-using-ingress),
checked on 2026-10-14.


## Optional independent HA-host monitor

The monitor runs on the HA host while the observed dashboard VM is separate.
This VM-health monitor never connects to the controller or MQTT. (The separate
room monitoring uses MQTT only through Home Assistant Core; see below.)
Monitoring is **disabled by default**; updating the app alone does not send a
notification.

**Coverage limit: a controller that has left the broker.** Not connecting to the
controller or MQTT is also a limit on what the monitor can report, not only a
safety property. **None of the three components can see a controller that is no
longer on the relay's broker.** The heartbeat only shows that the VM is
reachable and serving the health route; the certificate component only reports
the age and result of the VM's own certificate check; and the Telnet component
is a VM-to-controller TCP23 read stamped with the VM's own clock, entirely
independent of the MQTT session, so it keeps reporting `fresh` while the broker
path is dead. Offline firmware analysis of V14.1HAR.q1 establishes that this
state is reachable without any fault on the VM: a cloud `switch_broker` command
stores a volatile broker override whose only local symptom is an unexpected
will followed by silence, and a cloud write of `Z1w` = 0 alone can take the
controller off the relay, which a controller restart does not undo. In that
state every HA entity goes unavailable while all three monitor components can
still report healthy. Unavailable entities are the symptom to investigate
directly; the monitor is not a detector for it, and a silent monitor is not
evidence that the controller is connected.

Set `monitor_enabled: true`, a separately generated random URL-safe
`monitor_token` matching the VM's dedicated health-token configuration, and
`monitor_poll_interval` between 15 and 60 seconds (default 30). The monitor token
must differ from `ingress_token`; it is not a general dashboard credential.
Do not enter a long-lived HA user token. `homeassistant_api:true` supplies the
Supervisor-owned `SUPERVISOR_TOKEN` to the app. It stays in process memory and
private request pipes, is not stored in the durable alert state and is never
returned by status or logs. Other Supervisor/admin/Docker/host permissions and
public ports remain disabled.

The VM read is exactly `GET /api/dashboard/v1/monitor-health` with the separate
`X-Hargassner-Health` header, never a control request. HTTPS chain/hostname
verification stays enabled; the optional TCP connect-IP preserves the original
origin hostname for SNI, certificate checks and Host. There are no redirects,
plaintext VM reads or retries within an individual poll. Response JSON is
strict, limited to 64 KiB and rejected on duplicate keys/nonfinite numbers or a
body shorter than its declared content length.
A child-process deadline bounds DNS, TLS, headers and body reads to 5 s.

The accepted sanitized health schema keeps original certificate/Telnet checker
timestamps. Fresh API reads cannot renew stale runs. Defaults distinguish 120 s
heartbeat, 90000 s certificate execution, 45 s Telnet execution and 5 s future
clock tolerance. A Telnet `fresh` report is accepted with a frame up to 50 s
old (app 1.6.11; older apps refuse such reports from a newer VM monitor).
Missing/invalid/stale or failed heartbeat evidence is **unknown**;
it does not prove the heater is down. Normal maintenance is a suppression state.
A healthy first observation establishes a silent baseline. Fault and unknown
conditions create readable German problem notifications with a next step. Healthy
recovery and maintenance silently dismiss the corresponding warning. A missing
common connection produces one Status warning. The fixed persistent IDs are
`harg_health_heartbeat`, `harg_health_certificate`, `harg_health_telnet`.
A VM maintenance silences the Messwerte warning for at most 2 hours of its
run (the VM's cap). After that, from app 1.6.17 with a VM that reports it, the
warning says "Wartung seit über 2 Stunden aktiv (seit HH:MM) - Messwerte werden
wieder geprüft. Wartung prüfen." (the time in Home Assistant's time zone);
with an older VM it is the general Messwerte text.

A VM clock up to 5 s ahead of the HA host counts as current. A larger lead is
unknown `run_future`. Once raised, it stays until the stamp is no more than
2.5 s (half the tolerance) ahead, or has stayed within the 5 s tolerance for 5
minutes, so a VM clock hovering at the limit (5.1 s, then 4.9 s ahead) gives one
notification and one recovery instead of one of each per poll. The tolerance
never shortens an age, so it delays a stale verdict by at most 5 s. When the HA
host's own clock steps backward by more than 5 s, the next poll reports
`clock_invalid` for components that would otherwise be healthy or in
maintenance, and the poll after it evaluates normally again. Alerts that hold
under either clock, such as an unreachable VM, a stale or failed check or a
reported fault, keep their own reason throughout. Within a running app the
step is measured against monotonic time. After an app restart only the saved
wall-clock stamp can show it, and a stamp that lies in the future is replaced
at the first poll instead of being waited out. The hold time is kept in the
saved condition; the state format and reason codes are unchanged, so the state
survives both an update and a downgrade.

A check kind the evaluator does not know is ignored. Both known checks,
`certificate` and `telnet`, are required, and a report missing either one stays
`report_invalid` for all three components. An additional key that a later
relay version may send is neither evaluated nor reported.

Delivery is ONLY local `POST` to the Supervisor proxy at `172.30.32.2`, using
`/core/api/services/persistent_notification/create` for problems and
`/core/api/services/persistent_notification/dismiss` for silent recovery, with the
Supervisor token. This explicit fixed private-transport policy applies only to
the internal Supervisor proxy; the VM path is always verified HTTPS. The app
cannot select arbitrary HA services or remote notification URLs. A 2xx response
accepts the queued delivery; response bodies are not read. Notification text contains the problem and a suggested check, never internal
status codes, event hashes, credentials, hostnames, controller identifiers, raw
measurements or upstream exceptions. Event IDs remain transport metadata.
After startup the app also clears legacy healthy banners when current checks
confirm recovery. Only the three fixed monitor IDs can be dismissed. Unknown or
failed checks stay visible. The newest state of each check wins (app 1.6.12):
a queued event followed by a later event of the same check is superseded and
never sent; it leaves the outbox together with the next delivered event, only
after that delivery succeeded. Each check's newest state is therefore shown
within three polls even when the VM keeps dropping out. An event that no longer
matches the current condition is accepted without changing the notification;
the durable outbox and clock/freshness guards remain intact.

State is separate from all controller/charge journals: the evaluator-owned
directory `/data/health-monitor` (mode 0700) holds atomic, fsynced state and
lock files (mode 0600). Keep it across
app restarts and normal upgrades. Events are durable before sending, with a
stable retry ID and component notification ID. One event at most is delivered
per poll; failed/ambiguous delivery retains that event. The bounded 64-event
outbox refuses corruption or unsafe ownership without sending or deleting events.
When full, the next delivery also removes every superseded event before it, so
current condition changes are usually recorded in the same poll; otherwise once
enough space is available. Failed delivery
keeps the original events and IDs. Do not erase active state to clear a warning;
corrupt state requires explicit operator inspection/backup recovery.
This is at-least-once delivery,
not exactly-once; a lost successful response can cause a stable-ID redelivery.

`GET /api/local-monitor/status`, relative to the app's existing Ingress base,
returns only enabled/running, last-check epoch, queue count, current-process last accepted
component/condition and a fixed monitor error code. It uses the SAME actual
Supervisor peer and authenticated HA-user gate, works for a commissioned
non-admin user, accepts no query/body and refuses mutation methods. Do not
access a container port directly or reveal the private Ingress URL in receipts.
Status cannot perform recovery, trigger alerts or reveal options/token/HA user.
The proxy and optional worker stop together on normal app shutdown; no new
notification is started after shutdown is observed.

When commissioning, first verify the normal heartbeat/healthy baseline and that
no new notification appears. A deliberate **monitor-only** wrong token (HTTP 401)
followed by restoring it can then verify genuine endpoint-unverified and
recovery notifications, stable IDs/outbox dedup and status readback without
stopping the VM or changing ingress/controller state. It does not establish a
heater outage. Run that notification test only deliberately. Synthetic
loopback tests do not prove that the actual Supervisor permission/token/API or
receiver has been commissioned.

An HA-host or HA-Core outage prevents its own notifications; independent from
the VM does not mean independent from HA. A separate external watchdog is a
future operator choice. Container build, real notification delivery, receiver
permissions and actual app update remain unverified until separately recorded.

`health_alerts.py` is a byte-identical copy of the project's monitoring
evaluator; `release.json` pins its SHA-256. Its evaluator/outbox semantics are
retained; this app adapter adds the fixed
health-token fetch, existing HTTPS connector, bounded one-event scheduler and
Supervisor destination. No original producer observation time is rewritten.


## Room monitoring with temperature sensors

The app can watch temperature sensors that already exist in Home Assistant and
check whether the heating does its job: rooms that stay too cold while heating
should be active, rooms that do not get warmer after heating starts, rooms that
are clearly too warm, frost, and the health of each sensor. The room sensors
are for monitoring only. They never control the heating, never replace the
controller's own room sensor and never appear as controller readings.

The app sends only codes and numbers to the VM. The VM writes the German texts
and shows them in the **Betriebsüberwachung** (HA sensor
`sensor.betriebsueberwachung` and the dashboard). Room monitoring needs a VM
version that sends the context and accepts these reports (see "Before the VM
is updated" below). Without it, nothing changes on the VM.

### Before the VM is updated

The app can be installed before the VM. Room monitoring needs the VM release
with the Betriebsüberwachung (operation-monitor-20261117 or later). Until that
release runs, the VM sends no context: the room page
`api/local-monitor/raumsensoren` (see "Opening the room page") says "Noch
keine Daten von der VM empfangen",
no room is judged and no room finding appears anywhere. Nothing has to be
changed in the app when the VM is updated; the room checks start by themselves
within a few minutes.

### What the app accesses

For room monitoring, with the Supervisor-provided token and nothing else, the
app:

- reads `/core/api/config` (whether Home Assistant is running, its time zone)
  and the state of each selected sensor, once a minute;
- reads the list of all states only while you open the room page below;
- opens one Home Assistant Core WebSocket and subscribes to the single MQTT
  topic `hargassner/+/house/context`, sent by the VM;
- publishes its report to `hargassner/<gateway>/house/report` through Core's
  `mqtt.publish` service (QoS 0, not retained), every 2 minutes and shortly
  after a change.

The room page adds, only while it is opened or saved:

- one short Core WebSocket connection with the read-only command
  `config/auth/list`, to tell whether the person viewing the page is a Home
  Assistant administrator (cached for page rendering for one minute; every save
  requires a fresh lookup and is refused if Core is unavailable);
- one fixed read-only template through `/core/api/template` that returns the
  area name of each temperature sensor;
- `GET /addons/self/info` and `POST /addons/self/options` at the Supervisor:
  the app reads its own stored options and, when an administrator saves,
  writes them back with only the room settings changed.

**Raum anlernen** (see below) adds, only when an administrator presses it for
one room:

- `GET /core/api/states` once, to find the VM's two heating-circuit climate
  entities (by the attributes the VM publishes, not by their names);
- one WebSocket connection with `recorder/statistics_during_period` for that
  room's sensor only (hourly mean, min and max, in pieces of 10 days, at most
  one year, 60 seconds in all);
- `GET /core/api/history/period/...` for that room's sensor (and, where the
  VM's history does not reach, the two climate entities) only, in pieces of
  12 hours read newest first, stopping after two empty pieces, at most 90
  days (each piece at most 4 MB and 5 seconds);
- one `GET /api/dashboard/v1/circuit-history?from=...&to=...` to the VM (the
  last 30 days, at most 2 MB, 15 seconds), sent like the panel's own requests
  with the Ingress credential and the Home Assistant user id and Ingress path
  of the administrator who pressed the button. The VM treats it as an
  ordinary, read-only dashboard session.

Nothing is written to Home Assistant or the VM; the learning state is kept in
the app's own `/data/room_learning.json` (readable only by the app).

The Supervisor lets every app read and change its own options with the token
it already has, so the room page needs no extra permission: `hassio_api` stays off and
no `hassio_role` is requested. The app does not restart itself and cannot
change any other app, Home Assistant setting or entity.

It has no broker password, no controller connection and no call that can
change a heating setting. Its rule timers and sensor
observations are saved in the app's own `/data` (see "Room checks after an app
restart"); a new sensor selection starts again only the rooms it changes.
Sensor selections persist in the app options; the separate VM-health monitor retains its durable outbox. Home Assistant's MQTT integration
must be connected to the same broker as the VM. The thread runs even when no
sensor is selected, so that the VM learns that room monitoring is off.

### Opening the room page

The room page lives inside the app's Ingress address, at the path
`api/local-monitor/raumsensoren`. The app's panel (**Hargassner** in the
sidebar or **Open Web UI**) opens the VM dashboard at the root of that
address, so the room page can be opened by hand. VM dashboard releases newer
than operation-monitor-20261117 also link it under Ereignisse and Info when
opened through HA Ingress.

The quickest way: the app writes the path to its log when it starts
(**Settings → Apps**, this app, **Log**), in a line beginning with
"Raumsensoren auswählen". Put the address of your Home Assistant in front of
that path, open the app's panel once and then open the result. If the line is
missing, build the address yourself:

1. Open the app's panel once, so that Home Assistant lets the browser in.
2. Find the address of the dashboard frame: in Firefox right-click in the
   dashboard and choose **Dieser Frame → Frame in neuem Tab öffnen**; in
   Chrome or Edge right-click in the dashboard and choose
   **Frame-Quelltext anzeigen**, then take the address after `view-source:`.
   It looks like `https://<your-home-assistant>/api/hassio_ingress/<code>/`.
3. Add `api/local-monitor/raumsensoren` to the end of that address:
   `https://<your-home-assistant>/api/hassio_ingress/<code>/api/local-monitor/raumsensoren`.

The `<code>` stays the same for this installation, so the address can be
bookmarked. If the bookmark answers "401: Unauthorized", the browser's
Ingress session has expired: open the app's panel once more and reload. The
page works before the VM is updated.

### Choosing the sensors

The room page shows the monitoring status at the top and below it every
temperature sensor of Home Assistant with its name, entity ID, current value
and, when Home Assistant knows it, its area. The search field filters by
name, area or entity ID. For each sensor choose:

- **nicht überwachen** (default), **Heizkreis 1**, **Heizkreis 2** or
  **Weitere Räume**. Heizkreis 1 and 2 are rooms that this circuit heats; pick
  sensors away from radiators, windows, doors and direct sun. Weitere Räume
  are rooms without a heating circuit or with closed valves, for example
  cellar or garage; only the sensor itself is checked there.
- For rooms on a heating circuit: **Darf kühler sein um**,
  **Einfluss Heizkreis 1**, **Einfluss Heizkreis 2** and **Ofen oder viel
  Sonne** (no notice when the room is too warm).
  - **Darf kühler sein um** (0 to 10 °C in steps of 0.5 °C, default 0) says
    how far the room may stay below the circuit's set temperature, for
    example 2 for a bedroom kept cooler on purpose or 6 for a sunroom.
  - **Einfluss Heizkreis 1/2** (0 to 100 %, default 100 % on the room's own
    circuit and 0 % on the other) says how strongly that circuit heats the
    room. A room heated by both circuits gets a value on both, for example
    60 % and 40 %. The room's own circuit (the group it is in) must have at
    least the other circuit's influence; otherwise put the room in the other
    group. 0 % on both circuits means frost and sensor checks only (what
    "nur Frost und Sensor prüfen" was in 1.6.8).

  The room is "too cold" only 1.5 degrees below the set temperature minus its
  allowance: with 23 °C set and 3.5 °C allowed, at 18.0 °C or below. The
  notice then names the allowed temperature and the set one, for example
  'Am Raumsensor "Wintergarten" ist es seit längerem zu kalt (16,5 °C; erlaubt
  ab 21,0 °C, Ziel 23,0 °C).' for a room that may be 2 degrees cooler. Without an
  allowance the text stays "(16,5 °C statt 23,0 °C)". The VM writes these
  texts; the app sends only codes and values. The VM shows the allowed temperature
  from its release room-allowance-20261119 on (it accepts 0.5 to 10 degrees);
  an older VM keeps the old text, and both versions work together in any
  order. Influence and learning stay inside the app; the VM needs no update
  for them (learning uses the VM's circuit history when the VM offers it).

  **What influence changes** (the same thresholds for both circuit types):

  | Influence | Effect |
  | --- | --- |
  | 0 % on both circuits | frost and sensor checks only |
  | below 30 % on a circuit | that circuit's heating starts do not judge the room ("Raum wird nicht wärmer"); below 30 % in total the room is also never "too cold" |
  | 30 % or more | judged; after a heating start the room must rise in proportion to its influence (at 100 % the rise of earlier versions, at 50 % half of it, never less than 0.2 degrees, the noise of a sensor) |
  | 50 % or more on a circuit | counts for that circuit's checks "Heizkreis zu kalt" and "Heizkreis zu warm", with the cold limits scaled by its influence (at 60 % "3 degrees too cold" becomes 1.8 degrees) |
  | 80 % or more | a strong room: when a circuit has two or more strong rooms with at most 2 degrees allowance, only those decide "Heizkreis zu kalt" and "Heizkreis wird nicht wärmer" |
  | 30 % or more on the other circuit too | when the circuit has two or more rooms that only it heats, the room neither decides nor holds off that circuit's checks "Heizkreis zu kalt" and "Heizkreis wird nicht wärmer": the other circuit may keep it warm while this one has failed |

  The last two rules are the safety rules: a failed pump or mixer is still
  found through the rooms the circuit heats strongly and alone, even when a
  weakly heated room stays warm by the sun, a stove, a neighbour room or the
  other circuit, and such a room warming after a heating start never ends a
  circuit fault. A room of the other group counts for this circuit's checks
  against this circuit's target minus its allowance.
  With the defaults (0 °C or a 1.6.8 choice, 100 % on the own circuit) every
  check is exactly that of 1.6.8.
- For Weitere Räume: **Frostwarnung** (warn about frost in that room).

Above the list choose **Raumüberwachung** (`beobachten` or `melden`, see
below) and **Heizkreis 1 ist / Heizkreis 2 ist**: `automatisch` takes the type
from the controller (radiators or underfloor); choose Heizkörper or Fußboden
only if the controller setting is wrong. Then press **Speichern**.

Underfloor circuits react slowly. The whole-circuit check (**Störung**: every
monitored room of the circuit at least 3 degrees below the day target) is
judged only during a day phase, and only after 4 hours of heat-up from the
start of that phase; it then needs 3 hours. 4 + 3 = 7 hours fit into an 8-hour
evening phase (for example 15:00-23:00), so a circuit that still has not warmed
the rooms 7 hours after the day phase began is reported. A normal heat-up after
setback never counts, a short morning phase (for example 06:00-09:00) is never
judged, and nothing is judged during the night setback of Auto. Once reported,
the Störung stays through the night and is cleared only when a later day phase
is warm enough. When the controller keeps the circuit off after the setback
without a phase (heating limit, frost protection), the Störung ends when the
time program's next day phase begins, because the controller then decided it
is warm enough (from app 1.6.17 with a VM that sends the program's phase;
otherwise it holds up to 24 hours). A circuit kept in mode **Absenken** is
judged the same way against its setback target (app 1.6.17); if the controller
switches it off there, the Störung ends.
Single rooms of an underfloor circuit are judged the same way:
only in a day phase (or in mode Absenken), after its 4 hours of heat-up; setback and heat-up pause
their 4-hour cold spell instead of restarting it, so it can add up over the
evening phases. While the rooms are still getting warmer (for example after a
holiday) the circuit check waits. Single rooms that stay too cold are reported
as **Hinweis** (see "What is checked"). Radiator circuits need 2 plus 3 hours
and are judged as before.

Saving writes the app's own options through the Supervisor and keeps every
other option (dashboard address, tokens, VM-health monitor) unchanged. The
running room monitoring takes the new selection over at once, without a
restart. Rooms and circuits the new selection changes start their waiting
times again; all other rooms continue. The page says whether the save
worked. If Home Assistant refuses or does not answer, the page says so and
nothing is assumed saved; reload the page to see the stored state. Sensors
that are selected but no longer exist in Home Assistant stay listed under
**Ausgewählt, aber nicht mehr in Home Assistant** so they can be removed.

Who sees what:

- A Home Assistant **administrator** can change and save the selection. The
  page checks this for the logged-in Home Assistant user on every save.
- **Other users** see the same page read-only, with a note that only
  administrators can change the selection.
- If the app cannot check the user (for example while Home Assistant
  restarts), the page is read-only for everyone and says so.

The same settings remain visible and editable in the app's **Configuration**
tab as lists of entity IDs (`heizkreis_1_sensoren`, `heizkreis_2_sensoren`,
`haus_sensoren`, `kuehlere_raeume`, `ohne_ueberwaermung`,
`haus_frost_sensoren`, from app 1.6.8 `kuehler_4_raeume`,
`kuehler_6_raeume`, `nur_frost_raeume`) and from app 1.6.9
`raum_einstellungen`, one entry per room with `sensor`, `kuehler` (0 to 10 in
steps of 0.5), `einfluss_hk1` and `einfluss_hk2` (0 to 100). A change made
there takes effect after the app is restarted (or when the room page saves);
the room page names such a change that is not active yet.

The room page writes a `raum_einstellungen` entry only for a room the 1.6.8
lists cannot express (another allowance than 0, 2, 4 or 6, or another
influence than 100 % on its own circuit and 0 % on the other). A room
without an entry keeps the 1.6.8 meaning of the lists, so 1.6.8 settings
need no change: 2, 4 or 6 degrees stay that number at 100 % on the own
circuit, "nur Frost" becomes 0 % on both circuits. An entry wins over the
lists. For a downgrade the page also writes the lists: the allowance rounded
down to 0, 2, 4 or 6 degrees (every one of them also in `kuehlere_raeume`, so
1.6.7 treats it as 2 degrees), 0 % on both circuits as `nur_frost_raeume`.

**Downgrade from 1.6.9.** The Supervisor drops the unknown option
`raum_einstellungen` when an older version is installed. Then 1.6.8 sees each
room with its allowance rounded down to 0, 2, 4 or 6 degrees and 100 % on its
own circuit (influence and a room's second circuit are lost; a room with 0 %
on both stays frost only); 1.6.7 sees every room with an allowance as 2
degrees cooler and every room as fully heated. Rounding down is stricter,
never more lenient, except that a room with low influence is judged fully
again; such a room can give notices the 1.6.9 settings had avoided. The saved
room state of a room with a free setting starts fresh once in the older
version; `/data/room_learning.json` is ignored by it and used again after an
upgrade. A room may be in at most one of `kuehler_4_raeume`,
`kuehler_6_raeume` and `nur_frost_raeume`.

An invalid `raum_einstellungen` entry entered in the Configuration tab (an
allowance off the 0.5 grid, more than 10, an influence that is not a whole
number from 0 to 100, a room not on a circuit, or a room whose own circuit has
less influence than the other) makes the whole room configuration invalid,
like any other invalid room list: the room checks stop and the
Betriebsüberwachung says so. The room page never saves such a value.

At most 24 sensors can be selected in total. A sensor may be in only one
group. The page refuses selections that break these rules. If the
Configuration tab holds a selection that breaks them, the dashboard keeps
working, room checks stay off and the Betriebsüberwachung shows that the room
sensor settings of the app are invalid.

### beobachten or melden

**Raumüberwachung** starts as `beobachten`: room notices are shown in the
Betriebsüberwachung and the dashboard, but they do not change its overall
state. After a few weeks without wrong notices, switch to `melden`; then room
notices count for the overall state.

### What is checked

The room checks report a heating problem that lasts, not normal life in the
house. Airing, an open door, cooking, showers, sun, guests, a valve
turned down for an afternoon, slow underfloor heating and the first cold
morning after the night setback stay quiet. The waiting times depend on the
circuit type. Radiators react within an hour; underfloor heating needs many
hours.

| Check | Radiators | Underfloor |
| --- | --- | --- |
| No cold check after a change of day/setback phase, target or mode (an existing "Heizkreis zu kalt" holds meanwhile and is judged again afterwards) | 2 h | 4 h, day phase only |
| No warm check after a change of the day target or mode | 2 h | 8 h |
| One cold spell: room 1.5 degrees below target and not getting warmer, for | 90 min | 4 h (adds up over day phases) |
| Room too cold: after a cold spell the room has not come back within 1 degree of the day target for | 12 h | 12 h |
| All rooms of a circuit clearly too cold (3 degrees) and none getting warmer, for (judged in a day phase only against the day target, after that phase's heat-up; during setback (heating, heating limit or inactive), an unknown phase, missing VM data or Home Assistant being away an existing "Heizkreis zu kalt" holds until a later day phase decides, up to 24 h; switched off in a day phase it ends; switched off after a setback without a phase it ends when the program's next day phase begins (app 1.6.17, VM house context 3); mode Absenken is judged against the setback target (app 1.6.17); radiators from app 1.6.12, both from app 1.6.13: the 3 h add up over the day phases of one local day, reset at local midnight, owner decisions 2026-11-23) | 3 h judged in the day phases of one day, each after its 2 h heat-up | 3 h judged in the day phases of one day, each after its 4 h heat-up |
| Watching a room after a heating start | 60 min | 6 h |
| Room does not get warmer: heating starts without effect on different days | 3 | 3 |
| Circuit does not get warmer (fault): starts without effect on different days | 2 | 2 |
| Allowance for the pump to start and the flow to warm up | 30 min | 30 min |
| Room clearly too warm at night (21:00-06:00, 2 degrees) for | 2 h | 4 h |
| All rooms of a circuit too warm for | 6 h | 12 h |
| Room at 28 °C or more while heating, for | 1 h | 1 h |
| Too cold although the heating limit switched off the circuit, for (day phase only; during setback an existing notice holds, up to 24 h, from app 1.6.12) | 6 h | 12 h |
| Airing: at least this long after a drop of 1 degree in 15 min, then until the room is back (at most 12 h) | 60 min | 3 h |
| "Getting warmer": the last 15 min at least 0.3 degrees above the 15 min this long before | 1 h | 3 h |

**Airing.** A drop of 1 degree within 15 minutes is an opened window or door.
From then until the room is back (within 0.5 degrees of where it was, or less
than 1 degree below its target), at most 12 hours, the room counts neither as
cold nor as not responding, and a heating start in that time does not count.
This is read from the temperature alone; no window sensor is needed.

**Heating starts.** A start is the change from setback to day, a target raised
by 1 degree or more, or heating again after an hour off. A room is watched only
when it starts at least 1.5 degrees below its target. Warming is judged on the
whole watch: the median of its last quarter against the median of its first
quarter, so single readings and short dips do not decide. A start without
effect is counted; a start the room warms on, or the room getting back within
1 degree of its day target at any time, clears the count; a start with
airing, a cold flow, the pump off or the room near its target does not count
either way. **Raum wird nicht wärmer** needs starts without effect on three
different days (at least 20 hours apart) within four days without the room
reaching its day level in between, so one aired, open or turned-down morning,
or a door that is open every morning, never reports. It ends at the first
start the room warms on, when the room is within 1 degree of its day target,
or two days after the last start without effect. A room whose radiator is
cold but which the sun brings to its day level every day is therefore not
reported. **Heizkreis wird nicht wärmer** (a fault) needs two starts on
different days on which at least two rooms and at least half of the circuit's
rooms stayed flat and none warmed, and the rooms that stayed flat have not all
been back at their day level since; aired rooms do not count, so the whole
house aired at once is not a fault. A failed pump or mixer is reported
from the second morning; the circuit cold check (3 degrees, 3 h) usually comes
first.

**Rooms with an allowance and the circuit checks**. "All rooms of a circuit clearly too cold" (a fault) is judged over
the circuit's strong rooms (80 % or more) with at most 2 degrees allowance,
each against its own limit, as in earlier versions. A room with more than 2
degrees is one the owner keeps unusually cool, often warmed by sun or a
stove: it neither raises the fault while it sits at its allowed temperature
nor holds it off when sun or a stove keeps it warm while the heating has
failed. Only on a circuit with fewer than two such rooms do the other rooms
with 50 % or more count, each against its own allowed temperature and with
limits scaled by its influence (a sensor of such a room that drops out
pauses the check, as before, instead of letting the other rooms decide).
Rooms without influence never count for any circuit check. Rooms with an
allowance are watched after a heating start only when they start 1.5
degrees below their allowed temperature.

**Room too cold** needs the room to stay away from its day level: after one
cold spell, 12 hours without getting back within 1 degree of the day target.
A room someone turned down for an afternoon, or that was cold until a slow
underfloor heating caught up, is back by evening and is forgotten. A room whose
radiator stays cold is reported the same evening or the next morning, and the
notice stays through the night setback (or a night the heating limit switches
the circuit off) until the room is warm again, or 26 hours after the last cold
spell, for example when the heating stays off for the summer. Home Assistant
or the app being away meanwhile (a restart, an update, a longer outage) does
not end it either. A room
that is meant to stay cooler, or whose sensor sits at a cold spot such as a
window, gets an allowance or a lower influence on the room page, or is set
to "nicht überwachen". The app never learns a room's usual level by itself:
that would also hide a radiator that slowly fails. The owner can let it
propose values for one room
(**Raum anlernen**, below); nothing changes until the owner accepts.

### Raum anlernen

Every room saved on a heating circuit has a **Raum anlernen** part on the
room page. Only a Home Assistant administrator can use it, and only for one
room at a time; it never starts by itself.

1. Choose the **Zeitraum** (**alles Verfügbare**, up to one year, the
   default, or the last 3, 7, 14, 30 or 90 days) and press **Raum
   anlernen**. The app reads:
   - the two **heating circuits** (phase Tag/Absenken, target, status) from
     the VM's own history: the state code every 5 minutes and the day and
     setback targets (configured target plus room correction) from the
     VM's parameter journal, both kept 30 days. Phase: state 1 (Heizen) is
     day; 2 (Absenken Rampe), 3 (Absenken) and 4 (Tag/Nacht Abschaltung)
     are setback, as the VM's own phase mapping selects them; any other
     state counts as neither. Days older than 30, or every day when the VM
     cannot give its history (a VM release older than the endpoint, access
     refused, unreachable, or no target known in the period), come
     from Home Assistant's recorder history of the VM's climate entities,
     as far back as the recorder keeps them (at most 90 days);
   - the **room temperature** from Home Assistant's long-term statistics
     (hourly mean, minimum and maximum; Home Assistant keeps them
     indefinitely for every sensor with `state_class: measurement`) for the
     allowance, and from the recorder history (every change, by default 10
     days) for the heat-up response, where the finer resolution matters. A
     sensor without long-term statistics falls back to the recorder history
     for both.

   Only days with both the room and its circuit known are evaluated, so the
   allowance reaches as far back as the circuit data (30 days from the VM,
   more when Home Assistant keeps the climate entities longer); "vorhanden"
   in the evidence counts the statistics read, the allowance line the days
   evaluated. Usually the result is there within seconds; otherwise reload
   the page later. **Abbrechen** takes effect after the statistics and VM
   reads (at most about 75 seconds).
2. The room page shows a **Vorschlag** with its evidence, for example:
   "darf 4 °C kühler sein, Einfluss Heizkreis 1 10 %, Heizkreis 2 0 % (bisher
   0 °C, 100 %, 0 %)", "Das Tagesmittel liegt bei 18,2 °C, das Ziel bei
   23,0 °C (30 Tage mit Tagbetrieb ausgewertet, aus Stundenmitteln).", "Beim
   Aufheizen durch Heizkreis 2 steigt die Raumtemperatur um 0,4 K/h (9
   Aufheizvorgänge).", and the sources: "Raumtemperatur:
   Langzeitstatistik von Home Assistant, stündlich, 63 Tage vorhanden; feiner Verlauf
   (jede Änderung) 10 Tage für das Aufheizen.", "Heizkreise: VM-Verlauf,
   alle 5 Minuten, 30 Tage." When a source is missing the evidence says
   which and why, for example "Raumtemperatur: keine Langzeitstatistik in
   Home Assistant (dem Sensor fehlt wohl state_class: measurement);
   ersatzweise Verlauf von Home Assistant (jede Änderung), 10 Tage." or
   "Heizkreise: VM-Verlauf nicht verwendet (die VM bietet diese Abfrage noch
   nicht an (ältere VM-Version)); ersatzweise Verlauf der Heizkreise in Home
   Assistant, 10 Tage."
3. Press **Übernehmen** to write the values to the settings (the monitoring
   uses them at once), or **Verwerfen**. Nothing changes without
   **Übernehmen**. Afterwards the room shows "Angelernt am ..." with the
   values and a **Zurücksetzen** button that sets the room back to 0 °C and
   100 % on its own circuit.

How the proposal is made:

- **Allowance**: from the day-phase level of the room against the circuit's
  target, 2 hours after a day phase starts (4 hours for underfloor), on days
  with at least 2 hours of such readings: the deficit that 4 days in 5 stay
  within, minus 1 degree (a room is "too cold" 1.5 degrees below its limit),
  rounded up to 0.5 degrees, 0 to 10 degrees. At least 3 such days are
  needed; otherwise the allowance stays as it is.
- **Influence per circuit**: from the heating starts of that circuit (setback
  to day, or a target 1 degree higher) with the room at least 1 degree below
  the new target: the rise of the room over 2 hours (6 hours for underfloor),
  as a median over the starts. 0.8 degrees per hour (radiators) or 0.25
  degrees per hour (underfloor) is 100 %. At least 3 starts are needed. A
  start within an hour of the other circuit's start cannot be split between
  the circuits and is not used for either, unless the room has no influence
  from the other circuit: then it counts for the room's own circuit. If the
  other circuit turns out to heat the room more, the proposal moves the room
  to that circuit's group.
- Gaps in the history (a sensor offline, a restart of Home Assistant) are left
  out; a history piece that cannot be read is a gap, and when more than a
  quarter of the pieces cannot be read the app says so instead of guessing.

**Zu wenig Daten**: with fewer than 3 days of day phase and fewer than 3
heating starts per circuit (for example a recorder that keeps only a few
days, or a new sensor) the page says so and offers **Ab jetzt aufzeichnen**
for 3, 7 or 14 days. During the recording the room reports no heating problem
of its own ("zu kalt", "wird nicht wärmer", "zu warm"); frost and sensor checks
stay on and the room still counts for its circuit's checks. At the end of the
recording the next opening of the room page analyses exactly that period
(the VM keeps the circuits 30 days, the long-term statistics keep the room;
the heat-up response needs Home Assistant's recorder, by default 10 days, so
open the page within a few days of the end). A recording
can be cancelled (**Aufzeichnung abbrechen**); a running analysis too
(**Abbrechen**).

**Learning sees what the room did, not what it should do.** A room whose
radiator, valve or actuator fails would be learned as a cold room that the
circuit hardly heats, and the checks would then stay quiet about it. That is
why learning is manual, shows its evidence and needs the owner's acceptance.
Warnings mark every proposal that loosens the checks noticeably: "Der Raum
erreicht das Ziel nie" (on no day within 1 degree of the target) or "...
selten" (on fewer than half of the days), "Der Vorschlag erlaubt deutlich
mehr Abweichung" (more than 1 degree more than now, or more than 2 degrees),
"Der Einfluss von Heizkreis N sinkt unter 80 %" (the room leaves the strong
rooms of its circuit), "Der Raum wird beim Aufheizen von Heizkreis N kaum
wärmer" (below 50 % on its own circuit) and "Laut Verlauf heizt auch
Heizkreis N den Raum" (a second circuit of 30 % or more). With a warning,
**Übernehmen** works only after ticking "Ich habe den Raum geprüft; die Werte
sind gewollt". Check the radiator first. A proposal older than 7 days, or one
for a room whose settings changed since, is refused: learn again.
Only an administrator's visit to the room page starts the analysis of an
ended recording (the VM history is then read as that administrator); the
recorder history is read in pieces of 12 hours of at most 4 MB each, and a
very busy climate entity can exceed that and leave gaps.

The learning state (a running recording, a proposal, "angelernt am") is kept
in `/data/room_learning.json` and survives an app restart; a room taken off a
circuit forgets it. Deleting the file is harmless.

**Latencies for a real fault** (as designed), measured from midnight in
synthetic tests:

| Fault | Day 06:00-22:00 | Day 06:00-09:00 and 15:00-23:00 |
| --- | --- | --- |
| Radiator stays cold: **Raum zu kalt** | about 21.5 h (same evening) | about 36.5 h |
| Radiator stays cold: **Raum wird nicht wärmer** | 55 h (third morning) | 55 h |
| Failed pump, radiators: **Heizkreis zu kalt** (fault) | about 11 h | about 19 h |
| Failed pump, radiators: **Heizkreis wird nicht wärmer** (fault) | 31 h (next morning) | 31 h |
| Underfloor room without heat: **Raum zu kalt** | about 26 h | about 55 h |
| Underfloor room without heat: **Raum wird nicht wärmer** | about 60 h | about 69 h |
| Failed pump, underfloor: **Heizkreis zu kalt** (fault) | about 20 h | about 22 h |
| Failed pump, underfloor: **Heizkreis wird nicht wärmer** (fault) | 36-60 h | about 45 h |

A start is judged only when the room starts 1.5 degrees or more below its
target, so with a setback of less than 1.5 degrees only the cold checks apply
(underfloor rows: setback 2-3 degrees).

Frost (5 °C or less for 10 minutes) is checked without any VM data and
unchanged by any of the above; frost in two or more rooms is a fault. Sensor
checks report a sensor that is not found, gives no value, is silent for a day,
reports impossible values, jumps three times within 24 hours (8 degrees or
more at once, or 3 degrees away and back where the shorter leg is faster than
room air can change, 1.5 degrees a minute; a sensor at an open window is not
jumping), has shown exactly the same
value for three days, or has a weak battery. A sensor problem never counts as
a heating problem.

Normal night setback and a normal heat-up do not create notices: the rooms are
compared with the circuit's current day or setback target from the
controller, never with the internal control target. While Home Assistant
starts, the VM data is missing or old, or a target is unknown, rooms are shown
as **Unbekannt**, never as **Normal**, and waiting times pause. Every notice
ends by itself when the cause is gone.

The warm checks compare with the day target also during the night setback,
so the daily change between day and setback does not restart their waiting
time; only a changed day target, a changed mode or heating that starts again
after an hour off does. A room that only gets warmer slowly after a heat-up
is judged from the moment the circuit really heats: while the pump starts and
the flow is still cold (at most 30 minutes) the check waits.

If the room settings are invalid, the app reports that once and the rooms of
the circuits named in the settings are shown as **Unbekannt**.

The room page also shows the last report, whether the VM accepts the reports,
the circuit types and how many minutes in the last 24 hours and 7 days the
target was unknown. The log shows only fixed lines such as
`House monitor: ha_api_unavailable.`, at most once per 5 minutes each.

### Room checks after an app restart

A restart of the app (an update, a restart of the app, a reboot of the host)
does not start the room checks over, so a lasting problem is neither cleared
by the restart nor reported again days later as new. A restart of Home
Assistant Core alone only pauses the checks while Core is unavailable.

The app saves what the checks have seen (waiting times, the starts without
effect, cold spells, airing, the last hours of each sensor and the open
notices with their start time) in `/data/house_state.json`, at most every 5
minutes and when it stops. The file is written as a whole (temporary file, then
renamed), is readable only by the app, holds no address or password and stays
below 4 MB. After a restart the checks continue from it as if Home Assistant
had been unavailable for the time the app was stopped: waiting times pause for
that time and run on, those that would have ended during an outage that long
end, and an open notice keeps its start time. Nothing is credited for the
stopped time; every check is judged again on current readings. If the app
cannot save when it stops (a crash, a power cut), the minutes since the last
save (at most 5) are lost and count as part of the stopped time, exactly as
if Home Assistant had been unavailable from that save on.

The saved state is used only when it fits:

- the app was stopped for at most 24 hours (measured on the host's monotonic
  clock when the host did not reboot, so a clock change does not matter;
  after a reboot by the clock, which must not lie more than 5 minutes before
  the save, and at least the time since the reboot);
- the room settings: a room whose circuit, circuit type, allowance,
  influence, "ohne Überwärmung" or frost setting changed starts fresh; a
  circuit whose rooms or influences changed starts its circuit-wide checks
  fresh. A state saved by app 1.6.6, 1.6.7 or 1.6.8 continues after the update
  to 1.6.9 (the rule constants and the marks of 1.6.8 settings are
  unchanged). A state saved by app 1.6.6 or 1.6.7 continues after the update to 1.6.8. After a
  downgrade to 1.6.7 while a room uses 4 or 6 degrees or frost only, 1.6.7
  starts the checks fresh once (it does not know these choices). A circuit whose
  type changed starts fresh with its rooms; when only its rooms changed, its
  circuit-wide checks (all rooms too cold or too warm, heating starts without
  effect) start fresh, and every unchanged room continues with its own
  notices. Switching between beobachten and melden keeps everything;
- the check rules of the installed version (an update that changes a waiting
  time or limit starts fresh).

Without a file (first start) the checks start fresh without a log line. An unreadable or unsuitable file starts them fresh with one log
line such as `House monitor: state_unreadable.` (`state_incompatible`,
`state_clock`, `state_too_old`); a restored state is
logged as `House monitor: state_restored.` (`state_partly_restored` when some
rooms start fresh). A failed save is logged as `state_save_failed`. Deleting
the file is harmless: the checks then start fresh at the next start.

### Removing the app

Raumsensoren leeren, dann App entfernen: set every sensor on the room page to
**nicht überwachen** and save (or, in the Configuration tab, empty every room
list and `raum_einstellungen`, save and restart the app; a flag list or
`raum_einstellungen` entry left for a removed sensor makes the configuration
invalid), then wait until the Betriebsüberwachung no longer shows room
monitoring (a few minutes). Only then uninstall the app. If the app is removed
while sensors are selected, the VM keeps the checks **Räume** and
**Raumsensoren** at **Unbekannt**.

## Independent Betriebsüberwachung

The app maintains the existing MQTT entity identity and device. It subscribes through HA Core to the default `homeassistant` operation discovery topic and the VM operation reports, and redirects only this entity to its own checked state topic. No controller or settings command is possible. The VM report topic remains untouched. The app needs Home Assistant MQTT to be running; no broker credentials are stored in the app.

A live report is required for Normal. Retained reports cannot start or renew that state. Without a live report for 90 seconds the state is Störung, with a message that the VM/monitoring is unreachable and the actual heating state is unknown. When the optional VM-health monitor is enabled, a failed or stale heartbeat also overrides a recent VM report. Its healthy heartbeat alone cannot clear a missing report. Recovery requires a new live report. The app publishes its checked state without retention every 15 seconds; if the app or HA connection fails, HA marks the entity unavailable after 90 seconds. No old Normal can be replayed from this topic. After the app starts, and after a reconnect to Home Assistant (Core restart, integration reload) while the VM was reporting until then, the app waits the same 90 seconds (Unbekannt) before a missing report counts; a VM that was already silent stays Störung. When the last VM report said maintenance (a VM release restarts its services), a silence of up to 15 minutes is shown as Unbekannt ("Die VM wird gerade gewartet und sendet nach dem Neustart wieder Betriebsdaten."), never beyond the 2-hour cap on maintenance that the VM counts: once the report says the maintenance has lasted 2 hours (`maintenance_capped`), a silent VM is a Störung after the 90 seconds, and a maintenance that began 1 h 55 min ago is excused for its last 5 minutes only. Freshness is measured on the app's own clock from the arrival of a report; the VM's timestamp only has to lie within 5 minutes of this host's clock and must not repeat a recent report, so clock differences or a VM clock that steps back no longer matter. A heartbeat that is still unknown in the first 90 seconds is no failure.

After VM/app restart, room evaluation waits for fresh context and an accepted current app report. Room selections remain in app options; the room checks continue from their saved state ("Room checks after an app restart"), on a first start or without a usable saved state they start fresh. Missing acknowledgement is not proof that the rooms are healthy.

## Notification service check (optional)

The separate harg-notify container sends emails and calls and is the third
participant in health monitoring. With the option `notify_watch: true` the app
also subscribes through HA Core to `hargassner/+/notify/status` and adds the
check **Benachrichtigungsdienst** to its checked verdict:

- **Normal** while a live, non-retained status of the same gateway is younger
  than 90 seconds;
- **Unbekannt** in the first 90 seconds after the app starts without a status;
- **Hinweis** otherwise, with one row "Der Benachrichtigungsdienst meldet sich
  nicht." A Normal verdict becomes Hinweis; Hinweis, Unbekannt and Störung stay
  as they are. When the VM already reports a row of that check, the app adds no
  second one.

Only freshness is judged: the signed envelope `{"notify_status": ...,
"authorization": ...}` of contract version 2 with `schema` 1 and
`published_at` within the lease. The app holds no notify key and does not
check the signature (the VM adapter and API do), so on a broker where other
accounts may write `notify/status` this check can be fooled; the broker ACL
should let only `harg-notify` write that topic. The status holds no addresses
or numbers. Leave the option off until the container
is installed: otherwise the verdict shows that Hinweis permanently. The
container in turn watches this app's checked verdict as its heartbeat.
