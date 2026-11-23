# Hargassner Dashboard for Home Assistant

Open the existing local dashboard from Home Assistant with your Home Assistant
login. This app uses Ingress, so there is no separate dashboard password prompt
inside Home Assistant. Opening the dashboard directly still requires its local
login. The Home Assistant panel is available to all authenticated HA users;
each of them gets an ordinary dashboard session (administrator functions need
the direct dashboard login).

The app runs on Home Assistant OS or Supervised (`aarch64` or `amd64`). It
forwards requests to the existing HTTPS dashboard; it does not run another
controller connection or change manufacturer connectivity.

Add `https://github.com/lanabretterebner/harg-ha` to the Home Assistant
App store repositories, then install this app. This is separate from adding
the same URL to HACS for the thermostat popup.

See [DOCS.md](DOCS.md) for installation and configuration. The app and backend
must both be configured before first use. Source tests do not establish an
installed app or real notification delivery.

Optional features, all off by default (details in DOCS.md):

- **VM-health monitor**: polls the VM's dedicated health endpoint with its
  own token and shows problems as Home Assistant notifications. It uses the
  Supervisor-provided HA API token, never a long-lived user credential.
- **Room monitoring**: choose temperature sensors that already exist in Home
  Assistant on the app's room page (`api/local-monitor/raumsensoren` inside the
  app's Ingress address). The app reports only lasting heating problems (too
  cold, not getting warmer, too warm), frost and sensor faults as codes and
  numbers to the VM, which shows them in the Betriebsüberwachung. Per room an
  allowance and an influence per heating circuit can be set, and **Raum
  anlernen** proposes them from the history, applying nothing before an
  administrator accepts. Only Home Assistant administrators can save. The
  checks continue across app restarts. Room sensors are for monitoring only
  and control nothing; the app has no broker password, no controller
  connection and no control call. Empty the room selection before removing
  the app.
- **Benachrichtigungsdienst check** (`notify_watch`): shows the freshness of
  the separate harg-notify notification container in the Betriebsüberwachung.

The app also judges the Betriebsüberwachung entity itself: without a live VM
report for 90 seconds it shows Störung.

Changes per version: [CHANGELOG.md](CHANGELOG.md).
