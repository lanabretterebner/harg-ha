# Changelog

## 1.4.6

- Compact thermostat cards with aligned mode/status rows and Mushroom-style filled adjustment controls.
- Remove visible step/range captions and redundant explanatory text; preserve enforced limits and accessible labels.
- Preserve target/correction authority, unknown-value presentation, six-mode selection and native control behavior.
- Keep the separately versioned ingress/monitor app1.5.1 unchanged.

## 1.4.5

- Keep a separately qualified room-correction control available in the card and popup when Auto has no qualified configured target basis.
- Preserve unknown configured targets, missing room readings, independent mode availability and backend command qualification.
- Keep the separately versioned Supervisor ingress/monitor app1.5.1 unchanged.

## App1.5.1 checkpoint (frontend1.4.4 unchanged)

Preserve the Supervisor environment through S6's with-contenv wrapper so enabled
health monitoring starts alongside ingress. Startup errors log only fixed stage
names. Six startup regressions and an exact digest-pinned image/init comparison
cover the missing-environment failure and correction. Thermostat frontend bytes,
monitor transport, TLS, permissions and durable state contracts are unchanged.
Existing frontend tags and release assets are preserved.


## 1.4.4

- Retire popup temperature API and debounce leases synchronously for coalesced connection/user and connectivity changes before Lit renders.
- Subscribe only to owned native connection/config contexts; preserve existing hass setters and clean up on stop/reparent. Keep native nodes mounted and repeated-session drafts intact.
- Add portable lifecycle regressions and eight real-DOM/context checks; extend maintained bounded installed-native probes.
- Keep the separately versioned Supervisor ingress/monitor app1.5.0 byte-identical.

## 1.4.3

- Preserve connection/user-scoped temperature API and debounce leases across reconnect and ABA, retaining uncertain requests without false rejection.
- Keep submitted target stable and inert until qualified reconciliation; serialize burst adjustments and preserve independent six-mode controls.
- Ship maintained bounded installed-native probes and automated local-only browser fixtures.
- Include separately versioned Supervisor app1.5.0 with optional disabled-by-default independent health monitoring, durable notification outbox and authenticated read-only status.

## 1.4.2

- Keep existing native card/control nodes during target changes; rotate only guarded callback/API leases.
- Capture desired targets before debounce and retain them through pending or uncertain readback, with explicit status. Revert only on a bound rejection; never retry automatically.
- Share memory-only ordered intent between card/popup surfaces; reconcile genuine mode/base changes and keep independent mode controls usable.
- Preserve last confirmed inert targets for other-circuit/source gaps without an eight-second unmount. The ingress app remains1.4.0 unchanged.

## 1.4.1

- Keep the last confirmed target visibly inert during qualified mode, base, offset and phase readback, with a fixed eight-second limit.
- Cover both the custom thermostat card and native more-info popup; keep current reported mode/status truthful.
- Permanently retire callbacks from earlier target contexts, including fully-qualified updates without a missing-target stage.
- Leave the optional Supervisor ingress app at1.4.0 with identical source and configuration.

## 1.4.0

- Preserve the last confirmed thermostat dial during a bounded, qualified room-offset readback. Block stale and already-debounced service calls while it reconciles.
- Admit all authenticated HA users to the ingress panel.
- Support a fixed LAN TCP address with original TLS hostname verification; add bounded read reconnect and credential-free diagnostics.

## 1.3.0

Add a discoverable Hargassner Thermostat dashboard card with native visual
configuration, target dial, separate measured room/correction, the same six-mode
menu/icons and status. Add logical headings inside the existing device page
while retaining native rows/actions and one unchanged MQTT device. Include a
full grouped dashboard example and dedicated UI installation instructions.

## 1.2.0

Add offline native mode icons and a read-only Heizkreisstatus line to the
single Modus popup. Include the existing dashboard Ingress proxy as a Supervisor
app repository, status-aware native tiles, entity-naming reference and separate
installation instructions. Native preset forwarding and temperature behavior
remain unchanged. The intermediate1.1.1 icon draft was not published.

## 1.1.0

Replace both native thermostat mode selectors with one Modus menu containing
the six Hargassner choices. Only an explicit selection invokes the existing
preset action. Package the module as a HACS Dashboard custom repository and
provide optional native tiles without inline preset selectors.

## 1.0.0

Earlier manual resource hid the duplicate HVAC menu while retaining the native
preset menu. It is superseded by the single Modus menu.
