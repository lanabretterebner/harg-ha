# MQTT entities and heating-circuit status

The separately installed local-control backend publishes MQTT discovery and
qualified state. Configure HA's MQTT integration against its existing broker;
this client repository does not install a broker or another controller connection.

Each matching climate entity uses native HA `off`, `heat`, `auto` HVAC states
and six manufacturer presets: Aus, Auto, Heizen, Absenken, 1x Heizen and
1x Absenken. It exposes `hargassner_control_schema: configured_room_setpoint_v1`.
The popup recognizes that contract rather than fixed entity IDs. Unsupported
entities and unknown current presets retain native controls.

`1x Heizen` and `1x Absenken` are one-time modes that the controller ends by
itself. Offline firmware analysis of V14.1HAR.q1 establishes that each is a
forced day or setback phase until the start of the circuit's next heating
window, where the firmware calls its own `set_mode(Auto)` and rewrites the mode
parameter, so the selected preset reads `Auto` again; with no window in the
circuit's time programme a 24-hour fallback does it. A later observation of
`Auto` after one of these presets is that normal silent end, and must not be
presented as a lost command, a rejected write or a mode the user chose. No end
time or countdown is derived or displayed: the instant depends on the circuit's
time programme and on the controller clock, which has no NTP client and whose
error is permanent, so any such value would be invented.

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
correction. Its dial writes only the correction, -3 to +3 °C in 0.5 °C steps;
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

The frontend also understands the additive `Temperaturauftrag` with `schemaVersion` 1: controller-unit `temperatureUnit: °C`, opaque `context`, monotonic operation `sequence` and full-envelope `revision`, at most the last 8 `records`, plus same-snapshot `confirmedTarget`, `confirmedBasis` and `confirmedPreset` echoes. A record contains its sequence, original basis/requested target, status (`pending`, `confirmed`, `rejected`, `sent_unconfirmed`), safe reason/origin and timestamps. A confirmed correction record alone is insufficient: the displayed actual target/basis/preset must match the coherent echoes before settling. HA Fahrenheit display converts the Celsius metadata explicitly.

The client captures a local draft before native debounce, keeps it pending/uncertain without automatic retry, and rejects stale snapshots/outcomes. An older rejected request cannot undo newer input. Memory-only intent is scoped to the authenticated HA connection/user/entity; nothing changes shared HA states or persists an intent. Native card/control nodes remain mounted while callback/API leases rotate. Only bound definitive rejection allows rollback. Fresh genuine mode/base/day-night changes remain truthful; no cached target grants permission.

During a source/readback gap, the last confirmed number remains a labelled inert private projection. Actual Off stays Off; offline/unqualified targets cannot send. The independently available six-mode path is not disabled by uncertain temperature presentation. Missing initial confirmed bounds never invents a dial range.

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
release this lock. Further gestures are not queued or retried, and a submitted
request cannot be superseded on its still cached basis; unsubmitted gestures
still coalesce. The six-mode selector remains independently available.

A terminal uncertain request with a new coherent qualified basis remains
labelled with its uncertain requested number until another deliberate gesture
replaces it; this never claims that the old requested target was confirmed.
Local timeout/connection/service errors alone do not prove backend termination.

If none of these qualified settlement or recovery conditions arrives, an
uncertain request can keep temperature control inert indefinitely. Its requested number
and uncertainty note remain visible; independently qualified mode control stays
available.


## Independent correction fallback

When no active target basis is qualified, including Auto during summer shutdown,
the card and popup offer a separately labelled Raumkorrektur dial instead of
inventing a day/setback target. It writes only the commissioned number entity,
-3 to +3 °C in 0.5 °C steps; day/setback targets remain separately editable.

The adapter passes an opaque `hargassner_correction_id` on both climate and its
correction number. The number also advertises `hargassner_control_schema:
room_correction_v1` and exact bounds. The frontend requires exactly one matching
number, validates metadata and same-device registry information when present,
and follows its actual entity ID after renaming. Missing, duplicated, excluded,
foreign-device or unavailable entities cannot be substituted by name guessing.
This requires the matching backend marker update; the frontend alone cannot
create the control. Existing global offline/metadata/source command fences apply.

Requested correction is shared in memory across card/popup for the same
connection, authenticated user and entity. Rapid unsubmitted gestures coalesce;
submitted writes become inert until matching newer number readback, definitive
predispatch service rejection, or explicit recovery. Old echoes never roll the
number back. Timeout, disconnect or lost service response remain honestly
uncertain, retaining requested presentation without automatic resend. Client/
server clock skew can prevent receipt confirmation; it never grants success.
A dial drag that passes an end keeps that end while the pointer is in the open
bottom gap; only a new press there selects the nearer end. An unmeasurable dial
selects nothing. A release that cannot be sent, or another surface's pending
request, discards the local draft; plus and minus step from the confirmed or
requested value or from a step already queued for sending, never from a drag.
The dial does not wrap: a drag that returns to the ring beyond the gap keeps
its end until the pointer is nearer to it along the ring than across the gap.
A normal qualified target restores its existing target-dial behavior. Unknown
active target and missing actual room sensors remain labelled unknown.

## Energy totals

A backend with `hargassner_energy_schema: energy_totals_v1` also publishes five
`total_increasing` sensors on the same device, outside the selectable entity
list: Pelletverbrauch (Förderzähler, kg, not weighed), Pelletenergie
(Schätzung, kWh, pellet kg times a configurable calorific value), Brennerstunden,
Brennerstarts and Heizgradtage. Their values never decrease except after a
genuine reset of the backend's own totals. The device page groups them under
Pelletslager, Kessel and Steuerung. How to use them:
[Energy dashboard](ENERGY-DASHBOARD.md).

## One-time charge triggers

The one-time charge buttons (`button:PR040` buffer, `button:PR042` hot water in
the [naming reference](../examples/entity-name-mapping.json) and the
[UI groups](../examples/entity-ui-groups.json)) are momentary requests, and when
the parameter reports `1` and then `0` that `0` is the trigger resetting itself,
neither completion of the charge nor a rejection of the request. Completion is
only an independently qualified charging-to-noncharging transition of the
buffer or hot-water state; a trigger reset, request acknowledgement, pump output
or elapsed time never establishes it. This client adds no acknowledgement write
and no entity that claims a finished cycle.

## Telemetry cadence is a controller default, not a guarantee

The freshness behaviour of these entities rests on the controller's telemetry
cadence: V14.1HAR.q1 checks each channel every 60 s by default and resends an
unchanged channel once per `max_channel_age`, 3600 s by default. Offline
firmware analysis establishes that both are only defaults and that the cloud
commands `get_channel_timeout` and `get_max_channel_age` are **setters**, not
reads, so a cloud-sent value can change the cadence on a controller this client
never wrote to. A longer resend age pushes state past the backend's availability
timeout and makes entities flap or sit unavailable with nothing wrong locally;
no local code in this repository sends either command. The HA-host monitor's own
numbers are unaffected: its Telnet sample contract (a `fresh` frame at most
50 s old) and its 45 s, 120 s and 90000 s limits are VM-local and independent
of the MQTT cadence, so a changed cadence is not visible there either.
