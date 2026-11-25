# Hargassner for Home Assistant

Home Assistant clients for an existing Hargassner local-control backend:
thermostat frontend 1.4.17 (a dashboard card, the matching native popup and
device-page grouping), the optional Supervisor app 1.6.17 (the dashboard
through HA Ingress, with optional monitoring) and reference examples.

| Component | Installation | Result |
| --- | --- | --- |
| Thermostat card and popup | Marketplace (HA 2026.11+) or HACS, Dashboard category | Native target dial, one Modus menu, icons and Heizkreisstatus |
| Existing device page | Same loaded resource | Logical headings inside its native entity lists; one MQTT device stays intact |
| Hargassner Dashboard | HA App store repositories | Existing dashboard through authenticated HA Ingress |
| Tiles and naming reference | Copy examples after checking entity IDs | Status on everyday climate tiles and separate day/setback/correction controls |

Use [this public repository](https://github.com/lanabretterebner/harg-ha) in
both stores independently. The Marketplace (HACS before HA 2026.11) downloads
the browser module; Supervisor
builds the included app. Installing either does not install the other or the
MQTT/backend services. No live backend identity, configuration or token is included.

## Current versions

- **Frontend 1.4.16** works with HA 2026.11: the dial follows the confirmed
  value after a dropped tap. HA 2026.11 holds its native target dial while a
  tap waits to be sent; when the card or popup dropped such a tap unsent (the
  target or phase changed, the connection dropped, the popup closed, or + and -
  ended back on the confirmed value), 1.4.15 left the dial on that never-sent
  number until the popup was reopened. No command is added; earlier HA
  versions behave as before.
- **App 1.6.11** accepts the VM Telnet monitor's `fresh` reports with a frame
  up to 50 s old, so a slow VM disk no longer raises health alerts. Install it
  before a VM release with that monitor. Control behavior is unchanged.
- **Frontend 1.4.17**: the card updates only when
  its own heating circuit, room correction, connection or settings change, not
  on every state change in the house (less work on a phone). Behaviour is
  unchanged.
- **App 1.6.12**: **Heizkreis zu kalt** is judged
  in the day phase only for radiators too (owner decision), so a failed pump's
  Störung stays raised overnight while the circuit keeps heating during
  setback, instead of ending and returning the next day;
  Betriebsüberwachung delivers each check's newest state instead of working
  through outdated queued events; the room page saves any whole influence (not
  only multiples of 5), asks before Zurücksetzen, shows no old VM confirmation
  while the VM sends no data, and its panel texts are German; one damaged
  learning entry no longer discards the others. Update the Supervisor app.
- **App 1.6.13**: **Heizkreis zu kalt** also holds
  overnight when setback switches the circuit off (until a later day phase
  decides, at most 24 hours), and the 3 hours towards a new fault add up over
  the day phases of one day, so short split programmes report a failed pump the
  same day (owner decisions). Saved room state continues. The ingress
  listener also closes peers other than the Supervisor's gateway before they
  take a connection slot (security audit 2026-11-23), and its notifications,
  status texts and room page read as complete German sentences. Update the
  Supervisor app; HACS frontend 1.4.17 is unchanged.
- **App 1.6.14**: no behaviour change; its `health_alerts.py` is again
  byte-identical to the project's monitoring evaluator. Updating the
  Supervisor app is optional; HACS frontend 1.4.17 is unchanged.
- **App 1.6.15**: Heizkreis zu kalt also holds overnight when the controller
  switches an Auto circuit off without a phase (heating limit, inactive or
  frost protection after setback), instead of ending at night and returning
  the next day. Saved room state continues. Update the Supervisor app; HACS
  frontend 1.4.17 is unchanged.
- **App 1.6.16**: the same overnight hold in manual Absenken, 1x Absenken
  and an unknown mode; in Aus, Heizen or 1x Heizen a circuit switched off
  still ends it. Saved room state continues. Update the Supervisor app; HACS
  frontend 1.4.17 is unchanged.

- **App 1.6.17**: a Heizkreis zu kalt held
  overnight ends when the program's next day phase begins (with a VM that sends
  it); manual Absenken is judged against the setback target; maintenance past
  the VM's 2 h cap gets its own notification text. Saved room state continues.
  Update the Supervisor app; HACS frontend 1.4.17 is unchanged.

