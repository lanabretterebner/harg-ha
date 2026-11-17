# Hargassner for Home Assistant

Frontend 1.4.14 and the optional ingress/monitor app 1.6.1 include the HA clients for an existing Hargassner local-control
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

Frontend 1.4.14 keeps an independent room-correction control available when
Auto has no qualified day/setback target basis, including summer phases.
The configured target remains unknown until the backend qualifies that basis.
Install/update the HACS resource and fully reload the frontend. The separately
versioned ingress/monitor app 1.6.1 contains separate notification-outbox, strict
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
   /hacsfiles/harg-ha/hargassner-thermostat-popup.js?v=1.4.14
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
`/local/hargassner-thermostat-popup.js?v=1.4.14` instead. Remove that manual
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
popups showing one Modus menu, icons and status. Current 1.4.14 installation and native-component acceptance must be recorded separately from this source package. Release-specific fixture checks are in
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
Publish frontend 1.4.14 as a new `v1.4.14` release after its final source,
package and frontend tests pass. Preserve all existing tags and release assets.
The independently versioned Supervisor app 1.5.5 includes its own ingress/monitor
fixes and requires a separate Supervisor update.
HACS users update the resource and fully reload each browser/Companion App.
[Third-party icon licenses](THIRD-PARTY-NOTICES.md).

Frontend 1.4.14 keeps the native thermostat and temperature-control nodes mounted while readback catches up. A requested number is shown as **Angefragt** until coherent qualified readback confirms it; an uncertain outcome says **Bestätigung unklar**, without automatic retry or silent rollback. A definitive rejection restores the confirmed number. Current modes/status stay truthful, and genuine schedule/base changes reconcile normally. During qualification loss the previous confirmed number remains visibly read-only; offline never becomes command authority. The separate Modus selector remains usable whenever its own native authority is available. The separately updated optional ingress app 1.5.3 retains disabled-by-default independent
VM-health monitoring; see its instructions for private options and commissioning.


Frontend 1.4.14 also retires old popup temperature callbacks synchronously when
connection/user identity or connectivity changes before a native Lit render.
Native connection/config contexts are observed directly; existing hass setters
are preserved. Repeated connected snapshots of the same identity keep a queued
adjustment. Stop/reparent releases only owned bindings. This adds no new command
authority, automatic retries or target confirmation, and remains part of frontend 1.4.14.


When a qualified configured target is unavailable, the card and popup can use
only the separately discovered, available room-correction number entity.
The adjustment remains −3 to +3°C in0.5°C steps. It never supplies a guessed
configured target or room reading. Correction availability follows that number
entity's own current backend authority; absent or unavailable correction entities
remain read-only. The six-mode selector retains its own independent availability.


Frontend 1.4.14 gives minus and plus separate rounded, filled, subtly bordered
buttons with a visible gap. It preserves compact thermostat spacing and
its existing native target controls, independent room-correction fallback,
six-mode selector and status. Update the HACS resource and fully reload each
browser/Companion App to replace an already registered card class. The separately
versioned ingress/monitor app 1.5.5 includes independent fixes and is updated
separately through Supervisor. Layout changes do not grant command
authority or supply missing target/room values.

Frontend 1.4.8 places popup room-correction controls before Modus and status,
and centers the card mode/status rows vertically. While that fallback is visible,
only the dedicated unavailable native target region is collapsed. Native target
spacing and original inline styles return when qualification changes. Existing
native controls stay mounted. The optional app 1.5.2 is byte-identical to the
frontend 1.4.7 checkpoint and requires no new app update for this layout release.

Frontend 1.4.9 puts **Ist** directly below the displayed set/correction number
inside the card dial. Missing or unqualified room readings explicitly say
**Ist: Nicht verfügbar** in the same reserved line; no room value is inferred.
Cards retain a fixed 400px height, including pending/rejected commands,
long statuses, offline states and helper failures. Native controls remain mounted
through qualified updates. Popup layout and all 15 app 1.5.2 files are unchanged.

Frontend 1.4.10 fixes the room-correction dial: a touch in the open bottom gap
selects the nearer end (−3 or +3) instead of always +3, a touch on the centre
value text selects nothing, and zero is shown as **0 °C**, never **-0 °C**. The
card's six-mode selector is released when its own mode request settles, even if
the climate briefly became unavailable meanwhile. Correction bounds and steps,
command authority and readback confirmation are unchanged. App 1.5.3 contains
only ingress option/request-path fixes; its monitor evaluator is unchanged.

Frontend 1.4.11 fixes three further room-correction dial inputs. A drag that
passes −3 or +3 keeps that end while the pointer is in the open bottom gap, on
either side of straight-down, instead of flipping to the opposite end; only a
new press in the gap selects the nearer end. A dial that cannot be measured
selects nothing. A release that cannot be sent, or a request pending from
another card or popup of the same circuit, discards the local draft, and minus
and plus step from the confirmed or requested value, never from a drag draft.
Correction bounds and steps, command authority and readback confirmation are
unchanged. All app 1.5.3 files are unchanged from frontend 1.4.10.

Frontend 1.4.12 stops the dial from wrapping. A drag that crosses the whole
bottom gap and returns to the ring on the far side keeps the end it left
through, instead of jumping to the opposite end. It follows the pointer again
once the pointer is nearer to that end along the ring than across the gap. A
new press on the ring still selects the value under it. Bounds, steps, command
authority and readback confirmation are unchanged, and so are all app 1.5.3
files.

Frontend 1.4.13 changed only its version number; card and popup behavior are
those of 1.4.12. That release carried app 1.5.4, whose optional monitor accepts
a relay clock up to 5 s ahead of the HA host and recovers from a backward step
of the HA host's clock at the next poll. Earlier app versions stayed at
`clock_invalid` until the old time had passed and reported no relay outage in
that time. Alerts that do not depend on the clock keep their own reason. The
monitor's state, reason codes and report format are unchanged. Update the app
through Supervisor; the HACS update alone does not change it.

Frontend 1.4.14 again changes only its version number; card and popup behavior
are those of 1.4.12. The release carries app 1.5.5, whose optional monitor
ignores a check kind it does not know instead of reporting all three components
invalid, so a later relay-side check cannot turn one unknown key into a blanket
unknown with a notification for each component. Both known checks are still
required and no current report changes its verdict. The release also documents
three limits it does not change: the monitor cannot see a controller that has
left the broker, `1x Heizen` and `1x Absenken` end silently when the controller
returns the circuit to Auto, and the one-time charge trigger's `0` report is its
own reset rather than a finished charge. Update the app through Supervisor; the
HACS update alone does not change it.

App 1.5.6 replaces internal health codes and hashes with readable German problem
messages and a next step. Recovery clears the old warning silently. A missing
common connection produces one warning; obsolete queued events do not raise
stale alerts or clear newer faults. Update the app through Supervisor; the
HACS frontend remains 1.4.14.

App 1.6.0 adds optional room monitoring with existing Home Assistant
temperature sensors per heating circuit, reported to the VM's
Betriebsüberwachung. Room sensors control nothing. It starts in `beobachten`
mode. Update the app through Supervisor; the HACS frontend remains 1.4.14.

App 1.6.1 replaces typing entity IDs into the app options: the app's room page
`api/local-monitor/raumsensoren` (inside the app's Ingress address) lists every
temperature sensor with name, value and area, with a search field, and a Home
Assistant administrator picks the sensors and saves there. Other users see the
page read-only. The options stay visible and editable in the Configuration
tab. See the app's DOCS.md, "Opening the room page".
