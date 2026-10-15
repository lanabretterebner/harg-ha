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
