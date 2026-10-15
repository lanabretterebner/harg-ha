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
URL independently. App1.5.0 build, upgrade and monitor commissioning remain
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
Options are read once per load, limited to64KiB and rejected on duplicate keys
or nonfinite numbers. Ingress and monitoring use the same configuration snapshot.

Start the app, choose **Open Web UI**, and enable **Show in sidebar** if desired.
Home Assistant will open the dashboard inside its authenticated interface.
Use that sidebar entry or Open Web UI for access; do not hardcode the temporary
`/api/hassio_ingress/...` address in a website card.

`panel_admin: false` makes the sidebar panel available to all authenticated
Home Assistant users, including nonadministrators. Ingress authentication is
still required; this does not expose an anonymous or LAN port. The backend applies the same existing dashboard control
permissions to every admitted Home Assistant user. The app does not map Home
Assistant entity permissions onto individual boiler controls.

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
backend. The app now logs a fixed diagnostic category such as `dns`, `timeout`,
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
App1.5.0 requests the HA API permission solely for its optional monitor; ordinary
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
certificate/hostname verification. They make no live Home Assistant or
controller requests. The current container build, app upgrade and actual
nonadministrator access remain unverified by these synthetic tests.

The separately installed backend has its own ingress and browser verification.
This source package does not contain a controller identity, credential, live
configuration or backend deployment. No successful local test establishes that
a particular HA ingress session or backend setting is commissioned.

The app uses the official `base-python:3.14-alpine3.24` image, which supports
the two declared architectures. Its OCI multi-architecture index was verified
on2026-10-14 and is pinned by digest in the Dockerfile; see
[base-image verification](../validation/base-image.json). Packaging and security decisions follow the
official [base image list](https://github.com/home-assistant/docker-base),
[app configuration](https://developers.home-assistant.io/docs/apps/configuration/),
[Ingress requirements](https://developers.home-assistant.io/docs/apps/presentation/#ingress),
and [authenticated user headers](https://developers.home-assistant.io/docs/apps/security/#authenticating-a-user-when-using-ingress),
checked on 2026-10-14.


## Optional independent HA-host monitor (app1.5.0)

The monitor runs on the HA host while the observed dashboard VM is separate.
It never connects to the controller or MQTT. Existing ingress origin, token,
connect-host and non-admin access options remain unchanged. Monitoring is
**disabled by default**; updating the app alone does not send a notification.

Set `monitor_enabled: true`, a separately generated random URL-safe
`monitor_token` matching the VM's dedicated health-token configuration, and
`monitor_poll_interval` between15 and60 seconds (default30). The monitor token
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
strict, limited to64KiB and rejected on duplicate keys/nonfinite numbers or a
body shorter than its declared content length.
A child-process deadline bounds DNS, TLS, headers and body reads to5s.

The accepted sanitized health schema keeps original certificate/Telnet checker
timestamps. Fresh API reads cannot renew stale runs. Defaults distinguish120s
heartbeat,90000s certificate execution,45s Telnet execution and2s future clock
tolerance. Missing/invalid/stale or failed heartbeat evidence is **unknown**;
it does not prove the heater is down. Normal maintenance is a suppression state.
A healthy first observation establishes a silent baseline. Changed fault,
unknown, maintenance and recovery conditions update fixed persistent IDs
`harg_health_heartbeat`, `harg_health_certificate`, `harg_health_telnet`.

Delivery is ONLY local `POST
http://172.30.32.2/core/api/services/persistent_notification/create`, using the
Supervisor token. This explicit fixed private-transport policy applies only to
the internal Supervisor proxy; the VM path is always verified HTTPS. The app
cannot select arbitrary HA services or remote notification URLs. A2xx response
accepts the queued delivery; response bodies are not read. Notifications contain
fixed component/status/reason codes and event IDs, never credentials, hostnames,
controller identifiers, raw measurements or upstream exceptions.

State is separate from all controller/charge journals: evaluator-owned0700
`/data/health-monitor`,600 atomic/fsynced state and lock files. Keep it across
app restarts and normal upgrades. Events are durable before sending, with a
stable retry ID and component notification ID. One event at most is delivered
per poll; failed/ambiguous delivery retains that event. The bounded64-event
outbox refuses corruption or unsafe ownership without sending or deleting events.
When full, it continues delivering the existing queue one event per poll; current
condition changes are recorded once enough space is available. Failed delivery
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

Root commissioning must first verify the normal heartbeat/healthy baseline and
zero new notifications. A controlled **monitor-only** wrong-token401 followed
by restoration can then verify genuine endpoint-unverified and recovery
notifications, stable IDs/outbox dedup and status readback without stopping the
VM or changing ingress/controller state. It does not establish a heater outage.
Only the release owner performs that explicit notification test. Synthetic
loopback tests do not prove that the actual Supervisor permission/token/API or
receiver has been commissioned.

An HA-host or HA-Core outage prevents its own notifications; independent from
the VM does not mean independent from HA. A separate external watchdog is a
future operator choice. Container build, real notification delivery, receiver
permissions and actual app update remain unverified until separately recorded.

The reused project `health_alerts.py` is byte-pinned to SHA-256
`8f81aa20024f15032ae32a8103787ce5900cfb02457a2142a9f463cfd25f604c`.
Its evaluator/outbox semantics are retained; this app adapter adds the fixed
health-token fetch, existing HTTPS connector, bounded one-event scheduler and
Supervisor destination. No original producer observation time is rewritten.
