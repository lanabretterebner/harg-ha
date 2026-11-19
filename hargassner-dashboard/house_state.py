"""The room rules' state across app restarts (`/data/house_state.json`).

The room rules report patterns over hours and days: failed heating starts on
three different days, 12 h away from the day target, airing episodes, the
sensor histories. Kept only in memory, every app restart re-learned them for
days, so a lasting problem was reported as over ("wieder in Ordnung") and days
later as a new problem. The worker now saves the engine at most every
SAVE_PERIOD_S (5 minutes, owner decision 2026-11-18) and when it stops (temporary file, fsync, rename) and continues from it at
start.

What a restore means:
- A restart counts as Home Assistant being unavailable for the time the app was
  stopped. Every timer pauses for that time and ends after its pause limit,
  exactly as during an outage the app sees; windows and findings age by it. The
  stopped time is never credited: the first evaluation judges every condition
  from current readings.
- The stopped time comes from the monotonic clock when the host did not reboot
  (same boot id), so a wall-clock step changes nothing. After a reboot it is the
  wall-clock difference, at least the time since this boot, accepted only from 0
  (up to RESTORE_SKEW_S back counts as 0) to RESTORE_MAX_GAP_S; otherwise
  everything starts fresh.
- Each room and circuit carries a fingerprint of the options it was judged
  with. A room whose circuit, circuit type, allowance ("darf 2/4/6 °C kühler
  sein", "nur Frost und Sensor prüfen"), "ohne Überwärmung" or frost flag
  changed starts fresh. A circuit whose type changed
  starts fresh; one whose rooms changed keeps its phase, target and settle
  timing but starts its checks over its rooms (heating starts, circuit_cold,
  circuit_warm, circuit_no_response) fresh. Every other room continues.
- The schema, the rule constants and a checksum must match. Anything unexpected
  gives a fresh engine (the behaviour before 1.6.6): never half a state, never
  a crash. A restored value never raises a finding by itself.
- STATE_SCHEMA must be raised by hand when the meaning of saved state changes
  (a rule logic change that keeps every constant is not seen by rules_digest).

Sizes are bounded: per sensor only the last HISTORY_KEEP_S of readings (what
the rules look back at), the step-size statistics in compact form, at most
STATE_MAX_BYTES in all. Pure functions plus one small file class; the clocks
are passed in.
"""
from collections import deque
import glob
import hashlib
import math
import os
import re
import stat
import tempfile

import house_rules as rules

STATE_SCHEMA = 'harg-house-state/1'
STATE_PATH = '/data/house_state.json'
STATE_MAX_BYTES = 4 * 1024 * 1024
# At most every 5 minutes (owner decision 2026-11-18: fewer writes on SD-card
# hosts). A stop without the final save (a crash, a power cut) loses at most
# this much, and the restore counts it as part of the stopped time.
SAVE_PERIOD_S = 300
RESTORE_MAX_GAP_S = 86400
RESTORE_SKEW_S = 300
# The longest look-back of the rules on raw readings: the floor trend (3 h)
# plus two 15-min medians, with margin; one older reading is kept as well.
HISTORY_KEEP_S = 14400
HISTORY_KEEP_MAX = 1000
WINDOW_SAMPLES_MAX = 2000
SPIKES_MAX = 64
DELTA_KEYS_MAX = 200
TIME_LIMIT_S = 1e8
BOOT_ID_PATH = '/proc/sys/kernel/random/boot_id'
BOOT_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
DOCUMENT_KEYS = frozenset(('schema', 'rules', 'saved_wall', 'saved_mono', 'boot', 'engine', 'sha256'))
TEMP_PREFIX = '.house-state-'
ONSET_KEYS = frozenset(('room', 'target', 'count'))


class Invalid(Exception):
    """The saved state is not usable (a fixed reason only)."""


def boot_id(path=BOOT_ID_PATH):
    """The host's boot id, or None. Same id: the monotonic clock continued."""
    try:
        with open(path, 'r', encoding='ascii') as stream:
            value = stream.read(64).strip()
        return value if BOOT_ID.fullmatch(value) else None
    except (OSError, ValueError):
        return None


def rules_digest():
    """The rule constants the state was built with; another set starts fresh."""
    numbers = {name: getattr(rules, name) for name in dir(rules)
               if name.isupper() and type(getattr(rules, name)) in (int, float)}
    body = {'numbers': numbers, 'types': rules.TYPE_CONSTANTS, 'codes': list(rules.CODES)}
    return hashlib.sha256(rules.canonical(body).encode('utf-8')).hexdigest()


