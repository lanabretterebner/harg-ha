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
must both be configured before first use. Version 1.3.1 is a source candidate; its build and actual installation remain
separate acceptance steps. Earlier version 1.3.0 installation evidence does not
establish this candidate has been installed.
