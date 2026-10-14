# Hargassner for Home Assistant

Version1.2.0 includes the HA clients for an existing Hargassner local-control
backend: one Modus menu with offline icons and heating-circuit status, the
optional dashboard Ingress app, native tiles and an entity-naming reference.

| Component | Installation | Result |
| --- | --- | --- |
| Thermostat popup | HACS, Dashboard category | One Modus menu, mode icons and Heizkreisstatus in native climate dialogs |
| Hargassner Dashboard | HA App store repositories | Existing dashboard through authenticated HA Ingress |
| Tiles and naming reference | Copy examples after checking entity IDs | Status on everyday climate tiles and separate day/setback/correction controls |

Use [this public repository](https://github.com/lanabretterebner/harg-ha) in
both stores independently. HACS downloads the browser module; Supervisor
builds the included app. Installing either does not install the other or the
MQTT/backend services. No live backend identity, configuration or token is included.

## Thermostat popup through HACS

1. In **HACS → three dots → Custom repositories**, add this public repository
   with category **Dashboard**. Open it, choose **Download**, select the release
   and confirm. Adding a custom repository alone does not install its JavaScript;
   verify that HACS shows an installed version.
2. Check **Settings → Dashboards → three dots → Resources** for one module
   registration. Preserve a HACS-generated `?hacstag=…` suffix. If absent, add
   this URL as a **JavaScript module**:

   ```text
   /hacsfiles/harg-ha/hargassner-thermostat-popup.js?v=1.2.0
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
`/local/hargassner-thermostat-popup.js?v=1.2.0` instead. Remove that manual
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

## Optional dashboard app

In **Settings → Apps → App store → three dots → Repositories**, add:

```text
https://github.com/lanabretterebner/harg-ha
```

Install **Hargassner Dashboard**. Supervisor builds the source for `amd64` or
`aarch64`; no prebuilt app image is required. The verified official base image
index is pinned by digest. The app exposes no host ports and requests no HA API,
Supervisor API, Docker or host privileges.

The existing backend must allow the app's source IP and HA browser origin and
provide a private ingress token. Set the app's HTTPS `upstream_url` and
`ingress_token` locally, start it and choose **Open Web UI** or **Show in sidebar**.
The ordinary dashboard URL keeps its password login. The app creates no second
controller/manufacturer connection.
[Complete app instructions](hargassner-dashboard/DOCS.md).

Actual container build, app installation and a live Ingress session remain
unverified by this packaging checkpoint. Home Assistant OS or a Supervisor
installation is required; installing the HACS popup alone does not install this app.

## Native tiles and entity IDs

Copy [thermostat-cards.yaml](examples/thermostat-cards.yaml) into a dashboard's
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
1.1.0 release was downloaded in HA2026.9.3/HACS2.0.5, with both matching native
popups showing one Modus menu. Release-specific fixture checks are in
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

Run `python3 tools/validate.py` and `npm run check`. Install the development-only
`tools/requirements.txt`, then run `python3 tools/test_package.py`. The app
loopback suite uses Python/OpenSSL and synthetic temporary HTTPS endpoints:

```sh
python3 -B -m unittest discover -s hargassner-dashboard -p 'test_*.py'
```

Serve this repository locally and open `tests/test-thermostat-popup.html` for
the browser fixture. Tests contact no live HA/controller. Structure, syntax and
loopback tests do not establish hosted installation or physical heating response.
Publish a GitHub release tagged `v1.2.0` with the standalone JavaScript asset;
Supervisor builds the committed app source from the repository.
[Third-party icon licenses](THIRD-PARTY-NOTICES.md).