The Marketplace (or HACS) updates only the browser resource; update the app through Supervisor.
Existing tags and release assets remain immutable. History:
[frontend changelog](CHANGELOG.md),
[app changelog](hargassner-dashboard/CHANGELOG.md).

## Thermostat popup through the Marketplace or HACS

From HA 2026.11 the built-in **Marketplace** replaces HACS (HACS installs and
custom repositories are migrated on the first start of 2026.11). The steps are
the same in both; only the menu names differ.

1. HA 2026.11 or newer: **Settings → Marketplace → Add from link**, enter
   `https://github.com/lanabretterebner/harg-ha` and choose the type
   **Dashboard** (adding a repository from a link asks to connect GitHub once).
   Older HA with HACS: **HACS → three dots → Custom repositories**, category
   **Dashboard**. Open it, install/download the release and confirm. Adding a
   repository alone does not install its JavaScript; verify that an installed
   version is shown.
2. Check **Settings → Dashboards → three dots → Resources** for one module
   registration of this card. The Marketplace registers
   `/local/community/harg-ha/hargassner-thermostat-popup.js?v=…` and moves an
   existing HACS registration (`/hacsfiles/harg-ha/…?hacstag=…`) there by
   itself; keep its generated `?v=…` suffix. If absent, add this URL as a
   **JavaScript module**:

   ```text
   /local/community/harg-ha/hargassner-thermostat-popup.js?v=1.4.17
   ```

   `/local/community/…` serves the installed file with HACS and with the
   Marketplace. The Marketplace still serves the old `/hacsfiles/…` path for
   compatibility only.
3. Remove older manual resource URLs, fully reload each browser/Companion App,
   open a dashboard, then open the matching climate dialog.

