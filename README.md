# Hargassner for Home Assistant

Frontend 1.4.16 and the optional ingress/monitor app 1.6.6 include the HA clients for an existing Hargassner local-control
backend: a discoverable thermostat dashboard card, the matching native popup,
one-device page grouping, the optional dashboard Ingress app and reference examples.

| Component | Installation | Result |
| --- | --- | --- |
| Thermostat card and popup | HACS, Dashboard category | Native target dial, one Modus menu, icons and Heizkreisstatus |
| Existing device page | Same loaded HACS resource | Logical headings inside its native entity lists; one MQTT device stays intact |
| Hargassner Dashboard | HA App store repositories | Existing dashboard through authenticated HA Ingress |
| Tiles and naming reference | Copy examples after checking entity IDs | Status on everyday climate tiles and separate day/setback/correction controls |

Use [this public repository](https://github.com/lanabretterebner/harg-ha) in
both stores independently. HACS downloads the browser module; Supervisor
builds the included app. Installing either does not install the other or the
MQTT/backend services. No live backend identity, configuration or token is included.

Frontend 1.4.16 works with HA 2026.11: the dial follows the confirmed value
after a dropped tap. HA 2026.11 holds its native target dial while a tap is
waiting to be sent; when the card or popup drops such a tap unsent (the target
or phase changed, the connection dropped, the popup closed, or + and − ended
back on the confirmed value), 1.4.15 left the dial on that never-sent number
and deaf to later updates until the popup was reopened. Nothing else changes;
no command is added. Earlier HA versions behave as before.

Frontend 1.4.15 added **Korrektur ±0** to the card and the popup: offered only
for a confirmed non-zero room correction, it returns the correction to 0 through
the same guarded paths as a native gesture and shows the request as unconfirmed
until the readback confirms it. The device page also groups the five energy
totals of a backend with `energy_totals_v1` (Pelletslager, Kessel, Steuerung);
without that backend nothing changes. The frontend keeps an independent
room-correction control available when
Auto has no qualified day/setback target basis, including summer phases.
The configured target remains unknown until the backend qualifies that basis.
Install/update the HACS resource and fully reload the frontend. The separately
versioned ingress/monitor app (1.6.6) contains separate notification-outbox, strict
configuration and incomplete-response fixes, treats an empty optional connection
host as unset, rejects raw non-ASCII request paths with HTTP400, recovers its
optional monitor from clock skew and backward clock steps and makes that monitor
ignore an unknown check kind instead of reporting every component invalid. Update that app through Supervisor
to receive its fixes; updating HACS alone changes only the thermostat frontend.
Existing tags and release assets remain immutable.

## Thermostat popup through HACS

1. In **HACS → three dots → Custom repositories**, add this public repository
   with category **Dashboard**. Open it, choose **Download**, select the release
   and confirm. Adding a custom repository alone does not install its JavaScript;
   verify that HACS shows an installed version.
2. Check **Settings → Dashboards → three dots → Resources** for one module
   registration. Preserve a HACS-generated `?hacstag=…` suffix. If absent, add
   this URL as a **JavaScript module**:

   ```text
   /hacsfiles/harg-ha/hargassner-thermostat-popup.js?v=1.4.16
   ```

3. Remove older manual resource URLs, fully reload each browser/Companion App,
   open a dashboard, then open the matching climate dialog.

For dialogs opened directly from Settings before a dashboard, merge
[frontend-global.yaml](examples/frontend-global.yaml) into the existing
`frontend.extra_module_url` list. Preserve other entries, check HA configuration,
restart HA and reload frontends. A dashboard resource alone does not guarantee
loading before a dashboard is visited.
[HA global modules](https://www.home-assistant.io/integrations/frontend/#loading-extra-javascript).

For manual installation, copy `dist/hargassner-thermostat-popup.js` to
`/config/www/hargassner-thermostat-popup.js` and use
`/local/hargassner-thermostat-popup.js?v=1.4.16` instead. Remove that manual
registration when switching to HACS.

The single **Modus** menu offers **Aus · Auto · Heizen · Absenken · 1x Heizen ·
1x Absenken**. Power, calendar/clock, sun, moon and timed-heating icons render
from embedded original SVG paths without runtime downloads. The `1x` labels
remain explicit. A user choice invokes HA's existing preset handler; loading,
opening or refreshing sends no command. Native temperature controls remain.

**1x Heizen and 1x Absenken end on their own, silently.** Offline firmware
analysis of V14.1HAR.q1 shows that both are a forced day or setback phase
lasting until the start of the circuit's next heating window, at which point the
controller itself sets the circuit back to Auto and rewrites the mode parameter,
so the menu reads **Auto** again. Where the circuit's time programme contains no
window, a 24-hour fallback does the same. Finding Auto selected without having
touched it is therefore the normal end of a one-time mode, not a lost command or
a failed write. No countdown or end time is shown: the instant depends on that
circuit's time programme and on the controller's own clock, which has no NTP
client and whose error is permanent, so a displayed "ends at …" would be an
invented value.

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
popup. Its native dial is labeled **Zieltemperatur**; the separate **Ist** reading
shows **Nicht verfügbar** when no measured room value exists. **Raumkorrektur**
is shown independently. No reported temperature or mode is invented. Dial writes
remain correction-only and backend-qualified.

The [complete dashboard example](examples/hargassner-dashboard.json) supplies
two custom thermostat stacks, separate base/correction controls and grouped
readings. Its entity IDs are generic suggestions: check the actual HA registry,
including collision suffixes, before importing a configuration.

## Groups inside the existing device page

The resource adds **Heizkreis1, Heizkreis2, Puffer, Kessel, Solar, Warmwasser,
Pelletslager and Steuerung** headings inside the native Control/Sensor/etc
entity lists of the matching existing Hargassner device. Original native rows,
buttons and disabled-entity expansion remain. A group can occur in more than
one native category card. No entity is moved to another HA device, renamed or
republished, and no MQTT reload is required.

Grouping uses immutable MQTT identity suffixes and the entity domain, so renamed
entity IDs and numeric collision suffixes remain supported. The five always-published energy totals join Pelletslager,
Kessel and Steuerung ([Energy dashboard](docs/ENERGY-DASHBOARD.md)). Unknown matching
entries stay visible. [Frontend group reference](examples/entity-ui-groups.json).
Only selected/published entities appear; the90-entry reference does not enable
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
index is pinned by digest. The app exposes no host ports or Supervisor-management,
Docker or host privileges. The app uses the Supervisor-provided token for the HA
API: optional VM-health notifications and, from app 1.6.0, optional room
monitoring, which reads the selected sensor states, opens one Core WebSocket and
publishes through Core's `mqtt.publish`. Both are off by default. From app
1.6.1 its room page also reads and saves the app's own options at the
Supervisor (which every app may do with its own token; no `hassio_api`
permission is requested) and checks through Core whether its viewer is a Home
Assistant administrator. No long-lived HA user token is needed.

The existing backend must allow the app's source IP and HA browser origin and
provide a private ingress token. Set the app's HTTPS `upstream_url` and
`ingress_token` locally, start it and choose **Open Web UI** or **Show in sidebar**.
The ordinary dashboard URL keeps its password login. The app creates no second
controller/manufacturer connection.
[Complete app instructions](hargassner-dashboard/DOCS.md).

The optional monitor reads only a dedicated token-authenticated health endpoint
over verified HTTPS, preserving original checker times. It runs on HA rather
than the observed VM and delivers persistent unknown/fault/recovery notifications
through the fixed internal Supervisor HA API. Healthy first baseline is silent.
Its private durable outbox preserves stable IDs across restart. Missing evidence
means unknown, not heater-down; an HA outage prevents HA-host delivery. Enable
it only after the matching VM endpoint/private token and root commissioning.
Read-only monitor status uses the existing authenticated Ingress peer/user gate.

Actual container build, app installation and a live Ingress session remain
unverified by this packaging checkpoint. Home Assistant OS or a Supervisor
installation is required; installing the HACS popup alone does not install this app.

## Native tiles and entity IDs

The older optional native tiles remain available: copy
[thermostat-cards.yaml](examples/thermostat-cards.yaml) into a dashboard's
Manual card editor. Both climate tiles show target temperature and
`Heizkreisstatus`; card and icon taps open the native dialog. Six number tiles
keep day, setback and correction controls separate. No inline preset selector
is added. HACS does not import card YAML.

Check all eight IDs first. The [90-entry naming reference](examples/entity-name-mapping.json)
lists suggested IDs; existing registry names and suffixes can differ. It is
documentation, not HA configuration to import or an instruction to select every
entity. MQTT discovery, freshness and correction-only dial writes remain backend duties.

## Compatibility and troubleshooting

The popup contract was researched against HA frontend20260930.3. The preceding
1.2.0 release was downloaded in HA2026.9.3/HACS2.0.5, with both matching native
popups showing one Modus menu, icons and status. 1.4.16 was also rendered and operated in a throwaway HA Core 2026.11.2 (frontend 20261028.6) with synthetic data. Current 1.4.16 installation and native-component acceptance must be recorded separately from this source package. Release-specific fixture checks are in
[validation.json](validation.json). Unsupported layouts and unknown current
presets retain native controls. Future HA DOM changes may require an update.

If both native menus remain, verify the installed HACS version and that its
exact registered module URL returns HTTP200. A404 means the file is not being
served; clearing cache cannot supply it. A current preset of `none` also retains
native menus intentionally; correct backend state delivery instead of fabricating a mode.

Uninstall the popup through HACS, remove its registrations, apply any global
configuration change and reload. `window.hargassnerThermostatPopup?.stop()`
restores native controls temporarily in the current tab. Remove the dashboard
app independently through its App store entry.

## Validation and publication

The HACS filename override selects `dist/hargassner-thermostat-popup.js`.
Root `repository.yaml` independently exposes the dashboard app to Supervisor.
The transfer ZIP is not a HACS `zip_release` archive.
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
serves only a loopback HTTP fixture and checks card, popup and device grouping
assertions plus page/console/request errors. It emits a structured result.
`npm test` remains the Node-only suite. You may also serve this repository and
open the HTML fixtures manually. These synthetic checks contact no live HA or
controller and do not establish installed-native rendering or physical heating
response. See [browser checks](tests/README.md).
Frontend 1.4.16 and app 1.6.11 are published as independent components. Installation and
served-byte acceptance are separate from source checks; consult the installed
version in HACS and Supervisor. Existing tags and assets remain immutable.
The app is updated through Supervisor; HACS updates only the browser resource.

The current frontend retains native controls during readback, correction-only
writes, truthful unknown targets and room readings, six-mode selection and
compact layouts. The dial uses -3 to +3 degrees in 0.5 steps. Pending and
uncertain writes never gain success from elapsed time or automatic retry.
Historical release details belong in [CHANGELOG.md](CHANGELOG.md).
[Third-party icon licenses](THIRD-PARTY-NOTICES.md).

App 1.6.3 makes Betriebsüberwachung independent of the VM: missing live reports or failed VM heartbeat cannot remain Normal. Update the Supervisor app separately; HACS frontend 1.4.14 is unchanged. The app uses the existing default `homeassistant` MQTT discovery prefix.

App 1.6.4 adds the optional check Benachrichtigungsdienst (option `notify_watch`, off by default) for the separate harg-notify notification container, no false Störung after app starts, Home Assistant reconnects or VM maintenance restarts, tolerance for small clock differences, and a room-monitor dwell fix (see [CHANGELOG.md](hargassner-dashboard/CHANGELOG.md)). It was not published on its own; app 1.6.5 contains it.

App 1.6.5 makes the room checks report only lasting heating problems: airing, open doors, sun, a valve turned down for an afternoon and slow underfloor heating no longer raise notices. Frost checks are unchanged. Update the Supervisor app. It is published together with frontend 1.4.15.

App 1.6.6 keeps the room checks across an app restart: their state is saved in the app's `/data`, so an app update, an app restart or a host reboot no longer ends a lasting room notice and reports it days later as new. It also carries the frost and Betriebsüberwachung fixes in [CHANGELOG.md](hargassner-dashboard/CHANGELOG.md). Update the Supervisor app; frontend 1.4.15 is unchanged.

App 1.6.8 replaces the room page's "darf kühler sein" checkbox with a per-room choice (normal, 2, 4 or 6 °C cooler, or frost and sensor only); the "zu kalt" notice names the allowed temperature with a VM that reads it (context major 2), and app and VM may be updated in either order (the VM side is live since 2026-11-19). Update the Supervisor app; HACS frontend 1.4.15 is unchanged.
App 1.6.9 replaces the fixed per-room choice with free values: an allowance of 0 to 10 °C in 0.5 steps and an influence of 0 to 100 % per heating circuit (a room can be on both circuits; a real circuit failure is still found through the strongly heated rooms), and adds **Raum anlernen**, which proposes these values from Home Assistant's history with its evidence and applies nothing before the owner accepts. 1.6.8 settings are taken over unchanged; the VM needs no update. Update the Supervisor app; HACS frontend 1.4.15 is unchanged.
App 1.6.10 lets **Raum anlernen** read the heating circuits from the VM's own 30-day history (read-only VM endpoint `circuit-history`, live since 2026-11-19; with an older VM it falls back to Home Assistant's history and says so) and the room temperature from Home Assistant's long-term statistics (hourly, kept indefinitely), with periods up to 90 days or everything available. Update the Supervisor app; HACS frontend 1.4.15 is unchanged.
App 1.6.11 accepts the VM Telnet monitor's held `fresh` reports (frame up to 50 s old) so a slow VM disk no longer raises health alerts; install it before the VM release with the new monitor. Control behaviour is unchanged.
App 1.6.7 holds the VM status check's clock-skew finding until the report is clearly inside the 5 s tolerance (half of it, or 5 minutes within it), so a clock hovering at the limit gives one notification and one recovery instead of flapping at every poll. Update the Supervisor app; frontend 1.4.15 is unchanged.
