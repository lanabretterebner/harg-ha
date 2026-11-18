"""Normal household behaviour against the room rules, with synthetic physics.

The owner (2026-11-18): the room rules must fire on a real, persistent heating
problem only and stay quiet through airing, an open door, sun, a valve turned
down for an afternoon, slow floor heating and the first cold morning after the
setback. Each scenario drives one circuit minute by minute through the
controller's day/setback schedule (day 06:00-22:00) with a first-order room
model; the rooms start in their equilibrium after two quiet days.

Physics (per minute, synthetic and deliberately pessimistic for the quiet
cases): the room (walls, furniture, air) moves towards its goal with a heat-up
or cool-down time constant (radiator 60/600 min, floor 300/1500 min). The air
at the sensor is the room plus a fast part: an open window pulls the air
half way to outdoor with an 8 min time constant and cools the room by a 120th
of the difference a minute; once closed, the air rejoins the room with
a 12 min time constant. An open door pulls the room towards 14 °C with 90 min;
sun adds up to `sun` kelvin to the goal around noon. A closed valve or a dead
radiator makes the goal the 15 °C the neighbours hold; a failed pump does that
for every room of the circuit. Readings are rounded to 0.1 K with +-0.04 K
noise and reported every minute (`every`: a sensor that measures every n
minutes and reports a change at once, else hourly).
"""
import math
import random
import unittest

import house_rules as r
from house_test_support import Harness

DAY = 1440
HEATING = frozenset(code for code, (check, _level) in r.CODES.items() if check == 'Räume')
SENSOR = frozenset(code for code, (check, _level) in r.CODES.items() if check == 'Raumsensoren')


class Room:
    def __init__(self, entity, *, tau_heat, tau_cool, offset=0.0, sun=0.0, every=1):
        self.entity, self.tau_heat, self.tau_cool, self.offset, self.sun = entity, tau_heat, tau_cool, offset, sun
        self.every = every           # minutes between the sensor's reports
        self.temperature = None
        self.air = 0.0               # the air at the sensor minus the room
        self.window = self.door = False
        self.valve = True            # False: closed by hand or stuck
        self.dead = False            # radiator gives nothing (air lock, stuck valve)

    def advance(self, target, supplying, effect, outdoor, minute_of_day):
        heating = supplying and effect and self.valve and not self.dead
        goal = target + self.offset if heating else 15.0
        if self.sun and 10 * 60 <= minute_of_day < 16 * 60:
            goal += self.sun * math.sin(math.pi * (minute_of_day - 10 * 60) / 360)
        tau = self.tau_heat if self.temperature < goal else self.tau_cool
        self.temperature += (goal - self.temperature) / tau
        if self.window:
            self.air += ((outdoor - self.temperature) / 2 - self.air) / 8
            self.temperature += self.air / 120
        else:
            self.air -= self.air / 12
        if self.door:
            self.temperature += (14.0 - self.temperature) / 90

    @property
    def reading(self):
        return self.temperature + self.air


# Day phases (minutes of the day). 'split' is a split daily programme
# (06:00-09:00 and 15:00-23:00, as in the floor circuit_cold decision).
PROGRAMMES = {'plant': ((6 * 60, 22 * 60),), 'split': ((6 * 60, 9 * 60), (15 * 60, 23 * 60))}


