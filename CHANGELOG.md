# Changelog

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
