# Heating in the Home Assistant Energy dashboard

The local-control backend publishes five counters on the existing Hargassner
device. They keep long-term statistics, so HA can show them per hour, day,
month and year. They need the backend with energy totals
(`hargassner_energy_schema: energy_totals_v1` in their attributes).

| Entity | What it counts |
| --- | --- |
| `sensor.pelletenergie_schaetzung` | Fuel energy in kWh, **an estimate** |
| `sensor.pelletverbrauch_foerderzaehler` | Pellets in kg from the boiler's feed counter |
| `sensor.brennerstunden` | Burner hours (Kessel Start to Ausbrand) |
| `sensor.brennerstarts` | Burner starts |
| `sensor.heizgradtage` | Heating degree days on the base temperature set in the backend (attribute `Basis °C`) |

Your HA may show other entity ids if names were already taken: check
**Settings → Devices & services → MQTT → Hargassner device**.

## Add the heating to the Energy dashboard

1. Open **Settings → Dashboards → Energy** (or the Energy panel and its
   configuration button).
2. Under **Gas consumption**, choose **Add gas source** and select
   **Pelletenergie (Schätzung)**. HA accepts energy sensors in kWh as a gas
   source; the pellet boiler then appears like a gas heating.
3. Optional cost: choose a fixed price per kWh. From a pellet price per kg,
   divide by the calorific value (default 4.8): 0.40 €/kg is about
   0.083 €/kWh.
4. Save. The first bars appear after about an hour; HA keeps no data from
   before the sensor existed.

Pellets in kg, burner hours, starts and degree days are not Energy dashboard
sources. Show them with a **Statistics graph** card (statistic type
*Change*, period *Day*, *Week* or *Month*) or a **Statistic** card. Kilograms
per degree day over a month say how efficiently the house heated.

## What the estimate means

- **The pellets are not weighed.** The controller counts how much its feed
  screw delivered and converts that to kg with its own calibration. Compare it
  with your deliveries now and then; a difference of a few percent is normal.
- **The kWh are pellet kg × 4.8 kWh/kg.** 4.8 is a typical net calorific value
  of A1 pellets (the standard requires at least 4.6). The value your supplier
  states can differ; the backend operator can set it (`ha_pellet_kwh_per_kg`).
  A changed value applies from then on; earlier kWh stay as counted.
- **This is fuel put in, not heat delivered.** The boiler's efficiency, and
  losses in the tank and pipes, are not subtracted. It is not a heat meter
  reading.
- **Gaps lower the totals, never raise them.** While the controller or the
  backend is away, nothing is counted; the sensors are unavailable meanwhile.
  An interruption of up to two hours is bridged from the counter, and its
  pellets appear in the hour the counter is read again; after a longer one
  they are not counted, so HA never books many hours of consumption into one
  bar.
- **Totals never go backwards.** A controller counter that resets or jumps is
  handled in the backend. Only if the backend's totals file is lost do the
  counters start again at 0; HA treats that as a new meter and shows no
  negative values.
- **Degree days** are added once per day, shortly after midnight, for the day
  before, and only for days with at least 90 % outdoor readings. The base
  temperature is the one configured in the backend (see the attribute
  `Basis °C`); unless it is 20 °C with a 12 °C heating limit, the figures are
  not comparable with published HGT 20/12 values.

## If the entities disappear for a while

If the backend is rolled back to a release without these counters, HA removes
the five entities. Their statistics stay in HA's database. When the counters
return, they get their default entity ids and the statistics continue under
them. A name, area or entity id you changed in HA is lost: if you renamed an
entity, rename it back, and select it again as the Energy dashboard source.
If HA offers to delete statistics of "entities no longer provided" in the
meantime, do not accept.
