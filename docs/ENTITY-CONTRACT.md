# MQTT entities and heating-circuit status

The separately installed local-control backend publishes MQTT discovery and
qualified state. Configure HA's MQTT integration against its existing broker;
this client repository does not install a broker or another controller connection.

Each matching climate entity uses native HA `off`, `heat`, `auto` HVAC states
and six manufacturer presets: Aus, Auto, Heizen, Absenken, 1x Heizen and
1x Absenken. It exposes `hargassner_control_schema: configured_room_setpoint_v1`.
The popup recognizes that contract rather than fixed entity IDs. Unsupported
entities and unknown current presets retain native controls.

The backend additionally exposes these climate attributes:

| Attribute | Meaning |
| --- | --- |
| `Heizkreisstatus` | Readable German controller state label |
| `Heizkreisstatus-Code` | Current original integer code when qualified |
| `Heizkreisstatus verfügbar` | Whether the current status observation is qualified |

The popup displays the heating-circuit status separately from the selected
operating mode. A status does not replace the native climate state and does not
assert that the pump is running. Unavailable status must remain explicit; it
must not be guessed from temperature or the last known mode.

The optional native [tile examples](../examples/thermostat-cards.yaml) show
`temperature` and `Heizkreisstatus` using Tile's supported `state_content`
attribute list. They open the native more-info dialog and add no inline preset
selector. [Official Tile configuration](https://www.home-assistant.io/dashboards/tile/).

The thermostat target is the active configured day/setback base plus room
correction. Its dial writes only the correction in−3…+3°C at0.5°C steps;
separate day/setback number entities edit the base temperatures. Command
availability remains the backend's decision and needs qualified fresh state.

The [90-entry naming reference](../examples/entity-name-mapping.json) describes
suggested entity IDs. Existing HA registry IDs are authoritative and can retain
older names or suffixes. The mapping is documentation, not HA configuration to
import, and does not select additional entities. Verify actual IDs before copying
tiles or updating automations. The optional whole-boiler program selector is
separate from each climate's six operating modes and its permissions.

The custom `custom:hargassner-thermostat-card` uses the same contract without
changing HA's `off`/`heat`/`auto` domain state. Its displayed target, measured room
temperature and correction are separate values. Frontend device-page grouping
also leaves one existing MQTT device and all registry/discovery identities intact.

## Requested-target presentation

`Sollwertbasis` identifies the currently qualified configured target context. The backend remains the only command authority. Legacy `Sollwertaktualisierung` markers are display hints; they never authorize a temperature command.

Frontend1.4.3 also understands additive `Temperaturauftrag` schemaVersion1: controller-unit `temperatureUnit: °C`, opaque `context`, monotonic operation `sequence` and full-envelope `revision`, bounded last8 `records`, plus same-snapshot `confirmedTarget`, `confirmedBasis` and `confirmedPreset` echoes. A record contains its sequence, original basis/requested target, status (`pending`, `confirmed`, `rejected`, `sent_unconfirmed`), safe reason/origin and timestamps. A confirmed correction record alone is insufficient: the displayed actual target/basis/preset must match the coherent echoes before settling. HA Fahrenheit display converts the Celsius metadata explicitly.

The client captures a local draft before native debounce, keeps it pending/uncertain without automatic retry, and rejects stale snapshots/outcomes. An older rejected request cannot undo newer input. Memory-only intent is scoped to the authenticated HA connection/user/entity; nothing changes shared HA states or persists an intent. Native card/control nodes remain mounted while callback/API leases rotate. Only bound definitive rejection allows rollback. Fresh genuine mode/base/day-night changes remain truthful; no cached target grants permission.

During a source/readback gap, the last confirmed number remains a labelled inert private projection. The previous eight-second unmount is removed. Actual Off stays Off; offline/unqualified targets cannot send. The independently available six-mode path is not disabled by uncertain temperature presentation. Missing initial confirmed bounds never invents a dial range.

Native +/- gestures update the private requested number immediately and coalesce
into one send 250 ms after the last gesture. Continuous slider movement remains
native local feedback; slider release sends immediately. Neither requested
feedback nor service response substitutes for qualified controller confirmation.

Once a temperature request is submitted, its requested number stays visible and
all temperature surfaces for the same HA session/entity become inert until
qualified coherent matching readback, a bound definitive rejection, or a genuine
qualified mode/base/day-night replacement settles it. A bound terminal backend
uncertainty plus a new coherent qualified basis also permits a new explicit
gesture, even when the actual target differs from the uncertain requested one. Old repeated state,
service promise resolution/error, elapsed time and reconnect alone do not
release this lock. Further gestures are not queued or retried. This deliberately
replaces the earlier ability to supersede a submitted request using its still
cached basis; unsubmitted gestures still coalesce. The six-mode selector remains
independently available.

A terminal uncertain request with a new coherent qualified basis remains
labelled with its uncertain requested number until another deliberate gesture
replaces it; this never claims that the old requested target was confirmed.
Local timeout/connection/service errors alone do not prove backend termination.

If none of these qualified settlement or recovery conditions arrives, an
uncertain request can keep temperature control inert indefinitely. Its requested number
and uncertainty note remain visible; independently qualified mode control stays
available.