class Plant:
    """One circuit (hc1 radiator or hc2 floor) under a day/setback schedule."""

    programme = 'plant'

    def __init__(self, kind, rooms, *, setback=None, outdoor=0.0, seed=7):
        self.kind, self.rooms = kind, rooms
        self.name = 'hc1' if kind == 'radiator' else 'hc2'
        key = 'heizkreis_1_sensoren' if kind == 'radiator' else 'heizkreis_2_sensoren'
        self.day_target = 21.0
        self.setback_target = setback if setback is not None else (17.0 if kind == 'radiator' else 19.0)
        self.outdoor = outdoor
        self.effect = True            # False: pump or mixer failed (flow reads warm, rooms get nothing)
        self.random = random.Random(seed)
        self.minute = 0
        for room in rooms:
            room.temperature = self.day_target + room.offset
        self.h = Harness({key: [room.entity for room in rooms]}, local_minute=0,
                         values={room.entity: room.temperature for room in rooms})
        self.seen = []                # (minute, code, label)

    def schedule(self, minute_of_day):
        phases = PROGRAMMES[self.programme]
        if any(start <= minute_of_day < end for start, end in phases):
            return 'day', self.day_target, 1
        ramp = any(end <= minute_of_day < end + 30 for _start, end in phases)   # Absenken Rampe
        return 'setback', self.setback_target, 2 if ramp else 3

    def tick(self):
        minute_of_day = self.minute % DAY
        phase, target, status = self.schedule(minute_of_day)
        if self.kind == 'radiator':
            flow = 55.0 if phase == 'day' else 40.0
        else:
            flow = 34.0 if phase == 'day' else 30.0
        self.h.set_circuit(self.name, phase=phase, target=target, day_target=self.day_target,
                           setback_target=self.setback_target, status=status, pump=True, flow=flow,
                           mode='auto', type=self.kind)
        self.h.context['outdoor_avg'] = self.outdoor
        for room in self.rooms:
            room.advance(target, True, self.effect, self.outdoor, minute_of_day)
            noisy = room.reading + self.random.uniform(-0.04, 0.04)
            if room.every > 1:
                self.h.reporting[room.entity] = 60          # on change, at least hourly
            if self.minute % room.every == 0:
                self.h.values[room.entity] = round(round(noisy * 10) / 10, 1)
        self.minute += 1
        findings = self.h.step()
        for row in findings:
            self.seen.append((self.minute, row['code'], row['label']))
        return findings

    def run(self, minutes):
        for _ in range(minutes):
            self.tick()

    def to(self, day, hour, minute=0):
        """Run until the given day and local time (day 0 is the first day)."""
        self.run(day * DAY + hour * 60 + minute - self.minute)

    def codes(self, since=0):
        return {code for minute, code, _label in self.seen if minute > since}

    def first(self, code, since=0):
        return next((minute for minute, seen, _label in self.seen if seen == code and minute > since), None)


def radiator_rooms(**overrides):
    rooms = [Room('sensor.wohnzimmer', tau_heat=60, tau_cool=600, offset=0.2),
             Room('sensor.kueche', tau_heat=60, tau_cool=600, offset=-0.2),
             Room('sensor.schlafzimmer', tau_heat=60, tau_cool=600),
             Room('sensor.bad', tau_heat=60, tau_cool=600, offset=0.3)]
    for room in rooms:
        for key, value in overrides.items():
            setattr(room, key, value)
    return rooms


def floor_rooms():
    return [Room('sensor.flur', tau_heat=300, tau_cool=1500, offset=-0.2),
            Room('sensor.buero', tau_heat=300, tau_cool=1500, offset=0.2),
            Room('sensor.kinderzimmer', tau_heat=360, tau_cool=1500)]


def settled(kind='radiator', **kwargs):
    plant = Plant(kind, radiator_rooms() if kind == 'radiator' else floor_rooms(), **kwargs)
    plant.to(2, 0)
    assert plant.seen == [], plant.seen
    return plant


