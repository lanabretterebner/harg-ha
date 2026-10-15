# Hargassner for Home Assistant

Frontend1.4.5 and the optional ingress/monitor app1.5.1 include the HA clients for an existing Hargassner local-control
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

Frontend1.4.5 keeps an independent room-correction control available when
Auto has no qualified day/setback target basis, including summer phases.
The configured target remains unknown until the backend qualifies that basis.
Install/update the HACS resource and fully reload the frontend. The separately
versioned ingress/monitor app remains1.5.1; no app update is needed for this
frontend release. Existing v1.4.4 tag and release assets remain immutable.

## Thermostat popup through HACS

1. In **HACS → three dots → Custom repositories**, add this public repository
   with category **Dashboard**. Open it, choose **Download**, select the release
   and confirm. Adding a custom repository alone does not install its JavaScript;
   verify that HACS shows an installed version.
2. Check **Settings → Dashboards → three dots → Resources** for one module
   registration. Preserve a HACS-generated `?hacstag=…` suffix. If absent, add
   this URL as a **JavaScript module**:

   ```text
   /hacsfiles/harg-ha/hargassner-thermostat-popup.js?v=1.4.5
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
`/local/hargassner-thermostat-popup.js?v=1.4.5` instead. Remove that manual
registration when switching to HACS.

The single **Modus** menu offers **Aus · Auto · Heizen · Absenken · 1x Heizen ·
1x Absenken**. Power, calendar/clock, sun, moon and timed-heating icons render
from embedded original SVG paths without runtime downloads. The `1x` labels
remain explicit. A user choice invokes HA's existing preset handler; loading,
opening or refreshing sends no command. Native temperature controls remain.

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
entity IDs and numeric collision suffixes remain supported. Unknown matching
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
Docker or host privileges. App1.5.1 requests HA API permission solely for optional
monitor notifications using the Supervisor-provided token; monitoring is disabled
by default. No long-lived HA user token is needed.

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
popups showing one Modus menu, icons and status. Current1.4.5 installation and native-component acceptance must be recorded separately from this source package. Release-specific fixture checks are in
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

CI uses `npx playwright install --with-deps chromium`. To use an existing local
Chromium instead, set `HARG_BROWSER_EXECUTABLE=/usr/bin/chromium`. The runner
serves only a loopback HTTP fixture and checks card, popup and device grouping
assertions plus page/console/request errors. It emits a structured result.
`npm test` remains the Node-only suite. You may also serve this repository and
open the HTML fixtures manually. These synthetic checks contact no live HA or
controller and do not establish installed-native rendering or physical heating
response. See [browser checks](tests/README.md).
Publish frontend1.4.5 as a new `v1.4.5` release after its final source,
package and frontend tests pass. Preserve all existing tags and release assets.
The separately versioned Supervisor app remains1.5.1 with unchanged source.
HACS users update the resource and fully reload each browser/Companion App.
[Third-party icon licenses](THIRD-PARTY-NOTICES.md).

Frontend1.4.5 keeps the native thermostat and temperature-control nodes mounted while readback catches up. A requested number is shown as **Angefragt** until coherent qualified readback confirms it; an uncertain outcome says **Bestätigung unklar**, without automatic retry or silent rollback. A definitive rejection restores the confirmed number. Current modes/status stay truthful, and genuine schedule/base changes reconcile normally. During qualification loss the previous confirmed number remains visibly read-only; offline never becomes command authority. The separate Modus selector remains usable whenever its own native authority is available. The separately updated optional ingress app1.5.1 retains disabled-by-default independent
VM-health monitoring; see its instructions for private options and commissioning.


Frontend1.4.5 also retires old popup temperature callbacks synchronously when
connection/user identity or connectivity changes before a native Lit render.
Native connection/config contexts are observed directly; existing hass setters
are preserved. Repeated connected snapshots of the same identity keep a queued
adjustment. Stop/reparent releases only owned bindings. This adds no new command
authority, automatic retries or target confirmation, and remains part of frontend1.4.5.


When a qualified configured target is unavailable, the card and popup can use
only the separately discovered, available room-correction number entity.
The adjustment remains −3 to +3°C in0.5°C steps. It never supplies a guessed
configured target or room reading. Correction availability follows that number
entity's own current backend authority; absent or unavailable correction entities
remain read-only. The six-mode selector retains its own independent availability.
