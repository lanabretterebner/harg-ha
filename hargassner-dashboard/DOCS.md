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
URL independently. The 1.3.1 change is a source candidate; actual upgrade and nonadministrator
acceptance remain separate from earlier 1.3.0 installation evidence.

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

The app publishes no LAN ports, requests no Supervisor or Home Assistant API
permissions, and accepts requests only from the actual TCP peer `172.30.32.2`.
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
controller requests. The 1.3.1 container build, upgrade and actual nonadministrator access remain
unverified by these synthetic tests.

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
