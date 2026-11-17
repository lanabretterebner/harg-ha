"""Synthetic first-order room model per circuit type, driven over weeks.

Normal operation (day/setback cycles, status ramps, sun bumps, airing,
on-change sensors, HA and app restarts, target-unknown gaps and context
outages) must give ZERO findings. Injected faults must be detected within
the documented times and every finding must end after the repair.
"""
import math
import random
import unittest
import unittest.mock

import house_rules as r
from house_test_support import Harness, circuit

DAY = 1440


class Room:
    def __init__(self, entity, *, tau_heat, tau_cool, offset=0.0, cap=None, start=20.0,
                 resolution=0.1, every='periodic', sun=0.0, airing=False):
        self.entity, self.tau_heat, self.tau_cool = entity, tau_heat, tau_cool
        self.offset, self.cap, self.temperature = offset, cap, start
        self.resolution, self.every, self.sun, self.airing = resolution, every, sun, airing
        self.fault = None          # None, 'closed', 'low', 'high'

    def equilibrium(self, target, heating, outdoor):
        if not heating or self.fault == 'closed':
            return 15.0 if heating or outdoor > 5 else outdoor
        value = target + self.offset
        if self.fault == 'low':
            value -= 2.5
        elif self.fault == 'high':
            value += 2.5
        if self.cap is not None and self.fault != 'high':
            value = min(value, self.cap)
        return value

    def advance(self, target, heating, outdoor, minute_of_day):
        goal = self.equilibrium(target, heating, outdoor)
        tau = self.tau_heat if self.temperature < goal else self.tau_cool
        self.temperature += (goal - self.temperature) / tau
        if self.airing and 8 * 60 <= minute_of_day < 8 * 60 + 10:
            self.temperature -= 0.2      # window open for 10 min
        shown = self.temperature
        if self.sun and 11 * 60 <= minute_of_day < 15 * 60:
            shown += self.sun * math.sin(math.pi * (minute_of_day - 11 * 60) / 240)
        return shown