def checksum(body):
    return hashlib.sha256(rules.canonical(body).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# Fingerprints of the options a room and a circuit were judged with

# The room's allowance in a fingerprint. Normal and 2 K keep the boolean of
# 1.6.6/1.6.7 ("darf kühler sein"), so a state saved by those versions
# continues; the choices of 1.6.8 are 4, 6 or "frost". 1.6.7 refuses those
# values and starts fresh (never half a state), which matters only after a
# rollback with one of the new choices in use.
# App 1.6.9: a room set exactly as a 1.6.8 choice (allowance 0/2/4/6 K, 100 %
# on its own circuit, 0 % on the other) keeps that mark, so a migrated room
# continues; every other free setting is "a<allowance>/<hc1 %>/<hc2 %>", and a
# room without influence is "frost". 1.6.8 refuses the new marks and starts
# that state fresh once after a downgrade. A room of the other circuit that a
# circuit heats too is listed in that circuit's fingerprint as "x<percent>".
MARK = re.compile(r'a(?:10|[0-9])(?:\.[05])?/[0-9]{1,3}/[0-9]{1,3}|x[0-9]{1,3}')


def cooler_mark(options, entity):
    if options.total(entity) == 0:
        return rules.FROST_ONLY
    if options.default_setting(entity):
        level = options.allowance(entity)
        return {0.0: False, 2.0: True}.get(level, int(level))
    allowance = options.allowance(entity)
    text = str(int(allowance)) if allowance == int(allowance) else str(allowance)
    return f'a{text}/{options.influence(entity, "hc1")}/{options.influence(entity, "hc2")}'


def _cooler(value):
    # bool first: True == 1 must not pass as a number, nor 4.0 as 4.
    if type(value) is bool or (type(value) is int and value in (4, 6)) or value == rules.FROST_ONLY:
        return value
    if isinstance(value, str) and MARK.fullmatch(value):
        return value
    raise Invalid('flag')


def room_fingerprint(options, entity):
    if entity in options.hc1:
        role = 'hc1'
    elif entity in options.hc2:
        role = 'hc2'
    else:
        role = 'haus'
    kind = options.option_types[rules.CIRCUITS.index(role)] if role in rules.CIRCUITS else None
    return [role, kind, cooler_mark(options, entity), entity in options.no_warm, entity in options.frost]


def circuit_fingerprint(options, name):
    kind = options.option_types[rules.CIRCUITS.index(name)]
    rows = [[entity, cooler_mark(options, entity), entity in options.no_warm]
            for entity in options.circuit_entities(name)]
    rows += [[entity, f'x{options.influence(entity, name)}', entity in options.no_warm]
             for entity in options.cross(name)]
    return [kind, rows]


# ---------------------------------------------------------------------------
# Export (monotonic times become ages before the save)

def _r(value):
    return round(float(value), 3)


def export(engine, *, mono, wall, boot):
    """The document for `restore`, JSON-ready (with its checksum)."""
    def age(when):
        return None if when is None else _r(mono - when)

    def latch(item):
        return [age(item.timer.since), _r(item.timer.total), _r(item.timer.paused), bool(item.active),
                dict(item.onset) if isinstance(item.onset, dict) else None]

    def trigger(key):
        return [key[0], int(key[1])]

    options = engine.options
    rooms = {}
    for entity, sensor in engine.sensors.items():
        history = [[age(when), value] for when, value in sensor.history]
        keep = [row for row in history if row[0] <= HISTORY_KEEP_S]
        older = [row for row in history if row[0] > HISTORY_KEEP_S]
        history = (older[-1:] + keep)[-HISTORY_KEEP_MAX:]
        # Step-size statistics: the youngest occurrence of each of the smallest
        # step sizes (only the smallest present one is used), every other
        # update counted per hour.
        youngest = {}
        for index, (when, delta) in enumerate(sensor.deltas):
            if delta is not None and (delta not in youngest or when >= sensor.deltas[youngest[delta]][0]):
                youngest[delta] = index
        kept = dict(sorted(youngest.items())[:DELTA_KEYS_MAX])
        representative = set(kept.values())
        hours = {}
        for index, (when, _delta) in enumerate(sensor.deltas):
            if index in representative:
                continue
            seconds = age(when)
            bucket = math.floor(seconds / 3600)
            first, count = hours.get(bucket, (math.inf, 0))
            hours[bucket] = (min(first, seconds), count + 1)
        window = engine.windows.get(entity)
        if window is not None and len(window.samples) > WINDOW_SAMPLES_MAX:
            window = None   # cannot happen at one sample a minute; such a start does not count
        finding = engine.no_response.get(entity)
        cold = engine.cold_dwell.get(entity)
        room = {
            'latches': {code: latch(item) for (code, owner), item in engine.latches.items() if owner == entity},
            'window': None if window is None else {
                'trigger': trigger(window.trigger), 'start': age(window.start), 'active': _r(window.active),
                'paused': _r(window.paused), 'samples': [[_r(t), value] for t, value in window.samples]},
            'no_response': None if finding is None else {
                'trigger': trigger(finding.trigger), 'onset': age(finding.onset), 'last': age(finding.last)},
            'response_fails': [age(when) for when in engine.response_fails.get(entity, [])],
            'cold_dwell': None if cold is None else latch(cold),
            'cold_since': age(engine.cold_since.get(entity)),
            'cold_seen': age(engine.cold_seen.get(entity)),
            'pump_off': age(engine.pump_off.get(entity)),
        }
        pending = sensor.pending
        rooms[entity] = {
            'fingerprint': room_fingerprint(options, entity),
            'sensor': {
                'history': history,
                'delta_keys': [[delta, age(sensor.deltas[index][0])] for delta, index in kept.items()],
                'delta_hours': [[first, count] for first, count in sorted(hours.values())],
                'last_key': sensor.last_key if isinstance(sensor.last_key, str) and len(sensor.last_key) <= 200 else None,
                'last_update': age(sensor.last_update), 'last_accepted': sensor.last_accepted,
                'last_accepted_mono': age(sensor.last_accepted_mono),
                'pending': None if pending is None else [age(pending[0]), pending[1], pending[2], age(pending[3])],
                'spikes': [age(when) for when in list(sensor.spikes)[-SPIKES_MAX:]],
                'step_until': age(sensor.step_until), 'jump_first': age(sensor.jump_first),
                'jump_last': age(sensor.jump_last), 'airing_drop': age(sensor.airing_drop),
                'airing_start': age(sensor.airing_start), 'airing_ref': sensor.airing_ref,
                'same_since': age(sensor.same_since), 'same_count': int(sensor.same_count),
                'presence': sensor.presence, 'numeric': bool(sensor.numeric),
                'implausible': bool(sensor.implausible), 'in_range_since': age(sensor.in_range_since),
                'battery': sensor.battery, 'name': sensor.name[:400] if isinstance(sensor.name, str) else None,
                'missing': latch(sensor.missing), 'unavailable': latch(sensor.unavailable),
                'implausible_latch': latch(sensor.implausible_latch), 'battery_latch': latch(sensor.battery_latch),
                'stale_active': bool(sensor.stale_active),
            },
            'room': room,
        }
    circuits = {}
    for name, circuit in engine.circuits.items():
        basis = {}
        for when, seconds in circuit.basis_unknown:
            moment = age(when)
            bucket = math.floor(moment / 3600)
            first, total = basis.get(bucket, (math.inf, 0.0))
            basis[bucket] = (min(first, moment), total + seconds)
        current = circuit.circuit_no_response
        circuits[name] = {
            'fingerprint': circuit_fingerprint(options, name),
            'last_phase': circuit.last_phase, 'last_target': circuit.last_target, 'last_mode': circuit.last_mode,
            'seen': bool(circuit.seen), 'settle_start': age(circuit.settle_start), 'last_upper': circuit.last_upper,
            'warm_settle_start': age(circuit.warm_settle_start), 'off_since': age(circuit.off_since),
            'triggers': int(circuit.triggers), 'judged': circuit.judged, 'why': circuit.why,
            'unknown_since': age(circuit.unknown_since),
            'basis_unknown': [[first, _r(total)] for first, total in sorted(basis.values(), key=lambda row: -row[0])],
            'circuit_cold': latch(circuit.circuit_cold), 'circuit_warm': latch(circuit.circuit_warm),
            'circuit_no_response': None if current is None else [
                trigger(current[0]), age(current[1]), int(current[2]), list(current[3])],
            'no_response_last': age(circuit.no_response_last),
            'starts': [[trigger(key), {'start': age(start['start']), 'armed': sorted(start['armed']),
                                       'fail': sorted(start['fail']), 'pass': sorted(start['pass']),
                                       'usable': int(start['usable'])}]
                       for key, start in circuit.starts.items()],
            'failed_starts': [age(when) for when in circuit.failed_starts],
            'failed_rooms': sorted(circuit.failed_rooms),
            'absent_since': age(circuit.absent_since), 'last_kind': circuit.last_kind,
        }
    state = {
        'rooms': rooms, 'circuits': circuits, 'entities': list(options.entities),
        'frost': None if engine.frost is None else [age(engine.frost[0]), dict(engine.frost[1])],
        'all_unavailable': latch(engine.all_unavailable),
    }
    body = {'schema': STATE_SCHEMA, 'rules': rules_digest(), 'saved_wall': _r(wall), 'saved_mono': _r(mono),
            'boot': boot if isinstance(boot, str) and BOOT_ID.fullmatch(boot) else None, 'engine': state}
    return {**body, 'sha256': checksum(body)}


# ---------------------------------------------------------------------------
# Restore

def _number(value, low=-math.inf, high=math.inf):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise Invalid('number')
    return float(value)


def _optional(value, low=-math.inf, high=math.inf):
    return None if value is None else _number(value, low, high)


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise Invalid('integer')
    return value


def _flag(value):
    if type(value) is not bool:
        raise Invalid('flag')
    return value


def _choice(value, choices):
    if value not in choices:
        raise Invalid('choice')
    return value


def _list(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise Invalid('list')
    return value


def _dict(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise Invalid('keys')
    return value


class _Decoder:
    """Ages back to monotonic times: `base` is the save moment in this process."""

    def __init__(self, options, base):
        self.options, self.base = options, base

    def time(self, value):
        return None if value is None else self.base - _number(value, -TIME_LIMIT_S, TIME_LIMIT_S)

    def times(self, value, limit):
        result = []
        for item in _list(value, limit):
            if item is None:
                raise Invalid('time')
            result.append(self.time(item))
        return result

    @staticmethod
    def duration(value):
        return _number(value, 0, TIME_LIMIT_S)

    @staticmethod
    def temperature(value):
        return _optional(value, -100.0, 200.0)

    def onset(self, value, needed=()):
        if value is None:
            if needed:
                raise Invalid('onset')
            return None
        if not isinstance(value, dict) or not set(value) <= ONSET_KEYS or not set(needed) <= set(value):
            raise Invalid('onset')
        result = {}
        for key, item in value.items():
            result[key] = _integer(item, 0, 1000) if key == 'count' else _number(item, -100.0, 200.0)
        return result

    def latch(self, value, target=None, needed=()):
        since, total, paused, active, onset = _list(value, 5)
        item = target if target is not None else rules.Latch()
        item.timer.since = self.time(since)
        item.timer.total, item.timer.paused = self.duration(total), self.duration(paused)
        item.active = _flag(active)
        if item.timer.since is None and (item.active or item.timer.total or item.timer.paused):
            raise Invalid('latch')
        item.onset = self.onset(onset, needed if item.active else ())
        return item

    def trigger(self, value, circuits=rules.CIRCUITS):
        name, count = _list(value, 2)
        return (_choice(name, circuits), _integer(count, 0, 2 ** 53))

    def members(self, value, allowed, limit=rules.MAX_SENSORS):
        items = _list(value, limit)
        if any(item not in allowed for item in items) or len(set(items)) != len(items):
            raise Invalid('members')
        return items


LATCH_NEEDS = {'room_frost': ('room',), 'limit_cold': ('room', 'target'), 'room_warm': ('room', 'target'),
               'room_hot': ('room',), 'room_cold': ('room', 'target')}


def _sensor(decode, sensor, saved):
    keys = ('history', 'delta_keys', 'delta_hours', 'last_key', 'last_update', 'last_accepted',
            'last_accepted_mono', 'pending', 'spikes', 'step_until', 'jump_first', 'jump_last', 'airing_drop',
            'airing_start', 'airing_ref', 'same_since', 'same_count', 'presence', 'numeric', 'implausible',
            'in_range_since', 'battery', 'name', 'missing', 'unavailable', 'implausible_latch', 'battery_latch',
            'stale_active')
    saved = _dict(saved, keys)
    sensor.history.clear()
    for row in _list(saved['history'], HISTORY_KEEP_MAX):
        when, value = _list(row, 2)
        sensor.history.append((decode.time(when), _number(value, -30.0, 60.0)))
    entries = []
    for row in _list(saved['delta_keys'], DELTA_KEYS_MAX):
        delta, when = _list(row, 2)
        entries.append((decode.time(when), _number(delta, 0.001, 100.0)))
    if len({delta for _when, delta in entries}) != len(entries):
        raise Invalid('deltas')
    for row in _list(saved['delta_hours'], 24 * 8 + 2):
        when, count = _list(row, 2)
        entries.extend([(decode.time(when), None)] * _integer(count, 1, rules.HISTORY_MAX))
    if len(entries) > rules.HISTORY_MAX + DELTA_KEYS_MAX:
        raise Invalid('deltas')
    entries.sort(key=lambda item: item[0])
    sensor.deltas = deque(entries)
    sensor.delta_counts = {delta: 1 for _when, delta in entries if delta is not None}
    sensor.updates = len(entries)
    key = saved['last_key']
    if key is not None and (not isinstance(key, str) or len(key) > 200):
        raise Invalid('key')
    sensor.last_key = key
    sensor.last_update = decode.time(saved['last_update'])
    sensor.last_accepted = _optional(saved['last_accepted'], -30.0, 60.0)
    sensor.last_accepted_mono = decode.time(saved['last_accepted_mono'])
    pending = saved['pending']
    if pending is not None:
        when, value, before, before_when = _list(pending, 4)
        sensor.pending = (decode.time(when), _number(value, -30.0, 60.0), _number(before, -30.0, 60.0),
                          decode.time(before_when))
    sensor.spikes = deque(decode.times(saved['spikes'], SPIKES_MAX))
    for name in ('step_until', 'jump_first', 'jump_last', 'airing_drop', 'airing_start', 'same_since',
                 'in_range_since'):
        setattr(sensor, name, decode.time(saved[name]))
    if (sensor.jump_first is None) != (sensor.jump_last is None) or (sensor.airing_start is None) != (saved['airing_ref'] is None):
        raise Invalid('pairs')
    sensor.airing_ref = _optional(saved['airing_ref'], -30.0, 60.0)
    sensor.same_count = _integer(saved['same_count'], 0, 2 ** 31)
    sensor.presence = _choice(saved['presence'], ('ok', 'missing', 'unknown'))
    sensor.numeric = _flag(saved['numeric'])
    sensor.implausible = _flag(saved['implausible'])
    sensor.battery = _optional(saved['battery'], 0.0, 100.0)
    name = saved['name']
    if name is not None and (not isinstance(name, str) or len(name) > 400):
        raise Invalid('name')
    sensor.name = name
    for latch in ('missing', 'unavailable', 'implausible_latch', 'battery_latch'):
        decode.latch(saved[latch], getattr(sensor, latch))
    sensor.stale_active = _flag(saved['stale_active'])
    if sensor.last_accepted is None and (sensor.history or sensor.pending is not None):
        raise Invalid('sensor')


def _room(decode, engine, entity, saved, circuit_kept):
    keys = ('latches', 'window', 'no_response', 'response_fails', 'cold_dwell', 'cold_since', 'cold_seen',
            'pump_off')
    saved = _dict(saved, keys)
    latches = saved['latches']
    if not isinstance(latches, dict) or not set(latches) <= set(LATCH_NEEDS):
        raise Invalid('latches')
    for code, value in latches.items():
        engine.latches[(code, entity)] = decode.latch(value, needed=LATCH_NEEDS[code])
    role = engine.sensors[entity].role
    window = saved['window']
    # A window belongs to a heating start of its circuit: with a fresh circuit
    # the start is gone, and so is the window (that start does not count).
    if window is not None and circuit_kept:
        window = _dict(window, ('trigger', 'start', 'active', 'paused', 'samples'))
        trigger = decode.trigger(window['trigger'], (role,))
        samples = []
        for row in _list(window['samples'], WINDOW_SAMPLES_MAX):
            t, value = _list(row, 2)
            samples.append((decode.duration(t), _number(value, -30.0, 60.0)))
        if not samples or decode.time(window['start']) is None:
            raise Invalid('window')
        item = rules.Window(trigger, decode.time(window['start']), samples[0][1])
        item.active, item.paused, item.samples = decode.duration(window['active']), decode.duration(window['paused']), samples
        engine.windows[entity] = item
    finding = saved['no_response']
    if finding is not None:
        finding = _dict(finding, ('trigger', 'onset', 'last'))
        onset, last = decode.time(finding['onset']), decode.time(finding['last'])
        if onset is None or last is None:
            raise Invalid('no_response')
        engine.no_response[entity] = rules.NoResponse(decode.trigger(finding['trigger'], (role,)), onset, last)
    fails = decode.times(saved['response_fails'], 1000)
    if fails:
        engine.response_fails[entity] = fails
    if saved['cold_dwell'] is not None:
        engine.cold_dwell[entity] = decode.latch(saved['cold_dwell'])
    for name, target in (('cold_since', engine.cold_since), ('cold_seen', engine.cold_seen),
                         ('pump_off', engine.pump_off)):
        when = decode.time(saved[name])
        if when is not None:
            target[entity] = when


def _circuit(decode, engine, circuit, saved, members_kept):
    """The circuit's phase, target and settle timing always (its type is
    unchanged); the checks over its rooms (heating starts, circuit_cold,
    circuit_warm, circuit_no_response) only while its rooms are unchanged."""
    keys = ('fingerprint', 'last_phase', 'last_target', 'last_mode', 'seen', 'settle_start', 'last_upper',
            'warm_settle_start', 'off_since', 'triggers', 'judged', 'why', 'unknown_since', 'basis_unknown',
            'circuit_cold', 'circuit_warm', 'circuit_no_response', 'no_response_last', 'starts', 'failed_starts',
            'failed_rooms', 'absent_since', 'last_kind')
    saved = _dict(saved, keys)
    _kind, rows = _list(saved['fingerprint'], 2)
    saved_members = set()
    for row in _list(rows, 2 * rules.MAX_SENSORS):
        entity, cooler, no_warm = _list(row, 3)
        if not isinstance(entity, str) or not rules.ENTITY_PATTERN.fullmatch(entity):
            raise Invalid('fingerprint')
        _cooler(cooler), _flag(no_warm)
        saved_members.add(entity)
    members = saved_members
    circuit.last_phase = _choice(saved['last_phase'], (None, 'day', 'setback'))
    circuit.last_target = _optional(saved['last_target'], 5.0, 30.0)
    circuit.last_mode = _choice(saved['last_mode'], (None,) + rules.CONTEXT_MODES)
    circuit.seen = _flag(saved['seen'])
    circuit.last_upper = _optional(saved['last_upper'], 5.0, 30.0)
    for name in ('settle_start', 'warm_settle_start', 'off_since', 'unknown_since', 'no_response_last',
                 'absent_since'):
        setattr(circuit, name, decode.time(saved[name]))
    circuit.triggers = _integer(saved['triggers'], 0, 2 ** 53)
    circuit.judged = _choice(saved['judged'], (None, 'yes', 'inactive', 'unknown'))
    circuit.why = _choice(saved['why'], (None,) + rules.WHYS)
    basis = []
    for row in _list(saved['basis_unknown'], 24 * 8 + 2):
        when, seconds = _list(row, 2)
        basis.append((decode.time(when), _number(seconds, 0, 8 * 86400)))
    circuit.basis_unknown = deque(sorted(basis, key=lambda item: item[0]))   # oldest first, as the rules expire it
    decode.latch(saved['circuit_cold'], circuit.circuit_cold, ('room', 'target', 'count'))
    decode.latch(saved['circuit_warm'], circuit.circuit_warm, ('room', 'target'))
    current = saved['circuit_no_response']
    if current is not None:
        trigger, onset, count, names = _list(current, 4)
        onset = decode.time(onset)
        if onset is None:
            raise Invalid('circuit_no_response')
        circuit.circuit_no_response = (decode.trigger(trigger, (circuit.name,)), onset,
                                       _integer(count, 0, rules.MAX_SENSORS),
                                       tuple(decode.members(names, members)))
    for row in _list(saved['starts'], 64):
        trigger, start = _list(row, 2)
        start = _dict(start, ('start', 'armed', 'fail', 'pass', 'usable'))
        when = decode.time(start['start'])
        if when is None:
            raise Invalid('start')
        circuit.starts[decode.trigger(trigger, (circuit.name,))] = {
            'start': when, 'armed': set(decode.members(start['armed'], members)),
            'fail': set(decode.members(start['fail'], members)), 'pass': set(decode.members(start['pass'], members)),
            'usable': _integer(start['usable'], 0, rules.MAX_SENSORS)}
    circuit.failed_starts = decode.times(saved['failed_starts'], 1000)
    circuit.failed_rooms = set(decode.members(saved['failed_rooms'], members))
    circuit.last_kind = _choice(saved['last_kind'], (None,) + tuple(rules.TYPE_CONSTANTS))
    if not members_kept:
        circuit.circuit_cold, circuit.circuit_warm = rules.Latch(), rules.Latch()
        circuit.circuit_no_response = circuit.no_response_last = None
        circuit.starts, circuit.failed_starts, circuit.failed_rooms = {}, [], set()


def uptime(path='/proc/uptime'):
    """Seconds since the host booted, or None."""
    try:
        with open(path, 'r', encoding='ascii') as stream:
            value = float(stream.read(64).split()[0])
        return value if math.isfinite(value) and value >= 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _gap(body, mono, wall, boot, since_boot):
    """Seconds the app was stopped, or an Invalid reason."""
    saved_wall, saved_mono = _number(body['saved_wall']), _number(body['saved_mono'])
    if isinstance(boot, str) and body['boot'] == boot:
        gap = mono - saved_mono
        if gap < 0:
            raise Invalid('state_clock')
    else:
        gap = wall - saved_wall
        if gap < -RESTORE_SKEW_S:
            raise Invalid('state_clock')
        gap = max(0.0, gap)
        if (isinstance(boot, str) and isinstance(body['boot'], str) and type(since_boot) in (int, float)
                and math.isfinite(since_boot)):
            # Saved in an earlier boot: at least the time since this boot has
            # passed, whatever a clock without a battery says before NTP.
            gap = max(gap, float(since_boot))
    if gap > RESTORE_MAX_GAP_S:
        raise Invalid('state_too_old')
    return gap


def restore(options, document, *, mono, wall, boot, since_boot=None):
    """(engine, outcome). outcome: state_restored, state_partly_restored (some
    rooms or circuits fresh), state_incompatible, state_clock, state_too_old or
    state_unreadable; for every outcome but the first two the engine is fresh."""
    try:
        if not isinstance(document, dict) or set(document) != DOCUMENT_KEYS:
            raise Invalid('state_unreadable')
        body = {key: value for key, value in document.items() if key != 'sha256'}
        if document['sha256'] != checksum(body):
            raise Invalid('state_unreadable')
        if body['schema'] != STATE_SCHEMA or body['rules'] != rules_digest():
            raise Invalid('state_incompatible')
        if body['boot'] is not None and not (isinstance(body['boot'], str) and BOOT_ID.fullmatch(body['boot'])):
            raise Invalid('state_unreadable')
        gap = _gap(body, mono, wall, boot, since_boot)
    except Invalid as problem:
        reason = problem.args[0] if problem.args and str(problem.args[0]).startswith('state_') else 'state_unreadable'
        return rules.HouseEngine(options), reason
    except Exception:
        return rules.HouseEngine(options), 'state_unreadable'
    try:
        engine, partial = _build(options, body['engine'], mono, gap)
    except Exception:
        # Never half a state: start as a new process did before 1.6.6.
        return rules.HouseEngine(options), 'state_unreadable'
    return engine, 'state_partly_restored' if partial else 'state_restored'


def _build(options, saved, now, gap):
    saved = _dict(saved, ('rooms', 'circuits', 'entities', 'frost', 'all_unavailable'))
    engine = rules.HouseEngine(options)
    decode = _Decoder(options, now - gap)
    rooms, circuits = saved['rooms'], _dict(saved['circuits'], rules.CIRCUITS)
    if not isinstance(rooms, dict) or len(rooms) > rules.MAX_SENSORS:
        raise Invalid('rooms')
    _list(saved['entities'], rules.MAX_SENSORS)
    # kept_circuits: unchanged type and rooms (with an unchanged type only the
    # circuit's timing is restored, see _circuit).
    kept_circuits, kept_rooms = set(), set()
    for name in rules.CIRCUITS:
        entry = circuits[name]
        current = circuit_fingerprint(options, name)
        if not isinstance(entry, dict) or not isinstance(entry.get('fingerprint'), list) \
                or len(entry['fingerprint']) != 2 or entry['fingerprint'][0] != current[0]:
            continue
        same = entry['fingerprint'] == current
        _circuit(decode, engine, engine.circuits[name], entry, same)
        if same:
            kept_circuits.add(name)
    for entity, entry in rooms.items():
        if entity not in engine.sensors:
            continue
        entry = _dict(entry, ('fingerprint', 'sensor', 'room'))
        if entry['fingerprint'] != room_fingerprint(options, entity):
            continue
        sensor = engine.sensors[entity]
        _sensor(decode, sensor, entry['sensor'])
        _room(decode, engine, entity, entry['room'], sensor.role in kept_circuits or sensor.role == 'haus')
        kept_rooms.add(entity)
    partial = kept_rooms != set(options.entities) or kept_circuits != set(rules.CIRCUITS)
    # A heating start that waits for a room without its window (not expected:
    # a kept circuit keeps all its rooms) is dropped unjudged, never judged on
    # the rooms that remain.
    for name in kept_circuits:
        circuit = engine.circuits[name]
        for key in list(circuit.starts):
            armed = circuit.starts[key]['armed']
            if any(engine.windows.get(entity) is None or engine.windows[entity].trigger != key for entity in armed):
                del circuit.starts[key]
    for entity in list(engine.windows):
        window = engine.windows[entity]
        circuit = engine.circuits.get(engine.sensors[entity].role)
        if circuit is None or window.trigger not in circuit.starts or entity not in circuit.starts[window.trigger]['armed']:
            del engine.windows[entity]
    # Sensor names give the labels (the identities of the rows the VM keeps).
    engine.refresh_labels()
    engine._types = {name: circuit.last_kind for name, circuit in engine.circuits.items()}
    if saved['entities'] == list(options.entities):
        decode.latch(saved['all_unavailable'], engine.all_unavailable)
    # Old windows: dropped (as on the next evaluation); the rules' own expiry
    # handles findings and dwells.
    span = rules.NO_RESPONSE_SPAN_S
    for entity in list(engine.response_fails):
        engine.response_fails[entity] = [when for when in engine.response_fails[entity] if now - when <= span]
    for circuit in engine.circuits.values():
        circuit.failed_starts = [when for when in circuit.failed_starts if now - when <= span]
        while circuit.basis_unknown and now - circuit.basis_unknown[0][0] > 7 * 86400:
            circuit.basis_unknown.popleft()
    for sensor in engine.sensors.values():
        sensor.expire(now)
    # The stopped time: as if Home Assistant had been unavailable for it.
    engine._pause_all(now, gap)
    frost = saved['frost']
    if frost is not None:
        since, onset = _list(frost, 2)
        since, onset = decode.time(since), decode.onset(onset, ('room', 'count'))
        eligible = list(options.hc1 + options.hc2) + [e for e in options.haus if e in options.frost]
        active = [e for e in eligible if engine.latches.get(('room_frost', e)) is not None
                  and engine.latches[('room_frost', e)].active]
        # Only while the restored rooms themselves still make it a frost fault.
        if since is not None and len(active) >= 2:
            engine.frost = (since, onset)
    return engine, partial


# ---------------------------------------------------------------------------
# The file

class StateFile:
    """One private JSON file, written atomically; reads never raise."""

    def __init__(self, path=STATE_PATH, max_bytes=STATE_MAX_BYTES):
        self.path, self.max_bytes = path, max_bytes

    def load(self):
        """(document, problem): (None, None) without a file."""
        self._clean_temporaries()
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return None, None
        except OSError:
            return None, 'state_unreadable'
        try:
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_size > self.max_bytes or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o022):
                    return None, 'state_unreadable'
                raw = stream.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                return None, 'state_unreadable'
            return rules.strict_json(raw), None
        except (OSError, ValueError, UnicodeError, RecursionError):
            return None, 'state_unreadable'

    def save(self, document):
        """True once the document is on disk; never raises."""
        temporary = None
        try:
            raw = rules.canonical(document).encode('utf-8')
            if len(raw) > self.max_bytes or os.path.islink(self.path):
                return False
            directory = os.path.dirname(self.path) or '.'
            fd, temporary = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=directory)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            folder = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
            return True
        except (OSError, ValueError, TypeError, RecursionError):
            return False
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _clean_temporaries(self):
        """A temporary file left by a stop during a write (never the state itself)."""
        directory = os.path.dirname(self.path) or '.'
        for name in glob.glob(os.path.join(glob.escape(directory), TEMP_PREFIX + '*')):
            try:
                if os.path.isfile(name) and not os.path.islink(name):
                    os.unlink(name)
            except OSError:
                pass
