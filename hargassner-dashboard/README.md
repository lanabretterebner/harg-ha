# Hargassner Dashboard for Home Assistant

Open the existing local dashboard from Home Assistant with your Home Assistant
login. This app uses Ingress, so there is no separate dashboard password prompt
inside Home Assistant. Opening the dashboard directly still requires its local
login. The Home Assistant panel is available to all authenticated HA users. Each
admitted user receives the existing dashboard control permissions.

The app runs on Home Assistant OS or Supervised (`aarch64` or `amd64`). It
forwards requests to the existing HTTPS dashboard; it does not run another
controller connection or change manufacturer connectivity.

Add `https://github.com/lanabretterebner/harg-ha` to the Home Assistant
App store repositories, then install this app. This is separate from adding
the same URL to HACS for the thermostat popup.

See [DOCS.md](DOCS.md) for installation and configuration. The app and backend
must both be configured before first use. Current container build, app upgrade
and monitor commissioning are separate acceptance steps; source tests do not
establish an installed app or real notification delivery.


App1.5.0 adds an optional independent VM-health monitor, disabled by default.
It uses the dedicated VM health token and Supervisor-provided HA API token,
never a long-lived user credential. See DOCS.md for permission/state/commissioning
limits. HACS frontend installation and this Supervisor app update are separate.

App1.5.1 introduced startup through `/command/with-contenv`, as required by the
S6 base image, so optional monitoring receives the Supervisor-provided token.
No credential file or alternate environment variable is accepted. Startup
failures report a fixed stage (`options`, `supervisor_token`, `monitor_state`,
`ingress_listener`, `signal_handlers` or `worker_start`) without exception text
or configuration values. The1.5.1 startup repair was tested in the digest-pinned
image with synthetic credentials and an isolated network; installation and
monitor commissioning on a real HA host are separate acceptance steps.

App1.5.2 drains a full notification outbox before recording current conditions,
uses one strict bounded options snapshot for ingress and monitoring, and rejects
incomplete upstream responses. App1.5.3 treats an empty optional
`upstream_connect_host` as unset and rejects raw non-ASCII request paths with
HTTP400 instead of reporting the dashboard unreachable. App1.5.4 accepts a VM
clock up to 5 s ahead of the HA host and recovers from a backward step of the
HA host's clock at the next poll instead of holding every component at
`clock_invalid` until the old time has passed; alerts that do not depend on the
clock keep their own reason meanwhile. App1.5.5 makes that monitor ignore a
check kind it does not know, instead of reporting all three components invalid
and notifying for each, while still requiring both known checks; no current
report changes its verdict. These are source-tested fixes; a new container
build, installed upgrade and real notification delivery require separate checks.

App 1.5.6 uses German problem notifications with a next step. Recovery quietly
clears the old warning. Update the app through Supervisor; no HACS update is
needed for this notification change.

App 1.6.0 adds optional room monitoring: choose temperature sensors that
already exist in Home Assistant, per heating circuit. The app checks whether
rooms stay too cold or get too warm while heating, whether they warm up after
heating starts, frost and the sensors themselves, and reports codes and
numbers to the VM, which shows the result in the Betriebsüberwachung. Room
sensors are for monitoring only and control nothing. From this version the app
reads the selected sensor states, opens one Home Assistant Core WebSocket and
publishes through Core's `mqtt.publish`; it still has no broker password, no
controller connection and no control call. It starts in `beobachten` mode,
which shows notices without changing the overall state. Empty the room lists
before removing the app. See DOCS.md.

App 1.6.1: sensors are chosen on the app's room page
(`api/local-monitor/raumsensoren` inside the app's Ingress address, see
DOCS.md "Opening the room page") instead of typing entity IDs. It lists every
temperature sensor of Home Assistant with name, value and area, with a search
field. A Home Assistant administrator chooses per sensor Heizkreis 1,
Heizkreis 2 or Weitere Räume and the extra flags and saves; the running room
monitoring takes the selection over without a restart. Other users see the
page read-only. Saving changes only the app's own options, through the
Supervisor, which every app may do with its own token; no new permission is
requested. The options stay editable in the Configuration tab.

App 1.6.2: every save on the room page checks again that the user is a Home
Assistant administrator (refused if Home Assistant does not answer).