class House:
    """One circuit under the controller's day/setback schedule."""

    def __init__(self, kind, rooms, *, options_extra=None, seed=1):
        self.kind, self.rooms = kind, rooms
        self.name = 'hc1' if kind == 'radiator' else 'hc2'
        key = 'heizkreis_1_sensoren' if kind == 'radiator' else 'heizkreis_2_sensoren'
        options = {key: [room.entity for room in rooms], **(options_extra or {})}
        self.h = Harness(options, local_minute=0, values={room.entity: room.temperature for room in rooms})
        for room in rooms:
            self.h.reporting[room.entity] = room.every
        self.random = random.Random(seed)
        self.day_target, self.setback_target = (21.0, 18.0) if kind == 'radiator' else (21.0, 20.0)
        self.outdoor = 3.0
        self.heating_off = False
        self.pump_effect = True
        self.dead = set()
        self.jumping = set()
        self.findings_seen = []
        self.minute = 0
        self.constant_day = False      # mode Heizen: no daily phase change
        self.off_minutes = 0           # minutes the heating has been off
        self.on_minutes = None         # minutes since the heating came back

    def schedule(self, minute_of_day):
        if self.constant_day or 6 * 60 <= minute_of_day < 22 * 60:
            return 'day', self.day_target, 1
        status = 2 if 22 * 60 <= minute_of_day < 22 * 60 + 30 else 3   # Absenken Rampe, then Absenken
        return 'setback', self.setback_target, status

    def tick(self, *, gaps=True):
        h = self.h
        day_index, minute_of_day = divmod(self.minute, DAY)
        self.minute += 1
        phase, target, status = self.schedule(minute_of_day)
        flow = (50.0 if phase == 'day' else 38.0) if self.kind == 'radiator' else (32.0 if phase == 'day' else 29.0)
        values = {'phase': phase, 'target': target, 'day_target': self.day_target,
                  'setback_target': self.setback_target, 'status': status, 'pump': True, 'flow': flow,
                  'mode': 'day' if self.constant_day else 'auto', 'type': self.kind}
        if self.heating_off:
            values.update(status=0, pump=False, flow=12.0, mode='off', phase=None, target=None)
            self.off_minutes += 1
            self.on_minutes = None
        else:
            if self.off_minutes:
                self.on_minutes, self.off_minutes = 0, 0
            if self.on_minutes is not None:
                # Back on: the pump starts after 2 min, the flow warms up by 1 K a minute.
                values.update(pump=self.on_minutes >= 2, flow=min(flow, 15.0 + self.on_minutes))
                self.on_minutes = None if self.on_minutes > 60 else self.on_minutes + 1
        if gaps and day_index % 3 == 1 and 14 * 60 <= minute_of_day < 14 * 60 + 40:
            values.update(phase=None, target=None)            # target basis unknown
        h.set_circuit(self.name, **values)
        h.context['outdoor_avg'] = self.outdoor
        h.context_state = 'ok'
        if gaps and day_index % 5 == 2 and 3 * 60 <= minute_of_day < 3 * 60 + 30:
            h.context_state = 'none'                          # VM or broker away
        h.ha = 'running'
        restart = gaps and day_index % 7 == 4 and 2 * 60 <= minute_of_day < 2 * 60 + 8
        if restart and minute_of_day < 2 * 60 + 3:
            h.ha = 'starting'
        heating = not self.heating_off and self.pump_effect
        for room in self.rooms:
            shown = room.advance(target if target else self.setback_target, heating, self.outdoor, minute_of_day)
            noisy = shown + self.random.uniform(-0.04, 0.04)
            value = round(round(noisy / room.resolution) * room.resolution, 2)
            if room.entity in self.dead:
                value = 'unavailable'
            elif restart and minute_of_day >= 2 * 60 + 3:
                value = 'unavailable'                         # HA restores states
            elif room.entity in self.jumping and self.minute % 97 == 0:
                value = value + 10.0
            h.values[room.entity] = value
        if gaps and day_index % 7 == 6 and minute_of_day == 12 * 60:
            h.engine = r.HouseEngine(h.options)               # app restart: nothing persisted
        findings = h.step()
        if findings:
            self.findings_seen.append((self.minute, sorted({row['code'] for row in findings})))
        return findings

    def run(self, minutes, **kwargs):
        for _ in range(minutes):
            self.tick(**kwargs)

    def until(self, code, limit, **kwargs):
        for minute in range(1, limit + 1):
            self.tick(**kwargs)
            if self.h.has(code):
                return minute
        return None


def radiator_house(seed=1):
    rooms = [Room('sensor.wohnzimmer', tau_heat=40, tau_cool=900, offset=0.1, start=19.5, sun=1.5),
             Room('sensor.kueche', tau_heat=35, tau_cool=800, offset=-0.3, start=19.5, every=60),
             Room('sensor.schlafzimmer', tau_heat=45, tau_cool=900, cap=18.5, start=17.5, resolution=0.5,
                  every=12 * 60, airing=True),
             Room('sensor.bad', tau_heat=30, tau_cool=700, offset=0.4, start=19.5)]
    return House('radiator', rooms, options_extra={'kuehlere_raeume': ['sensor.schlafzimmer'],
                                                   'ohne_ueberwaermung': ['sensor.wohnzimmer']}, seed=seed)


def floor_house(seed=2):
    rooms = [Room('sensor.flur', tau_heat=180, tau_cool=1500, offset=-0.2, start=20.5),
             Room('sensor.buero', tau_heat=200, tau_cool=1400, offset=0.2, start=20.5, sun=1.2, every=60),
             Room('sensor.kinderzimmer', tau_heat=220, tau_cool=1600, start=20.5, resolution=0.5,
                  every=12 * 60, airing=True)]
    return House('floor', rooms, seed=seed)


HEATING_CODES = frozenset(code for code, (check, _level) in r.CODES.items() if check == 'Räume')


