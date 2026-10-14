# Hargassner Thermostat Popup

Version1.1.0 gives a matching Home Assistant thermostat popup one visible
**Modus** selector with six choices:

**Aus · Auto · Heizen · Absenken · 1x Heizen · 1x Absenken**

It replaces both native HVAC and preset selectors in that popup. Selecting a
choice invokes the existing manufacturer preset action. Temperature controls
keep their native behavior. The MQTT backend still represents the six choices
as presets; no second preset selector is shown by this resource.

This is a **HACS Dashboard plugin**, a frontend JavaScript module. The
Hargassner MQTT adapter and its discovered entities are separate prerequisites.
A Supervisor app/add-on packages a service; HACS downloads this browser module.
The optional dashboard/ingress app is separate from both.

Actual HACS download and installation in the owner's Home Assistant remain
unverified. The resource has isolated browser fixture coverage.

## Install with HACS

Repository: [lanabretterebner/harg-ha](https://github.com/lanabretterebner/harg-ha).
A local ZIP is a transfer copy; add the public repository URL to HACS.

1. Open **HACS → three dots → Custom repositories**. Enter the public GitHub
   repository URL and select **Dashboard**. Add it and download this plugin.
2. Open **Settings → Dashboards → three dots → Resources**. In storage mode,
   HACS can create its module resource automatically. Check for exactly one
   entry for this plugin; preserve a HACS-generated `?hacstag=…` suffix.
   If absent, add this URL as a **JavaScript module**:

   ```text
   /hacsfiles/harg-ha/hargassner-thermostat-popup.js?v=1.1.0
   ```

3. Remove the older manually registered popup resource or its
   `frontend.extra_module_url` entry, if present. Fully reload each browser or
   Companion App frontend, open a dashboard, then open the thermostat popup.

For YAML-managed dashboard resources, add the same URL with `type: module` to
the existing resource list. For loading outside dashboards as well, use that
URL once in the existing `frontend.extra_module_url` list, check configuration
and restart Home Assistant. Avoid duplicate loading methods.
[Global module configuration](https://www.home-assistant.io/integrations/frontend/#loading-extra-javascript).

HACS stores dashboard resources under `www/community/` and serves them through
`/hacsfiles/`. Resource registration is separate from importing dashboard cards.
[HACS dashboard resources](https://www.hacs.xyz/docs/use/repositories/type/dashboard/),
[custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/).

## Backend contract and check

The climate entity must already expose:

```yaml
hargassner_control_schema: configured_room_setpoint_v1
hvac_modes: [off, heat, auto]
preset_modes: [Aus, Auto, Heizen, Absenken, 1x Heizen, 1x Absenken]
```

The backend may additionally list HA's synthetic `none` preset. Do not add the
schema marker to unrelated entities. The module recognizes the marker and
mode values rather than fixed entity IDs. Unknown current presets or a changed
native popup structure retain the normal native controls.

Open each matching climate popup. It should show one **Modus** selector, all six
choices and the native temperature controls, with no visible native HVAC or
preset selector. An explicit choice sends only that preset action. Loading,
opening, refreshing and restoring the popup send no command.

The configured-room-setpoint backend displays the active configured base plus
room correction and writes only the correction from the thermostat dial.
This frontend module does not choose a day/setback phase or grant permission.

[Optional native tiles](examples/thermostat-cards.yaml) open this popup and keep
separate day, setback and correction number controls. Verify their eight
suggested entity IDs against the actual Home Assistant registry before use.
Paste the YAML into a dashboard's Manual card editor. HACS does not import it.

## Compatibility and removal

The native popup contract was inspected against Home Assistant
frontend20260930.3. Isolated browser fixtures exercise the module; actual HACS
download and the owner's HA frontend/registry remain unverified. Future HA DOM
changes may require an update; unsupported structures retain native controls.

Remove the module/resource registration and uninstall it through HACS, then
reload the frontend. To restore native controls temporarily in the current
browser tab, run `window.hargassnerThermostatPopup?.stop()` in its console.

## Publish and validate

The `hacs.json` filename override selects
`dist/hargassner-thermostat-popup.js` in the `harg-ha` repository. Set a GitHub
description and topics such as `home-assistant`, `hacs`,
`dashboard` and `hargassner`, with issues enabled. Commit all files, run the
included checks, and publish a GitHub **release** tagged `v1.1.0`, attaching the
standalone JavaScript file. A tag alone is not a versioned GitHub release.
Until a release exists HACS can use the default branch. The repository ZIP is
for transfer/publication; `zip_release` is intentionally absent from `hacs.json`.
[HACS repository requirements](https://www.hacs.xyz/docs/publish/start/),
[Dashboard plugin structure](https://www.hacs.xyz/docs/publish/plugin/).

Run `python3 tools/validate.py` and `npm run check`. For the isolated browser
fixture, serve this repository with `python3 -m http.server 5186 --bind 127.0.0.1`
and open `/tests/test-thermostat-popup.html`. A maintainer can additionally run
the [official HACS action](https://github.com/hacs/action) with category `plugin`
after publication. Hosted GitHub metadata and actual HACS installation cannot
be validated from this local packaging checkpoint.
