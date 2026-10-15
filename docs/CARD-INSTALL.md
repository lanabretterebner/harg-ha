# Add Hargassner thermostat cards without YAML

1. Download/update this repository to1.4.0 in HACS and verify its existing module
   resource. Reload the frontend and open a dashboard so the resource loads.
2. Choose **Edit dashboard → Add card**. Search for **Hargassner Thermostat**
   in the custom cards list and select it.
3. Use the visual entity selector to choose a climate exposing
   `hargassner_control_schema: configured_room_setpoint_v1`. Set an optional
   display name, preview and save. Repeat for the other heating circuit.
4. Confirm one Modus menu with six icons, the Heizkreisstatus line, native target
   dial and separate measured-room/correction fields. Opening/adding the card
   sends no heating command. Choose a control only when you intend its action.

The default preview selects a compatible marked climate; it does not select a
mode or temperature. The actual registry IDs are authoritative. A missing
measured room value displays Nicht verfügbar and is not replaced by a configured
target. Unsupported/missing entities remain explicit.

The optional [complete dashboard configuration](../examples/hargassner-dashboard.json)
provides two thermostat stacks and grouped readings using generic suggested IDs.
Adapt IDs to the actual registry before importing it. Adding a HACS repository
or downloading this module does not itself create a dashboard or cards.

The [native tile examples](../examples/thermostat-cards.yaml) remain an optional
compact alternative that opens the corrected popup. The separate Supervisor
Ingress app opens the existing web dashboard and does not create these cards.

If the card is absent from Add Card, confirm the exact module URL returns200,
the HACS installed version is1.4.0 and the dashboard has loaded the resource.
A full frontend reload is required when upgrading an already registered custom
card class; clearing HTTP cache alone cannot replace an existing class in memory.
For direct Settings/device-page loading use the documented global loader.

The module also groups rows inside the existing Hargassner device's native
Control/Sensor lists. It changes presentation only, preserving the device,
entity IDs and native actions; no registry/device migration or MQTT reload.

During a qualified local room-offset write, the card briefly keeps the last confirmed dial visible and inert with a readback note. It never invents a new target. A fresh target replaces it; offline, changed context or an eight-second deadline removes the hold. This requires the matching backend display marker. Native target authority and separate day/setback controls are unchanged.
