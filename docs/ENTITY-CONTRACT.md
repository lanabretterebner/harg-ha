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
