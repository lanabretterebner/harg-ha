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
incomplete upstream responses. These are source-tested fixes; a new container
build, installed upgrade and real notification delivery require separate checks.