class NormalOperationTests(unittest.TestCase):
    def test_three_weeks_of_radiator_operation_without_findings(self):
        house = radiator_house()
        house.run(21 * DAY)
        self.assertEqual(house.findings_seen, [])
        self.assertIn(house.h.engine.circuits_report()['hc1']['judged'], ('yes', 'unknown'))

    def test_three_weeks_of_floor_operation_without_findings(self):
        house = floor_house()
        house.run(21 * DAY)
        self.assertEqual(house.findings_seen, [])

    def test_basis_unknown_counters_record_gaps(self):
        house = radiator_house()
        house.run(4 * DAY + 60)
        minutes = house.h.engine.basis_unknown_minutes(house.h.now)['hc1']
        self.assertGreater(minutes['7d'], 0)       # the 40-minute target gap counts after its grace


class FaultTests(unittest.TestCase):
    def settled(self, factory, days=2):
        house = factory()
        house.run(days * DAY)
        self.assertEqual(house.findings_seen, [])
        return house

    def test_radiator_valve_closed(self):
        house = self.settled(radiator_house)
        house.rooms[1].fault = 'closed'
        self.assertIsNotNone(house.until('room_cold', DAY, gaps=False))
        self.assertTrue(house.h.has('room_cold', circuit='hc1'))
        house.until('no_response', DAY, gaps=False)
        self.assertTrue(house.h.has('no_response'))
        house.rooms[1].fault = None
        house.run(2 * DAY, gaps=False)
        self.assertFalse(house.h.codes() & HEATING_CODES)

    def test_floor_actuator_closed(self):
        house = self.settled(floor_house)
        house.rooms[0].fault = 'closed'
        minute = house.until('room_cold', 2 * DAY, gaps=False)
        self.assertIsNotNone(minute)
        house.rooms[0].fault = None
        house.run(2 * DAY, gaps=False)
        self.assertFalse(house.h.codes() & HEATING_CODES)

    def test_pump_without_effect(self):
        house = self.settled(radiator_house)
        house.pump_effect = False
        self.assertIsNotNone(house.until('circuit_no_response', 2 * DAY, gaps=False))
        row = house.h.rows('circuit_no_response')[0]
        self.assertEqual((row['level'], row['circuit']), ('Störung', 'hc1'))
        self.assertGreaterEqual(row['count'], 2)
        house.pump_effect = True
        house.run(2 * DAY, gaps=False)
        self.assertFalse(house.h.codes() & HEATING_CODES)

    def test_curve_too_low(self):
        house = self.settled(radiator_house)
        for room in house.rooms:
            room.fault = 'low'
        self.assertIsNotNone(house.until('room_cold', 2 * DAY, gaps=False))
        for room in house.rooms:
            room.fault = None
        house.run(2 * DAY, gaps=False)
        self.assertFalse(house.h.codes() & HEATING_CODES)

    def test_curve_too_high_per_type(self):
        for factory, constant, limit in ((radiator_house, False, 2 * DAY), (floor_house, True, 3 * DAY)):
            with self.subTest(factory=factory.__name__):
                house = self.settled(factory)
                house.constant_day = constant
                for room in house.rooms:
                    room.fault = 'high'
                self.assertIsNotNone(house.until('circuit_warm', limit, gaps=False))
                for room in house.rooms:
                    room.fault = None
                house.run(3 * DAY, gaps=False)
                self.assertFalse(house.h.codes() & HEATING_CODES)

    def test_floor_warm_with_daily_phase_change(self):
        # The warm rules settle on a change of U = max(T, day), not on every
        # phase change: a floor circuit on a day/setback schedule completes the
        # 12 h circuit dwell.
        house = self.settled(floor_house)
        for room in house.rooms:
            room.fault = 'high'
        self.assertIsNotNone(house.until('circuit_warm', 3 * DAY, gaps=False))
        for room in house.rooms:
            room.fault = None
        house.run(3 * DAY, gaps=False)
        self.assertFalse(house.h.codes() & HEATING_CODES)

    def heat_up_after_off(self, fault=None):
        # Off from 08:00 to 11:00 (rooms lose about 2 K), then back on: the
        # pump starts 2 min late and the flow is cold for the first 20 min.
        house = self.settled(floor_house)
        house.run(8 * 60 - house.minute % DAY, gaps=False)
        house.heating_off = True
        house.run(180, gaps=False)
        if fault:
            house.rooms[0].fault = fault
        house.heating_off = False
        house.tick(gaps=False)
        armed = set(house.h.engine.windows)
        self.assertTrue({'sensor.flur', 'sensor.buero'} <= armed, armed)
        house.run(30, gaps=False)
        # The start-up allowance kept every window armed through the cold start.
        self.assertTrue(armed <= set(house.h.engine.windows))
        return house, armed

    def test_without_the_start_up_allowance_the_cold_start_disarms(self):
        floor = dict(r.TYPE_CONSTANTS['floor'], startup=0)
        with unittest.mock.patch.dict(r.TYPE_CONSTANTS, floor=floor):
            house = self.settled(floor_house)
            house.run(8 * 60 - house.minute % DAY, gaps=False)
            house.heating_off = True
            house.run(180, gaps=False)
            house.heating_off = False
            house.tick(gaps=False)
            # Pump still off at the trigger: disarmed at once (the aired room is paused).
            self.assertFalse({'sensor.flur', 'sensor.buero'} & set(house.h.engine.windows))

    def test_floor_heat_up_arms_response_windows_and_stays_quiet(self):
        house, armed = self.heat_up_after_off()
        house.run(8 * 60, gaps=False)
        self.assertFalse(armed & set(house.h.engine.windows))   # every window completed
        self.assertEqual(house.findings_seen, [])

    def test_floor_actuator_closed_during_heat_up_gives_no_response(self):
        house, armed = self.heat_up_after_off('closed')
        self.assertIsNotNone(house.until('no_response', 8 * 60, gaps=False))
        self.assertEqual([row['label'] for row in house.h.rows('no_response')], [house.h.engine.labels['sensor.flur']])
        house.rooms[0].fault = None
        house.run(2 * DAY, gaps=False)
        self.assertFalse(house.h.codes() & HEATING_CODES)

    def test_frost_with_heating_off(self):
        house = self.settled(radiator_house)
        house.heating_off, house.outdoor = True, -10.0
        self.assertIsNotNone(house.until('frost', 8 * DAY, gaps=False))
        self.assertEqual(house.h.rows('frost')[0]['level'], 'Störung')
        self.assertTrue(house.h.has('room_frost'))
        house.heating_off, house.outdoor = False, 3.0
        house.run(2 * DAY, gaps=False)
        self.assertFalse(house.h.codes() & {'frost', 'room_frost'})

    def test_dead_sensor_flags_only_the_sensor(self):
        house = self.settled(radiator_house)
        house.dead.add('sensor.kueche')
        self.assertEqual(house.until('sensor_unavailable', 60), 31)
        house.run(DAY)
        self.assertFalse(house.h.codes() & HEATING_CODES)
        self.assertEqual(house.h.engine.sensors_report(house.h.now)['unavailable'], 1)
        house.dead.clear()
        house.tick()
        self.assertNotIn('sensor_unavailable', house.h.codes())

    def test_jumping_sensor_flags_only_the_sensor_and_expires(self):
        house = self.settled(floor_house)
        house.jumping.add('sensor.flur')
        self.assertIsNotNone(house.until('sensor_jumping', DAY, gaps=False))
        house.run(DAY, gaps=False)
        self.assertFalse(house.h.codes() & HEATING_CODES)
        house.jumping.clear()
        house.run(DAY + 60, gaps=False)
        self.assertEqual(house.h.codes(), set())

    def test_all_sensors_unavailable(self):
        house = self.settled(radiator_house)
        house.dead.update(room.entity for room in house.rooms)
        self.assertEqual(house.until('sensors_all_unavailable', 60), 31)
        self.assertEqual(house.h.codes(), {'sensors_all_unavailable'})
        self.assertEqual(house.h.engine.circuits_report()['hc1'], {
            'type': 'radiator', 'type_source': 'controller', 'sensors': 4, 'judged': 'unknown', 'why': 'sensors'})
        house.dead.clear()
        house.run(2)
        self.assertEqual(house.h.codes(), set())


if __name__ == '__main__':
    unittest.main()