For dialogs opened directly from Settings before a dashboard, merge
[frontend-global.yaml](examples/frontend-global.yaml) into the existing
`frontend.extra_module_url` list. Preserve other entries, check HA configuration,
restart HA and reload frontends. A dashboard resource alone does not guarantee
loading before a dashboard is visited. The global loader loads the card on every
page, dashboards included: with it, the Marketplace adds no dashboard resource of
its own, and an older registration of this card under Resources can be removed.
Keep the `?v=` of the global entry at the installed version after each update;
a stale version loads the older card class first.
[HA global modules](https://www.home-assistant.io/integrations/frontend/#loading-extra-javascript).

For manual installation, copy `dist/hargassner-thermostat-popup.js` to
`/config/www/hargassner-thermostat-popup.js` and use
`/local/hargassner-thermostat-popup.js?v=1.4.17` instead. Remove that manual
registration when switching to the Marketplace or HACS.

The single **Modus** menu offers **Aus · Auto · Heizen · Absenken · 1x Heizen ·
1x Absenken**. Power, calendar/clock, sun, moon and timed-heating icons render
from embedded original SVG paths without runtime downloads. A user choice
invokes HA's existing preset handler; loading, opening or refreshing sends no
command. Native temperature controls remain.

**1x Heizen and 1x Absenken end on their own, silently.** The controller keeps
the forced day or setback phase until the start of the circuit's next heating
window (or for 24 hours if its time programme has no window), then sets the
circuit back to Auto itself, so the menu reads **Auto** again. That is the
normal end, not a lost command. No end time is shown, because it would have to
be guessed. Details: [entity contract](docs/ENTITY-CONTRACT.md).

**Heizkreisstatus** appears separately below the controls. Missing, unqualified
or invalid status says **Nicht verfügbar**. It does not replace HA's native
HVAC state or assert that a pump runs. The backend must supply the schema,
six presets and documented status attributes.
[Entity/status contract](docs/ENTITY-CONTRACT.md).

## Add thermostat cards through the visual editor

After updating/loading the resource, open a dashboard, choose **Edit → Add card**
and search for **Hargassner Thermostat**. Select the card, choose the matching
climate entity in its visual editor and save. Repeat for the second circuit.
No YAML is required. [Detailed card installation](docs/CARD-INSTALL.md).

The card shows the same six-choice Modus menu/icons and read-only status as the
popup, around HA's native thermostat dial for the target. The separate **Ist**
reading shows **Nicht verfügbar** when no measured room value exists.
**Raumkorrektur** is shown independently. No reported temperature or mode is
invented. Dial writes remain correction-only (-3 to +3 °C in 0.5 °C steps) and
backend-qualified.

When Auto has no qualified day/setback target basis (for example in summer
phases), the card and popup keep a separate room-correction control; the
configured target stays unknown until the backend qualifies it. For a
confirmed non-zero correction they offer **Korrektur ±0**, which returns the
correction to 0 through the same guarded path as a native gesture and shows
the request as unconfirmed until the readback confirms it.

The [complete dashboard example](examples/hargassner-dashboard.json) supplies
two custom thermostat stacks, separate base/correction controls and grouped
readings. Its entity IDs are generic suggestions: check the actual HA registry,
including collision suffixes, before importing a configuration.

## Groups inside the existing device page

The resource adds **Heizkreis 1, Heizkreis 2, Puffer, Kessel, Solar, Warmwasser,
Pelletslager and Steuerung** headings inside the native Control/Sensor/etc
entity lists of the matching existing Hargassner device. Original native rows,
buttons and disabled-entity expansion remain. A group can occur in more than
one native category card. No entity is moved to another HA device, renamed or
republished, and no MQTT reload is required.

Grouping uses immutable MQTT identity suffixes and the entity domain, so renamed
entity IDs and numeric collision suffixes remain supported. The five energy
totals of a backend with `energy_totals_v1` join Pelletslager, Kessel and
Steuerung ([Energy dashboard](docs/ENERGY-DASHBOARD.md)). Unknown matching
entries stay visible. [Frontend group reference](examples/entity-ui-groups.json).
Only selected/published entities appear; the 90-entry reference does not enable
additional entities. Unsupported device/layout contexts retain the native view.
A fresh Settings page still needs the resource loaded; use the global loader
above for loading before any dashboard is visited.

## Optional dashboard app

In **Settings → Apps → App store → three dots → Repositories**, add:

```text
https://github.com/lanabretterebner/harg-ha
```

Install **Hargassner Dashboard**. Supervisor builds the source for `amd64` or
`aarch64`; no prebuilt app image is required. The verified official base image
index is pinned by digest. Home Assistant OS or a Supervisor installation is
required. The app exposes no host ports or Supervisor-management, Docker or
host privileges. It uses the Supervisor-provided token for the HA API (the
Betriebsüberwachung, optional VM-health notifications, optional room monitoring
and the room page); no long-lived HA user token is needed.

The existing backend must allow the app's source IP and HA browser origin and
provide a private ingress token. Set the app's HTTPS `upstream_url` and
`ingress_token` locally, start it and choose **Open Web UI** or **Show in sidebar**.
Every HA user who may open the panel gets an ordinary dashboard session; the
dashboard administrator functions need the direct dashboard login, which keeps
its password. The app creates no second controller/manufacturer connection.
[Complete app instructions](hargassner-dashboard/DOCS.md).

Optional features, all off by default:

- **VM-health monitor** (`monitor_enabled`): reads only a dedicated
  token-authenticated health endpoint over verified HTTPS, preserving original
  checker times. It runs on HA, not on the observed VM, and creates persistent
  notifications for unknown or failed checks; recovery silently dismisses them.
  A healthy first baseline is silent. Its private durable outbox keeps stable
  IDs across restarts. Missing evidence means unknown, not heater-down; an HA
  outage prevents its own notifications. Enable it only after the matching VM
  endpoint and private token are configured.
- **Room monitoring**: checks existing HA temperature sensors per heating
  circuit (chosen on the app's room page) and reports codes and numbers to
  the VM's Betriebsüberwachung. Room sensors are for monitoring only and
  control nothing.
- **Benachrichtigungsdienst check** (`notify_watch`): adds the freshness of the
  separate harg-notify notification container to the Betriebsüberwachung.

The app also judges the Betriebsüberwachung entity itself: without a live VM
report for 90 seconds it shows Störung. It uses the default `homeassistant`
MQTT discovery prefix.

## Native tiles and entity IDs

The older optional native tiles remain available: copy
[thermostat-cards.yaml](examples/thermostat-cards.yaml) into a dashboard's
Manual card editor. Both climate tiles show target temperature and
`Heizkreisstatus`; card and icon taps open the native dialog. Six number tiles
keep day, setback and correction controls separate. No inline preset selector
is added. The Marketplace and HACS do not import card YAML.

Check all eight IDs first. The [90-entry naming reference](examples/entity-name-mapping.json)
lists suggested IDs; existing registry names and suffixes can differ. It is
documentation, not HA configuration to import or an instruction to select every
entity. MQTT discovery, freshness and correction-only dial writes remain backend duties.

## Compatibility and troubleshooting

The popup contract was researched against HA frontend 20260930.3. Frontend
1.4.16 was also rendered and operated in a throwaway HA Core 2026.11.2
(frontend 20261028.6) with synthetic data. Installation of 1.4.16 and
native-component acceptance are recorded separately from this source package.
Release-specific fixture checks are in [validation.json](validation.json).
Unsupported layouts and unknown current presets retain native controls. Future
HA DOM changes may require an update.

If both native menus remain, verify the installed version and that its
exact registered module URL returns HTTP 200. A 404 means the file is not being
served; clearing cache cannot supply it. A current preset of `none` also retains
native menus intentionally; correct backend state delivery instead of fabricating a mode.

Uninstall the popup through the Marketplace (or HACS), remove its registrations, apply any global
configuration change and reload. `window.hargassnerThermostatPopup?.stop()`
restores native controls temporarily in the current tab. Remove the dashboard
app independently through its App store entry (empty its room selection first,
see [DOCS.md](hargassner-dashboard/DOCS.md#removing-the-app)).

The frontend keeps native controls during readback, correction-only writes,
truthful unknown targets and room readings, six-mode selection and compact
layouts. Pending and uncertain writes never gain success from elapsed time or
automatic retry. [Third-party icon licenses](THIRD-PARTY-NOTICES.md).

## Validation and publication

The `hacs.json` filename override selects `dist/hargassner-thermostat-popup.js`;
the HA 2026.11 Marketplace reads the same `hacs.json` from each release tag.
Root `repository.yaml` independently exposes the dashboard app to Supervisor.
[HACS structure](https://www.hacs.xyz/docs/publish/plugin/),
[HA app repositories](https://developers.home-assistant.io/docs/apps/repository/).

Run `python3 tools/validate.py`, `npm run check` and `npm test`. Install the development-only
`tools/requirements.txt`, then run `python3 tools/test_package.py`. The app
loopback suite uses Python/OpenSSL and synthetic temporary HTTPS endpoints:

```sh
python3 -B -m unittest discover -s hargassner-dashboard -p 'test_*.py'
```

Run the synthetic browser suites automatically from this repository:

```sh
npm ci
npx playwright install chromium
npm run test:browser
```

For hosts missing browser libraries, `npx playwright install --with-deps chromium`
installs them (requires host package authority). To use an existing local
Chromium instead, set `HARG_BROWSER_EXECUTABLE=/usr/bin/chromium`. The runner
serves only a loopback HTTP fixture and checks card, popup, device grouping and
room page assertions plus page/console/request errors. It emits a structured
result. `npm test` remains the Node-only suite. These synthetic checks contact
no live HA or controller and do not establish installed-native rendering or
physical heating response. See [browser checks](tests/README.md).