class QuietTests(unittest.TestCase):
    """Normal behaviour: no room or heating finding at all."""

    def assertQuiet(self, plant, since=0):
        self.assertEqual(plant.codes(since) & (HEATING | SENSOR), set(), plant.seen[:5])

    def test_airing_ten_minutes_during_the_heating_start(self):
        # Day 2 06:00 the day phase starts (bedroom about 18.5 °C, 2.5 K below);
        # 06:20-06:30 the bedroom window is wide open (air drops about 5 K).
        plant = settled()
        start = plant.minute
        plant.to(2, 6, 20)
        plant.rooms[2].window = True
        plant.run(10)
        plant.rooms[2].window = False
        plant.to(4, 0)
        self.assertQuiet(plant, start)

    def test_airing_thirty_minutes_during_the_heating_start(self):
        # As above, window open 06:20-06:50: the air drops by about 9 K and the
        # room itself by about 2 K, which takes it hours to get back.
        plant = settled()
        start = plant.minute
        plant.to(2, 6, 20)
        plant.rooms[2].window = True
        plant.run(30)
        plant.rooms[2].window = False
        plant.to(4, 0)
        self.assertQuiet(plant, start)

    def test_airing_thirty_minutes_with_a_slow_radiator(self):
        # A low-temperature radiator (2 h heat-up constant) and a battery sensor
        # reporting every 15 min: the 60 min pause after the last drop ended
        # while the room was still below where it started, and before 1.6.5
        # the one-hour judgement gave a no_response at 08:00.
        plant = settled()
        plant.rooms[2].tau_heat, plant.rooms[2].every = 120, 15
        start = plant.minute
        plant.to(2, 6, 20)
        plant.rooms[2].window = True
        plant.run(30)
        plant.rooms[2].window = False
        plant.to(4, 0)
        self.assertQuiet(plant, start)

    def test_airing_every_morning_for_a_week(self):
        # The same slow bedroom aired 06:20-06:50 every day: still no notice.
        plant = settled()
        plant.rooms[2].tau_heat, plant.rooms[2].every = 120, 15
        start = plant.minute
        for day in range(2, 9):
            plant.to(day, 6, 20)
            plant.rooms[2].window = True
            plant.run(30)
            plant.rooms[2].window = False
        plant.to(9, 12)
        self.assertQuiet(plant, start)

    def test_several_rooms_aired_at_once(self):
        # The whole house aired 06:10-06:40 on two mornings in a row (cross
        # ventilation), slow radiators, sensors reporting every 15 min.
        plant = settled()
        for room in plant.rooms:
            room.tau_heat, room.every = 120, 15
        start = plant.minute
        for day in (2, 3):
            plant.to(day, 6, 10)
            for room in plant.rooms:
                room.window = True
            plant.run(30)
            for room in plant.rooms:
                room.window = False
        plant.to(5, 0)
        self.assertQuiet(plant, start)

    def test_door_open_for_an_hour_during_the_heating_start(self):
        # The kitchen door to the cold stairwell stays open 06:00-07:00: a slow
        # loss (no airing signature), the room does not warm in that hour.
        plant = settled()
        start = plant.minute
        plant.to(2, 6)
        plant.rooms[1].door = True
        plant.run(60)
        plant.rooms[1].door = False
        plant.to(4, 0)
        self.assertQuiet(plant, start)

    def test_door_open_for_an_hour_in_every_room(self):
        # Moving furniture: every door open 06:00-07:00, the circuit cannot win.
        plant = settled()
        start = plant.minute
        plant.to(2, 6)
        for room in plant.rooms:
            room.door = True
        plant.run(60)
        for room in plant.rooms:
            room.door = False
        plant.to(4, 0)
        self.assertQuiet(plant, start)

    def test_door_open_every_morning(self):
        # A habit: the kitchen door to the stairwell is open 06:00-08:00 every
        # day for a week. The kitchen is warm again by midday each day.
        plant = settled()
        start = plant.minute
        for day in range(2, 9):
            plant.to(day, 6)
            plant.rooms[1].door = True
            plant.to(day, 8)
            plant.rooms[1].door = False
        plant.to(9, 12)
        self.assertQuiet(plant, start)

    def test_sun_gain(self):
        # A south room gains up to 4 K around noon every day for a week.
        plant = settled()
        plant.rooms[0].sun = 4.0
        start = plant.minute
        plant.to(9, 0)
        self.assertQuiet(plant, start)

    def test_valve_turned_down_for_an_afternoon(self):
        # Someone closes the bedroom valve 12:00-18:00 (room falls towards 15 °C).
        plant = settled()
        start = plant.minute
        plant.to(2, 12)
        plant.rooms[2].valve = False
        plant.to(2, 18)
        plant.rooms[2].valve = True
        plant.to(4, 0)
        self.assertQuiet(plant, start)

    def test_floor_morning_warm_up_after_setback_on_a_cold_day(self):
        # First cold morning: -10 °C, floor setback 19 °C; the rooms reach about
        # 19.5 °C at 06:00 and need most of the day to get back.
        plant = settled('floor')
        plant.outdoor = -10.0
        for room in plant.rooms:
            room.tau_cool = 700
        start = plant.minute
        plant.to(5, 0)
        self.assertQuiet(plant, start)

    def test_floor_big_setback_slow_warm_up(self):
        # A floor circuit with a 3 K setback warms back slowly every day.
        plant = settled('floor', setback=18.0)
        for room in plant.rooms:
            room.tau_heat = 480
        start = plant.minute
        plant.to(6, 0)
        self.assertQuiet(plant, start)


