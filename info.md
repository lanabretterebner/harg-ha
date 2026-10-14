# Hargassner Thermostat Popup

One visible Modus selector with Aus, Auto, Heizen, Absenken, 1x Heizen and
1x Absenken in matching native climate popups. Requires separately configured
MQTT climate entities with the configured_room_setpoint_v1 schema marker.

Install as a HACS Dashboard module. See README.md for resource registration,
optional native tiles, compatibility and removal. The module replaces the two
native mode menus; an explicit choice invokes Home Assistant's existing preset
action. It does not create entities or install the MQTT adapter.