class FaultTests(unittest.TestCase):
    """Real, persistent heating problems still fire, at their documented latency."""

    def test_radiator_cold_for_three_days(self):
        # The bedroom radiator stops at day 2 00:00 (air lock) and stays cold.
        plant = settled()
        fault = plant.minute
        plant.rooms[2].dead = True
        plant.to(5, 12)
        # room_cold: the room stays 1.5 K below for 90 min after the 2 h settle
        # of the 06:00 start, and keeps a deficit of 1 K or more for 12 h.
        cold = plant.first('room_cold', fault)
        self.assertIsNotNone(cold)
        self.assertLessEqual(cold - fault, 36 * 60)
        # no_response: the third failed heating start on a third day (day 4 07:00).
        response = plant.first('no_response', fault)
        self.assertIsNotNone(response)
        self.assertEqual(response, 4 * DAY + 7 * 60)
        self.assertNotIn('circuit_no_response', plant.codes(fault))
        self.assertNotIn('circuit_cold', plant.codes(fault))
        labels = {label for _minute, code, label in plant.seen if code in ('room_cold', 'no_response')}
        self.assertEqual(labels, {plant.h.engine.labels['sensor.schlafzimmer']})
        # Repaired: the next heating start clears both.
        plant.rooms[2].dead = False
        plant.to(6, 12)
        self.assertFalse(plant.h.codes() & HEATING)

    def test_pump_failure(self):
        # Day 2 00:00 the circuit pump fails: the controller still reports the
        # pump on and a warm flow, no room gets any heat.
        plant = settled()
        fault = plant.minute
        plant.effect = False
        plant.to(4, 12)
        cold = plant.first('circuit_cold', fault)
        response = plant.first('circuit_no_response', fault)
        self.assertIsNotNone(cold)
        self.assertIsNotNone(response)
        # circuit_cold (Störung): settle 2 h after 06:00, then 3 h with every room 3 K below.
        self.assertLessEqual(cold - fault, 12 * 60)
        # circuit_no_response (Störung): the second heating start without effect (day 3 07:00).
        self.assertEqual(response, 3 * DAY + 7 * 60)
        row = [row for row in plant.h.findings if row['code'] == 'circuit_no_response'][0]
        self.assertEqual((row['level'], row['circuit'], row['count']), ('Störung', 'hc1', 4))
        plant.effect = True
        plant.to(6, 12)
        self.assertFalse(plant.h.codes() & HEATING)

    def latencies(self, cases):
        """Whole hours from a fault at day 2 00:00 to each code's first row."""
        for kind, setback, fault, expected in cases:
            with self.subTest(kind=kind, setback=setback, fault=fault):
                plant = settled(kind, setback=setback)
                start = plant.minute
                if fault == 'dead':
                    plant.rooms[0].dead = True
                else:
                    plant.effect = False
                plant.to(5, 12)
                hours = {code: (plant.first(code, start) - start) // 60 for code in expected}
                self.assertEqual(hours, expected)
                self.assertEqual(plant.codes(start) & HEATING, set(expected))

    def test_floor_latencies(self):
        # Floor rooms, setback 18 or 19 °C, day 06:00-22:00.
        self.latencies((
            ('floor', 19.0, 'dead', {'room_cold': 26, 'no_response': 60}),
            ('floor', 18.0, 'pump', {'circuit_cold': 19, 'room_cold': 26, 'no_response': 60,
                                     'circuit_no_response': 36}),
            ('floor', 19.0, 'pump', {'circuit_cold': 20, 'room_cold': 26, 'no_response': 60,
                                     'circuit_no_response': 60})))


class SplitProgrammeFaultTests(FaultTests):
    """A split daily programme: day 06:00-09:00 and 15:00-23:00."""

    def setUp(self):
        Plant.programme = 'split'
        self.addCleanup(setattr, Plant, 'programme', 'plant')

    def test_radiator_cold_for_three_days(self):
        self.latencies((('radiator', 17.0, 'dead', {'room_cold': 36, 'no_response': 55}),))

    def test_pump_failure(self):
        self.latencies((('radiator', 17.0, 'pump', {'circuit_cold': 20, 'circuit_no_response': 31,
                                                    'room_cold': 36, 'no_response': 55}),))

    def test_floor_latencies(self):
        # Before 1.6.5 a floor room never reached room_cold on this programme
        # (an 8 h settle filled the whole 8 h evening phase).
        self.latencies((
            ('floor', 19.0, 'dead', {'room_cold': 55, 'no_response': 69}),
            ('floor', 18.0, 'pump', {'circuit_cold': 22, 'circuit_no_response': 45,
                                     'room_cold': 55, 'no_response': 69})))


class SplitProgrammeQuietTests(QuietTests):
    """Every quiet scenario again on the split daily programme."""

    def setUp(self):
        Plant.programme = 'split'
        self.addCleanup(setattr, Plant, 'programme', 'plant')


if __name__ == '__main__':
    unittest.main()
