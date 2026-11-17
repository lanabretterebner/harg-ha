"""House-sensor rules of the operation monitor: options, labels, rules, report.

Pure module: no network, no files, no clock. The caller passes monotonic and
wall seconds, the HA state, the sensor samples and a validated adapter
context. Room sensors are monitoring only; nothing here can control heating.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import json
import math
import re

APP_VERSION = '1.6.1'
REPORT_SCHEMA = 'harg-house-report/1'
CONTEXT_SCHEMA_PREFIX = 'harg-house-context/'
CONTEXT_MAJORS = (1,)
CIRCUITS = ('hc1', 'hc2')

ENTITY_PATTERN = re.compile(r'sensor\.[a-z0-9_]{1,200}')
CONTEXT_ID_PATTERN = re.compile(r'[0-9a-f]{16}:[0-9]{1,15}')
INSTANCE_PATTERN = re.compile(r'[0-9a-f]{32}')
LABEL_CHAR = re.compile(r'[\w ÄÖÜäöüß.,()/+-]')
LABEL_PATTERN = re.compile(r'[\w ÄÖÜäöüß.,()/+-]{1,40}')
ENTITY_LIKE = re.compile(r'[a-z_]+\.[a-z0-9_]+')
LABEL_MAX = 40
MAX_SENSORS = 24
MAX_FINDINGS = 24
MAX_FOLDED = 1000
MAX_HELD = 2592000
MAX_REPORT_BYTES = 16384
MAX_CONTEXT_BYTES = 4096

# Timing constants (spec 9.5).
SAMPLE_PERIOD_S = 60
CONTEXT_VALID_S = 180
UNKNOWN_GRACE_S = 900
CONTEXT_FREE_PAUSE_S = 7200
SENSOR_USABLE_AGE_S = 21600
SENSOR_STALE_FINDING_S = 86400
MISSING_DWELL_S = 1800
UNAVAILABLE_DWELL_S = 1800
IMPLAUSIBLE_DWELL_S = 600
STEP_UNUSABLE_S = 1800
EVENT_EXPIRY_S = 86400
SPIKE_WINDOW_S = 900
STUCK_S = 259200
BATTERY_DWELL_S = 3600
ALL_UNAVAILABLE_DWELL_S = 1800
FROST_DWELL_S = 600
NO_RESPONSE_EXPIRY_S = 86400
MEDIAN_WINDOW_S = 900
HISTORY_MAX = 12000
HISTORY_AGE_S = 7 * 86400
NOT_SUPPLYING_RESTART_S = 3600
MAX_ELAPSED_S = 300

# Per circuit type (spec 6.2).
TYPE_CONSTANTS = {
    'radiator': {'settle': 7200, 'airing': 3600, 'cold_room': 5400, 'cold_circuit': 10800,
                 'window': 3600, 'flow_margin': 8.0, 'warm_flow': 5.0, 'warm_night': 7200,
                 'warm_circuit': 21600, 'hot': 3600, 'limit_cold': 21600, 'pause': 7200,
                 'startup': 1800},
    'floor': {'settle': 28800, 'airing': 10800, 'cold_room': 14400, 'cold_circuit': 28800,
              'window': 21600, 'flow_margin': 3.0, 'warm_flow': 2.0, 'warm_night': 14400,
              'warm_circuit': 43200, 'hot': 3600, 'limit_cold': 43200, 'pause': 21600,
              'startup': 1800},
}
# 'startup': after a heat-up trigger the pump may still be starting and the
# flow still cold. During this allowance a pump off or a flow below the margin
# pauses the response window instead of disarming it (W starts counting once
# the circuit really supplies).
# Warm rules settle on a change of U = max(T, day), the mode or a restart of
# supplying, not on every phase change: U stays the day target across a normal
# day/setback cycle, so the night setback is no reason to stop judging them.
OPTION_TYPES = {'automatisch': None, 'heizkoerper': 'radiator', 'fussboden': 'floor'}
MODES = ('beobachten', 'melden')
COOLER_ALLOWANCE = 2.0

# Code table (spec 9.4), in its order: check, level.
CODES = {
    'room_cold': ('Räume', 'Hinweis'), 'circuit_cold': ('Räume', 'Störung'),
    'no_response': ('Räume', 'Hinweis'), 'circuit_no_response': ('Räume', 'Störung'),
    'room_warm': ('Räume', 'Hinweis'), 'circuit_warm': ('Räume', 'Hinweis'),
    'room_hot': ('Räume', 'Hinweis'), 'limit_cold': ('Räume', 'Hinweis'),
    'room_frost': ('Räume', 'Hinweis'), 'frost': ('Räume', 'Störung'),
    'sensor_missing': ('Raumsensoren', 'Hinweis'), 'sensor_unavailable': ('Raumsensoren', 'Hinweis'),
    'sensor_stale': ('Raumsensoren', 'Hinweis'), 'sensor_implausible': ('Raumsensoren', 'Hinweis'),
    'sensor_jumping': ('Raumsensoren', 'Hinweis'), 'sensor_stuck': ('Raumsensoren', 'Hinweis'),
    'sensor_battery': ('Raumsensoren', 'Hinweis'),
    'sensors_all_unavailable': ('Raumsensoren', 'Hinweis'),
    'config_invalid': ('Raumsensoren', 'Hinweis'), 'config_circuit_absent': ('Raumsensoren', 'Hinweis'),
}
CODE_ORDER = {code: index for index, code in enumerate(CODES)}
CONTEXT_BOUND = frozenset(('room_cold', 'circuit_cold', 'no_response', 'circuit_no_response',
                           'room_warm', 'circuit_warm', 'room_hot', 'limit_cold'))
# The three codes that sensors_all_unavailable replaces.
UNAVAILABLE_CODES = ('sensor_missing', 'sensor_unavailable', 'sensor_stale')
REASONS = ('config_invalid', 'ha_starting', 'ha_api_unavailable', 'no_context', 'context_version_unsupported')
WHYS = ('context', 'phase', 'type', 'sensors', 'absent', 'ha')
ACK_RESULTS = ('accepted', 'context_mismatch', 'instance_conflict',
               'rejected_schema', 'rejected_version', 'rejected_limits')
CONTEXT_MODES = ('off', 'auto', 'day', 'setback', 'once_day', 'once_setback')

# Heizkreisstatus classes (spec 6.1).
SUPPLYING, TRANSITION, LIMIT_OFF, INACTIVE, UNKNOWN = 'supplying', 'transition', 'limit_off', 'inactive', 'unknown'
STATUS_CLASS = {1: SUPPLYING, 3: SUPPLYING, 2: TRANSITION, 4: LIMIT_OFF, 5: LIMIT_OFF,
                0: INACTIVE, 6: INACTIVE, 7: INACTIVE, 8: INACTIVE, 9: INACTIVE, 10: INACTIVE}


def strict_json(raw):
    """Decode UTF-8 JSON; duplicate keys and non-finite numbers are refused."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result

    def constant(_value):
        raise ValueError('constant')

    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode('utf-8')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def half(value):
    """Round half up to the 0.5 K grid; always a float."""
    return float(math.floor(value * 2 + 0.5) / 2)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


# ---------------------------------------------------------------------------
# Options (spec 2.1)

LIST_KEYS = ('heizkreis_1_sensoren', 'heizkreis_2_sensoren', 'haus_sensoren',
             'kuehlere_raeume', 'ohne_ueberwaermung', 'haus_frost_sensoren')


@dataclass(frozen=True)
class HouseOptions:
    valid: bool
    mode: str = 'beobachten'
    option_types: tuple = (None, None)
    hc1: tuple = ()
    hc2: tuple = ()
    haus: tuple = ()
    cooler: frozenset = frozenset()
    no_warm: frozenset = frozenset()
    frost: frozenset = frozenset()
    raw_count: int = 0
    # Distinct entries per circuit list of an invalid configuration (0..24).
    raw_circuits: tuple = (0, 0)

    @property
    def entities(self):
        return self.hc1 + self.hc2 + self.haus

    @property
    def configured(self):
        return len(self.entities) if self.valid else self.raw_count

    def circuit_entities(self, circuit):
        return self.hc1 if circuit == 'hc1' else self.hc2


def _raw_entries(options, key):
    value = options.get(key) if isinstance(options, dict) else None
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _raw_count(options):
    seen = set()
    for key in LIST_KEYS[:3]:
        seen.update(_raw_entries(options, key))
    # An invalid configuration still keeps the house part visible, so the
    # config_invalid row reaches the adapter (configured 0 would mean "off").
    return max(1, min(MAX_SENSORS, len(seen)))


def _raw_circuits(options):
    """Per circuit list: how many distinct entries the owner gave. The report
    names them as the circuit's sensors, so the adapter shows those circuits as
    not judged (Räume Unbekannt) instead of Normal while nothing is monitored."""
    return tuple(min(MAX_SENSORS, len(_raw_entries(options, key))) for key in LIST_KEYS[:2])


def parse_house_options(options):
    """Strict parser. Never raises; any violation gives valid=False."""
    try:
        if not isinstance(options, dict):
            raise ValueError
        mode = options.get('raumueberwachung_modus', 'beobachten')
        if mode not in MODES:
            raise ValueError
        types = []
        for key in ('heizkreis_1_art', 'heizkreis_2_art'):
            value = options.get(key, 'automatisch')
            if not isinstance(value, str) or value not in OPTION_TYPES:
                raise ValueError
            types.append(OPTION_TYPES[value])
        lists = {}
        for key in LIST_KEYS:
            value = options.get(key, [])
            if not isinstance(value, list):
                raise ValueError
            for item in value:
                if not isinstance(item, str) or not ENTITY_PATTERN.fullmatch(item):
                    raise ValueError
            if len(set(value)) != len(value):
                raise ValueError
            lists[key] = tuple(value)
        hc1, hc2, haus = lists['heizkreis_1_sensoren'], lists['heizkreis_2_sensoren'], lists['haus_sensoren']
        assigned = hc1 + hc2 + haus
        if len(assigned) > MAX_SENSORS or len(set(assigned)) != len(assigned):
            raise ValueError
        circuit = set(hc1) | set(hc2)
        if not set(lists['kuehlere_raeume']) <= circuit or not set(lists['ohne_ueberwaermung']) <= circuit:
            raise ValueError
        if not set(lists['haus_frost_sensoren']) <= set(haus):
            raise ValueError
        return HouseOptions(True, mode, tuple(types), hc1, hc2, haus,
                            frozenset(lists['kuehlere_raeume']), frozenset(lists['ohne_ueberwaermung']),
                            frozenset(lists['haus_frost_sensoren']), len(assigned))
    except (ValueError, TypeError):
        mode = options.get('raumueberwachung_modus') if isinstance(options, dict) else None
        return HouseOptions(False, mode if mode in MODES else 'beobachten', raw_count=_raw_count(options),
                            raw_circuits=_raw_circuits(options))


def sanitize_label(name):
    if not isinstance(name, str):
        return ''
    text = ''.join(char if LABEL_CHAR.fullmatch(char) else ' ' for char in name[:400])
    text = ' '.join(text.split())[:LABEL_MAX].strip()
    if not text or ENTITY_LIKE.fullmatch(text):
        return ''
    return text


def make_labels(entities, names):
    """Labels in list order (hc1, hc2, haus); unique, at most 40 characters."""
    labels, used = {}, set()
    for position, entity in enumerate(entities, 1):
        base = sanitize_label(names.get(entity)) or f'Sensor {position}'
        label, counter = base, 1
        while label in used:
            counter += 1
            suffix = f' ({counter})'
            label = base[:LABEL_MAX - len(suffix)].rstrip() + suffix
        used.add(label)
        labels[entity] = label
    return labels


# ---------------------------------------------------------------------------
# Context validation (spec 9.2)

CONTEXT_KEYS = frozenset(('schema', 'context', 'online', 'maintenance', 'outdoor', 'outdoor_avg',
                          'report_ack', 'circuits'))
CONTEXT_CIRCUIT_KEYS = frozenset(('mode', 'phase', 'target', 'day_target', 'setback_target', 'status', 'pump',
                                  'flow', 'flow_target', 'type', 'type_source'))


def _bounded(value, low, high):
    return float(value) if number(value) and low <= value <= high else None


def context_major(document):
    schema = document.get('schema') if isinstance(document, dict) else None
    if not isinstance(schema, str) or not schema.startswith(CONTEXT_SCHEMA_PREFIX):
        return None
    major = schema[len(CONTEXT_SCHEMA_PREFIX):]
    if not re.fullmatch(r'[1-9][0-9]{0,1}', major):
        return None
    return int(major)


def validate_context(raw):
    """Return (status, context). status: ok, unsupported or invalid."""
    try:
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_CONTEXT_BYTES:
            return 'invalid', None
        document = strict_json(raw)
    except (ValueError, UnicodeError, RecursionError):
        return 'invalid', None
    if not isinstance(document, dict):
        return 'invalid', None
    major = context_major(document)
    if major is None:
        return 'invalid', None
    if major not in CONTEXT_MAJORS:
        return 'unsupported', None
    # v1 has exact key sets (spec 9.1): a missing or added key is another major.
    if set(document) != CONTEXT_KEYS:
        return 'invalid', None
    context = document.get('context')
    if not isinstance(context, str) or not CONTEXT_ID_PATTERN.fullmatch(context):
        return 'invalid', None
    circuits = document.get('circuits')
    if (not isinstance(circuits, dict) or set(circuits) != set(CIRCUITS)
            or any(not isinstance(row, dict) or set(row) != CONTEXT_CIRCUIT_KEYS for row in circuits.values())):
        return 'invalid', None
    for key in ('online', 'maintenance'):
        if type(document.get(key)) is not bool:
            return 'invalid', None
    for key in ('outdoor', 'outdoor_avg'):
        value = document.get(key)
        if value is not None and type(value) not in (int, float):
            return 'invalid', None
    ack = document.get('report_ack')
    if ack is not None and (not isinstance(ack, dict) or set(ack) != {'instance', 'seq', 'result'}):
        return 'invalid', None
    result = {'context': context, 'online': document['online'], 'maintenance': document['maintenance'],
              'outdoor': _bounded(document.get('outdoor'), -60, 60),
              'outdoor_avg': _bounded(document.get('outdoor_avg'), -60, 60),
              'report_ack': None, 'circuits': {}}
    if (ack is not None and isinstance(ack.get('instance'), str) and INSTANCE_PATTERN.fullmatch(ack['instance'])
            and type(ack.get('seq')) is int and 0 <= ack['seq'] <= 2 ** 53 - 1 and ack.get('result') in ACK_RESULTS):
        result['report_ack'] = {'instance': ack['instance'], 'seq': ack['seq'], 'result': ack['result']}
    for name in CIRCUITS:
        source = circuits[name]
        status = source.get('status')
        pump = source.get('pump')
        circuit_type = source.get('type') if source.get('type') in ('radiator', 'floor', 'absent') else None
        result['circuits'][name] = {
            'mode': source.get('mode') if source.get('mode') in CONTEXT_MODES else None,
            'phase': source.get('phase') if source.get('phase') in ('day', 'setback') else None,
            'target': _bounded(source.get('target'), 5, 30),
            'day_target': _bounded(source.get('day_target'), 5, 30),
            'setback_target': _bounded(source.get('setback_target'), 5, 30),
            'status': status if type(status) is int and 0 <= status <= 65535 else None,
            'pump': pump if type(pump) is bool else None,
            'flow': _bounded(source.get('flow'), 0, 120),
            'flow_target': _bounded(source.get('flow_target'), 0, 120),
            'type': circuit_type,
            'type_source': 'controller' if circuit_type is not None and source.get('type_source') == 'controller' else None,
        }
    return 'ok', result


# ---------------------------------------------------------------------------
# Timers

class Timer:
    """How long a condition has held. True runs, None pauses, False resets."""
    __slots__ = ('since', 'total', 'paused')

    def __init__(self):
        self.reset()

    def reset(self):
        self.since, self.total, self.paused = None, 0.0, 0.0

    def update(self, condition, now, elapsed, pause_limit):
        if condition is True:
            if self.since is None:
                self.since, self.total = now, 0.0
            else:
                self.total += elapsed
            self.paused = 0.0
        elif condition is None and self.since is not None:
            self.paused += elapsed
            if self.paused > pause_limit:
                self.reset()
        else:
            self.reset()


class Latch:
    """A finding: fires after `dwell` of `fire`, then holds while `hold` holds."""
    __slots__ = ('timer', 'active', 'onset')

    def __init__(self):
        self.timer, self.active, self.onset = Timer(), False, None

    def update(self, fire, hold, now, elapsed, dwell, pause_limit, onset=None):
        if self.active:
            self.timer.update(hold, now, elapsed, pause_limit)
            if self.timer.since is None:
                self.active, self.onset = False, None
        else:
            self.timer.update(fire, now, elapsed, pause_limit)
            if self.timer.since is not None and self.timer.total >= dwell:
                self.active = True
                self.onset = onset() if callable(onset) else onset
        return self.active

    def clear(self):
        self.timer.reset()
        self.active, self.onset = False, None

    def held(self, now):
        return held_seconds(now, self.timer.since)


def held_seconds(now, since):
    if since is None:
        return 0
    return int(min(MAX_HELD, max(0.0, now - since)))


def median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def parse_time(text):
    if not isinstance(text, str) or len(text) > 64:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.timestamp()


# ---------------------------------------------------------------------------
# Sensors

class Sensor:
    """Per-sensor history and health (spec 6.4)."""

    def __init__(self, entity, role, position):
        self.entity, self.role, self.position = entity, role, position
        self.history = deque(maxlen=HISTORY_MAX)   # accepted (mono, value)
        self.deltas = deque()                       # (mono, |delta|) of accepted updates
        self.delta_counts = {}
        self.updates = 0                            # accepted updates seen in the 7-day window
        self.last_key = None
        self.last_update = None                     # mono of the last distinct update
        self.last_accepted = None
        self.pending = None                         # spike candidate (mono, value, previous)
        self.spikes = deque()
        self.step_until = None
        self.jump_first = self.jump_last = None
        self.airing_drop = None
        self.same_since = None
        self.same_count = 0
        self.presence = 'unknown'                   # ok, missing, unknown
        self.numeric = False
        self.implausible = False
        self.in_range_since = None
        self.battery = None
        self.name = None
        self.missing = Latch()
        self.unavailable = Latch()
        self.implausible_latch = Latch()
        self.battery_latch = Latch()
        self.stale_active = False

    # -- value handling
    def step_size(self):
        if self.updates < 20 or not self.delta_counts:
            return 0.1
        return min(self.delta_counts)

    def expire(self, now):
        while self.deltas and (now - self.deltas[0][0] > HISTORY_AGE_S or len(self.deltas) > HISTORY_MAX):
            _mono, delta = self.deltas.popleft()
            if delta is not None:
                self.delta_counts[delta] -= 1
                if not self.delta_counts[delta]:
                    del self.delta_counts[delta]
            self.updates -= 1
        while self.history and now - self.history[0][0] > HISTORY_AGE_S:
            self.history.popleft()
        while self.spikes and now - self.spikes[0] > EVENT_EXPIRY_S:
            self.spikes.popleft()

    def _accept(self, mono, value):
        previous = self.last_accepted
        # Only nonzero changes between consecutive updates define the step size.
        delta = None if previous is None else round(abs(value - previous), 3)
        if delta is not None and delta < 0.001:
            delta = None
        if delta is not None:
            self.delta_counts[delta] = self.delta_counts.get(delta, 0) + 1
        self.deltas.append((mono, delta))
        self.updates += 1
        if previous is not None and abs(value - previous) < 0.001:
            self.same_count += 1
        else:
            self.same_since, self.same_count = mono, 0
        recent = self.recent(mono)
        if recent and max(recent) - value >= 1.0:
            self.airing_drop = mono
        self.history.append((mono, value))
        self.last_accepted = value

    def _occurrence(self, mono):
        if self.jump_last is None or mono - self.jump_last >= EVENT_EXPIRY_S:
            self.jump_first = mono
        self.jump_last = mono

    def ingest(self, mono, value):
        """One distinct numeric in-range update."""
        if self.pending is not None:
            when, candidate, before = self.pending
            if mono - when <= SPIKE_WINDOW_S and abs(value - before) <= 1.0 and abs(value - candidate) >= 3.0:
                self.pending = None
                self.spikes.append(when)
                while self.spikes and mono - self.spikes[0] > EVENT_EXPIRY_S:
                    self.spikes.popleft()
                if len(self.spikes) >= 3:
                    self._occurrence(mono)
            else:
                self.pending = None
                self._accept(when, candidate)
        if self.last_accepted is None:
            self._accept(mono, value)
        elif abs(value - self.last_accepted) >= 8.0:
            # A step, whatever the gap: the new value is the new baseline.
            self.history.clear()
            self.same_since, self.same_count = mono, 0
            self.last_accepted = value
            self.history.append((mono, value))
            self.step_until = mono + STEP_UNUSABLE_S
            self._occurrence(mono)
        elif abs(value - self.last_accepted) >= 3.0:
            self.pending = (mono, value, self.last_accepted)
        else:
            self._accept(mono, value)

    def settle_pending(self, now):
        if self.pending is not None and now - self.pending[0] > SPIKE_WINDOW_S:
            when, candidate, _before = self.pending
            self.pending = None
            self._accept(when, candidate)

    def recent(self, now):
        """Accepted values of the last 15 min (history is in time order)."""
        values = []
        for when, value in reversed(self.history):
            if now - when > MEDIAN_WINDOW_S:
                break
            if when <= now:
                values.append(value)
        return values

    def value(self, now):
        recent = self.recent(now)
        if recent:
            return median(recent)
        return self.last_accepted

    # -- health
    def stuck(self, now):
        return (self.same_since is not None and now - self.same_since >= STUCK_S
                and self.same_count >= 10 and self.step_size() < 0.5)

    def stale(self, now):
        return self.last_update is not None and now - self.last_update > SENSOR_USABLE_AGE_S

    def unusable_reason(self, now):
        if self.presence == 'missing':
            return 'missing'
        if self.presence != 'ok' or not self.numeric:
            return 'unavailable'
        if self.stale(now):
            return 'stale'
        if self.implausible:
            return 'implausible'
        if self.step_until is not None and now < self.step_until:
            return 'step'
        if self.stuck(now):
            return 'stuck'
        if self.last_accepted is None:
            return 'novalue'
        return None

    def usable(self, now):
        return self.unusable_reason(now) is None

    def airing(self, now, recovery):
        return self.airing_drop is not None and now - self.airing_drop < recovery

    def jumping(self, now):
        return self.jump_last is not None and now - self.jump_last < EVENT_EXPIRY_S


def classify_state(state):
    """Return (presence, numeric, value in °C or None, battery, name)."""
    if not isinstance(state, dict):
        return 'missing', False, None, None, None
    entity = state.get('entity_id')
    attributes = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
    name = attributes.get('friendly_name') if isinstance(attributes.get('friendly_name'), str) else None
    unit = attributes.get('unit_of_measurement')
    battery = None
    for key in ('battery_level', 'battery'):
        level = attributes.get(key)
        if isinstance(level, str):
            try:
                level = float(level)
            except ValueError:
                level = None
        if number(level) and 0 <= level <= 100:
            battery = float(level)
            break
    if not isinstance(entity, str) or not ENTITY_PATTERN.fullmatch(entity) or unit not in ('°C', '°F'):
        return 'missing', False, None, battery, name
    raw = state.get('state')
    try:
        value = float(raw) if isinstance(raw, str) and len(raw) <= 32 else None
    except ValueError:
        value = None
    if value is None or not math.isfinite(value):
        return 'ok', False, None, battery, name
    if unit == '°F':
        value = (value - 32.0) * 5.0 / 9.0
    return 'ok', True, value, battery, name


# ---------------------------------------------------------------------------
# Circuits

class Window:
    """One armed no-response window for one sensor."""
    __slots__ = ('trigger', 'start', 'active', 'paused', 'samples')

    def __init__(self, trigger, start, value):
        self.trigger, self.start, self.active, self.paused = trigger, start, 0.0, 0.0
        self.samples = [(0.0, value)]


class NoResponse:
    __slots__ = ('trigger', 'onset')

    def __init__(self, trigger, onset):
        self.trigger, self.onset = trigger, onset


class Circuit:
    def __init__(self, name, option_type):
        self.name, self.option_type = name, option_type
        self.last_phase = self.last_target = self.last_mode = None
        self.seen = False
        self.settle_start = None
        self.last_upper = None
        self.warm_settle_start = None
        self.off_since = None
        self.triggers = 0
        self.judged, self.why, self.unknown_since = None, None, None
        self.basis_unknown = deque()
        self.circuit_cold = Latch()
        self.circuit_warm = Latch()
        self.circuit_no_response = None   # (trigger, onset mono, count, members)
        self.absent_since = None
        self.last_kind = None             # last known radiator/floor, for pause limits


class HouseEngine:
    """Stateful rule evaluation; one per app process, injected clock."""

    def __init__(self, options):
        if not options.valid:
            raise ValueError('options_invalid')
        self.options = options
        self.sensors = {}
        position = 0
        for role, entities in (('hc1', options.hc1), ('hc2', options.hc2), ('haus', options.haus)):
            for entity in entities:
                position += 1
                self.sensors[entity] = Sensor(entity, role, position)
        self.circuits = {name: Circuit(name, options.option_types[index]) for index, name in enumerate(CIRCUITS)}
        self.labels = make_labels(options.entities, {})
        self.latches = {}       # (code, entity) -> Latch for per-room rules
        self.windows = {}       # entity -> Window
        self.no_response = {}   # entity -> NoResponse
        self.frost = None       # (since, onset dict)
        self.all_unavailable = Latch()
        self.pump_off = {}      # entity -> mono since the pump is reported off (room_warm)
        self._types = {}
        self.ctx = None
        self.wall = None
        self.last_now = None
        self.monitor, self.reason = 'unknown', 'no_context'
        self.paused = True
        self.findings = []

    # -- helpers
    def refresh_labels(self):
        self.labels = make_labels(self.options.entities, {entity: sensor.name for entity, sensor in self.sensors.items()})

    def latch(self, code, entity):
        return self.latches.setdefault((code, entity), Latch())

    def effective_type(self, circuit, ctx):
        controller = ctx['circuits'][circuit.name]['type'] if ctx else None
        if controller == 'absent':
            return 'absent', 'controller'
        if circuit.option_type is not None:
            return circuit.option_type, 'option'
        if controller is not None:
            return controller, 'controller'
        return None, None

    @staticmethod
    def status_class(ctx, circuit_name):
        if ctx is None or not ctx['online'] or ctx['maintenance']:
            return UNKNOWN
        source = ctx['circuits'][circuit_name]
        cls = STATUS_CLASS.get(source['status'], UNKNOWN)
        if cls == SUPPLYING and (source['phase'] is None or source['target'] is None):
            return UNKNOWN
        return cls

    # -- main step
    def step(self, now, *, wall, ha, samples, context, context_state, local_minutes):
        """Advance all rules by one sample.

        now: monotonic seconds; wall: epoch seconds (only to date sensor
        updates by their HA timestamp). ha: running, starting or unavailable.
        samples: entity -> state dict, or the string 'missing' (HTTP 404);
        an entity not in samples was not read. context: a validated context
        received at most CONTEXT_VALID_S ago, or None. context_state: ok,
        none or unsupported. local_minutes: minutes since local midnight or None.
        """
        elapsed = 0.0 if self.last_now is None else min(MAX_ELAPSED_S, max(0.0, now - self.last_now))
        self.last_now, self.wall = now, wall
        for entity in [e for e, f in self.no_response.items() if now - f.onset >= NO_RESPONSE_EXPIRY_S]:
            del self.no_response[entity]
        self.paused = ha != 'running'
        if ha == 'starting':
            self.monitor, self.reason = 'unknown', 'ha_starting'
        elif ha != 'running':
            self.monitor, self.reason = 'unknown', 'ha_api_unavailable'
        elif context_state == 'ok' and context is not None:
            self.monitor, self.reason = 'ok', None
        elif context_state == 'unsupported':
            self.monitor, self.reason = 'unknown', 'context_version_unsupported'
        else:
            self.monitor, self.reason = 'unknown', 'no_context'
        ctx = context if context_state == 'ok' else None
        self.ctx = ctx
        if self.paused:
            self._pause_all(now, elapsed)
        else:
            self._sensors(now, elapsed, samples)
            self._circuits(now, elapsed, ctx, local_minutes)
            self._frost(now, elapsed)
            self._aggregate(now, elapsed)
        self._judge(now, elapsed, ctx)
        self.findings = self._findings(now, ctx)
        return self.findings

    def _pause_all(self, now, elapsed):
        for sensor in self.sensors.values():
            for latch in (sensor.missing, sensor.unavailable, sensor.implausible_latch, sensor.battery_latch):
                latch.update(None, None, now, elapsed, 0, CONTEXT_FREE_PAUSE_S)
        for (code, entity), latch in self.latches.items():
            circuit = self.sensors[entity].role
            limit = self._pause_limit(circuit, code)
            latch.update(None, None, now, elapsed, math.inf, limit)
        for circuit in self.circuits.values():
            limit = self._pause_limit(circuit.name, 'circuit')
            circuit.circuit_cold.update(None, None, now, elapsed, math.inf, limit)
            circuit.circuit_warm.update(None, None, now, elapsed, math.inf, limit)
        for entity in list(self.windows):
            self._window_pause(entity, elapsed)
        self.all_unavailable.update(None, None, now, elapsed, math.inf, CONTEXT_FREE_PAUSE_S)

    def _pause_limit(self, role, code):
        if code == 'room_frost' or role == 'haus':
            return CONTEXT_FREE_PAUSE_S
        kind = self._types.get(role)
        return TYPE_CONSTANTS[kind]['pause'] if kind in TYPE_CONSTANTS else CONTEXT_FREE_PAUSE_S

    # -- sensors
    def _sensors(self, now, elapsed, samples):
        for entity, sensor in self.sensors.items():
            sample = samples.get(entity)
            if sample is None:
                # Not read this cycle: pause this sensor's timers.
                for latch in (sensor.missing, sensor.unavailable, sensor.implausible_latch, sensor.battery_latch):
                    latch.update(None, None, now, elapsed, 0, CONTEXT_FREE_PAUSE_S)
                continue
            if sample == 'missing':
                presence, numeric, value, battery, name = 'missing', False, None, None, None
            else:
                presence, numeric, value, battery, name = classify_state(sample)
                if name is not None:
                    sensor.name = name
            sensor.presence = presence
            sensor.numeric = numeric
            sensor.battery = battery
            if presence == 'ok' and isinstance(sample, dict):
                key = sample.get('last_reported') or sample.get('last_updated')
                key = key if isinstance(key, str) else None
                if key is not None and key != sensor.last_key and numeric:
                    sensor.last_key = key
                    stamp = parse_time(key)
                    wall_age = 0.0 if stamp is None or self.wall is None else max(0.0, self.wall - stamp)
                    mono = now - min(wall_age, HISTORY_AGE_S)
                    floor = max(sensor.last_update or -math.inf, sensor.history[-1][0] if sensor.history else -math.inf)
                    mono = min(now, max(mono, floor))
                    sensor.last_update = mono
                    if -30.0 <= value <= 60.0:
                        sensor.ingest(mono, value)
                elif key is not None and key != sensor.last_key:
                    sensor.last_key = key
            sensor.implausible = bool(numeric and not (-30.0 <= value <= 60.0))
            sensor.settle_pending(now)
            sensor.expire(now)
            # sensor_missing: judged only while HA is running (this method).
            sensor.missing.update(presence == 'missing', presence == 'missing', now, elapsed,
                                  MISSING_DWELL_S, CONTEXT_FREE_PAUSE_S)
            unavailable = presence == 'ok' and not numeric
            sensor.unavailable.update(unavailable, unavailable, now, elapsed,
                                      UNAVAILABLE_DWELL_S, CONTEXT_FREE_PAUSE_S)
            # Implausible: finding after 10 min out of range; clears after 10 min in range.
            if sensor.implausible:
                sensor.in_range_since = None
            elif numeric and sensor.in_range_since is None:
                sensor.in_range_since = now
            if sensor.implausible_latch.active:
                if numeric and sensor.in_range_since is not None and now - sensor.in_range_since >= IMPLAUSIBLE_DWELL_S:
                    sensor.implausible_latch.clear()
            else:
                sensor.implausible_latch.update(sensor.implausible if numeric else None, None, now, elapsed,
                                                IMPLAUSIBLE_DWELL_S, CONTEXT_FREE_PAUSE_S)
            level = sensor.battery
            fire = None if level is None else level <= 10.0
            hold = None if level is None else level <= 15.0
            sensor.battery_latch.update(fire, hold, now, elapsed, BATTERY_DWELL_S, CONTEXT_FREE_PAUSE_S)
            # Stale: judged on numeric sensors; an unavailable one has its own row.
            sensor.stale_active = bool(numeric and sensor.last_update is not None
                                       and now - sensor.last_update >= SENSOR_STALE_FINDING_S)

    # -- circuits
    def _circuits(self, now, elapsed, ctx, local_minutes):
        self._types = {}
        for circuit in self.circuits.values():
            kind, _source = self.effective_type(circuit, ctx)
            if kind in TYPE_CONSTANTS:
                circuit.last_kind = kind
            # Pause limits follow the last known type while the type is unknown.
            self._types[circuit.name] = circuit.last_kind
            self._circuit(circuit, kind, now, elapsed, ctx, local_minutes)

    def _circuit(self, circuit, kind, now, elapsed, ctx, local_minutes):
        entities = self.options.circuit_entities(circuit.name)
        cls = self.status_class(ctx, circuit.name)
        source = ctx['circuits'][circuit.name] if ctx else None
        usable_ctx = ctx is not None and ctx['online'] and not ctx['maintenance']
        known = kind in TYPE_CONSTANTS and usable_ctx
        constants = TYPE_CONSTANTS.get(kind, TYPE_CONSTANTS['floor'])
        limit = TYPE_CONSTANTS[circuit.last_kind]['pause'] if circuit.last_kind else CONTEXT_FREE_PAUSE_S
        if kind == 'absent' and entities:
            if circuit.absent_since is None:
                circuit.absent_since = now
        elif ctx is not None or not entities:
            # Only a context that shows the circuit (or removed sensors) clears it.
            circuit.absent_since = None
        trigger = warm_restart = False
        if usable_ctx:
            phase, target, mode = source['phase'], source['target'], source['mode']
            restart = False
            if phase is not None:
                if circuit.last_phase is None and not circuit.seen:
                    restart = True
                elif circuit.last_phase is not None and phase != circuit.last_phase:
                    restart = True
                    trigger = trigger or (circuit.last_phase == 'setback' and phase == 'day')
                circuit.last_phase = phase
            if target is not None:
                if circuit.last_target is None and not circuit.seen:
                    restart = True
                elif circuit.last_target is not None and abs(target - circuit.last_target) >= 0.05:
                    restart = True
                    trigger = trigger or target - circuit.last_target >= 1.0 - 1e-9
                circuit.last_target = target
            if mode is not None:
                if circuit.last_mode is not None and mode != circuit.last_mode:
                    restart = warm_restart = True
                circuit.last_mode = mode
            if cls in (LIMIT_OFF, INACTIVE):
                if circuit.off_since is None:
                    circuit.off_since = now
            if cls == SUPPLYING:
                # Only a known hour without supplying (limit off or inactive)
                # restarts settle and triggers; an outage or an unknown status
                # pauses instead (timers are never reset by the outage itself).
                if circuit.off_since is not None and now - circuit.off_since >= NOT_SUPPLYING_RESTART_S:
                    trigger = restart = warm_restart = True
                circuit.off_since = None
            if not circuit.seen and (phase is not None or target is not None):
                circuit.seen = True
                trigger = False  # unknown history: settle, but never arm
            if restart:
                circuit.settle_start = now
        settling = circuit.settle_start is None or now - circuit.settle_start < constants['settle']
        target = source['target'] if source else None
        day = source['day_target'] if source else None
        if target is not None and day is not None:
            upper = max(target, day)
        elif target is not None and day is None and source['phase'] == 'day':
            upper = target
        else:
            upper = None
        if usable_ctx and upper is not None:
            if circuit.last_upper is None or abs(upper - circuit.last_upper) >= 0.05:
                warm_restart = True
            circuit.last_upper = upper
        if warm_restart:
            circuit.warm_settle_start = now
        warm_settling = circuit.warm_settle_start is None or now - circuit.warm_settle_start < constants['settle']
        pump = source['pump'] if source else None
        flow = source['flow'] if source else None
        outdoor_avg = ctx['outdoor_avg'] if ctx else None
        absent = kind == 'absent'
        night = None if local_minutes is None else (local_minutes >= 21 * 60 or local_minutes < 6 * 60)

        def gate():
            """None: cannot judge; False: rule does not apply; True: evaluate."""
            if absent or not known or cls in (UNKNOWN, TRANSITION):
                return None
            if cls != SUPPLYING:
                return False
            return True

        base = gate()
        values, usable = {}, []
        for entity in entities:
            sensor = self.sensors[entity]
            if sensor.usable(now):
                values[entity] = sensor.value(now)
                usable.append(entity)

        def deficit(entity):
            if entity not in values or target is None:
                return None
            allowance = COOLER_ALLOWANCE if entity in self.options.cooler else 0.0
            return (target - allowance) - values[entity]

        recovery = constants['airing']
        # Heat-up trigger: arm the response windows.
        if trigger and known and not absent:
            circuit.triggers += 1
            for entity in usable:
                sensor = self.sensors[entity]
                d0 = deficit(entity)
                min_rise = max(0.3, 1.5 * sensor.step_size())
                if d0 is not None and d0 >= max(1.0, min_rise + 0.5) - 1e-9:
                    self.windows[entity] = Window((circuit.name, circuit.triggers), now, values[entity])
                else:
                    self.windows.pop(entity, None)
        for entity in entities:
            sensor = self.sensors[entity]
            ok = entity in values
            airing = sensor.airing(now, recovery)
            d = deficit(entity)
            # room_cold
            if base is not True:
                fire = hold = base
            elif settling:
                fire = hold = False
            elif not ok or airing or d is None:
                fire = hold = None
            else:
                fire, hold = d >= 1.5, d >= 1.0
            self.latch('room_cold', entity).update(
                fire, hold, now, elapsed, constants['cold_room'], limit,
                lambda e=entity: {'room': half(values[e]), 'target': half(target)})
            # limit_cold
            if absent or not known or cls in (UNKNOWN, TRANSITION):
                fire = hold = None
            elif cls != LIMIT_OFF:
                fire = hold = False
            elif source['phase'] is None:
                fire = hold = None
            elif source['phase'] != 'day':
                fire = hold = False
            elif not ok or airing or d is None:
                fire = hold = None
            else:
                if outdoor_avg is None:
                    fire = None
                else:
                    fire = outdoor_avg < 10.0 and d >= 2.5
                hold = d >= 2.0
            self.latch('limit_cold', entity).update(
                fire, hold, now, elapsed, constants['limit_cold'], limit,
                lambda e=entity: {'room': half(values[e]), 'target': half(target)})
            warm_allowed = entity not in self.options.no_warm
            # room_warm (night only)
            latch = self.latch('room_warm', entity)
            if not warm_allowed:
                latch.clear()
            else:
                if base is not True:
                    fire = hold = base
                elif warm_settling:
                    fire = hold = False
                elif night is None:
                    fire = hold = None
                elif not night:
                    fire = hold = False
                elif not ok or upper is None:
                    fire = hold = None
                else:
                    r = values[entity]
                    if pump is None or flow is None:
                        fire = None
                    else:
                        fire = pump and flow >= r + constants['warm_flow'] and r >= upper + 2.0
                    if pump is False:
                        self.pump_off.setdefault(entity, now)
                    else:
                        self.pump_off.pop(entity, None)
                    if r < upper + 1.5:
                        hold = False
                    elif pump is None:
                        hold = None
                    elif pump is False:
                        # Clears once the pump has been off for 30 min.
                        hold = False if now - self.pump_off[entity] >= 1800 else None
                    else:
                        hold = True
                latch.update(fire, hold, now, elapsed, constants['warm_night'], limit,
                             lambda e=entity: {'room': half(values[e]), 'target': half(upper)})
            # room_hot (absolute)
            latch = self.latch('room_hot', entity)
            if not warm_allowed:
                latch.clear()
            else:
                if base is None:
                    fire = None
                elif base is False:
                    fire = False
                elif not ok or pump is None:
                    fire = None
                else:
                    fire = pump and values[entity] >= 28.0
                hold = (values[entity] >= 27.0) if ok else None
                latch.update(fire, hold, now, elapsed, constants['hot'], limit,
                             lambda e=entity: {'room': half(values[e])})
            # no_response window and finding
            self._response(circuit, entity, sensor, now, elapsed, base, ok, airing, d,
                           values.get(entity), pump, flow, constants, limit)
        # Circuit-wide cold.
        if base is not True:
            fire = hold = base
        elif settling:
            fire = hold = False
        elif len(usable) < 2 or target is None or any(self.sensors[e].airing(now, recovery) for e in usable):
            fire = hold = None
        else:
            deficits = [deficit(e) for e in usable]
            fire, hold = all(x >= 3.0 for x in deficits), all(x >= 2.5 for x in deficits)
        circuit.circuit_cold.update(
            fire, hold, now, elapsed, constants['cold_circuit'], limit,
            lambda: {'room': half(min(values[e] for e in usable)), 'target': half(target), 'count': len(usable)})
        # Circuit-wide warm.
        warm = [e for e in usable if e not in self.options.no_warm]
        if base is not True:
            fire = hold = base
        elif warm_settling:
            fire = hold = False
        elif len(warm) < 2 or upper is None:
            fire = hold = None
        else:
            middle = median(values[e] for e in warm)
            fire = None if pump is None else (pump and middle >= upper + 2.0)
            hold = middle >= upper + 1.5
        circuit.circuit_warm.update(
            fire, hold, now, elapsed, constants['warm_circuit'], limit,
            lambda: {'room': half(median(values[e] for e in warm)), 'target': half(upper)})
        # Circuit-wide no response: every usable sensor for the same trigger.
        current = circuit.circuit_no_response
        if current is not None:
            trig, _onset, _count, members = current
            if not all(e in self.no_response and self.no_response[e].trigger == trig for e in members):
                circuit.circuit_no_response = None
        if circuit.circuit_no_response is None and len(usable) >= 2:
            triggers = {self.no_response[e].trigger if e in self.no_response else None for e in usable}
            if len(triggers) == 1 and None not in triggers:
                trig = triggers.pop()
                onset = min(self.no_response[e].onset for e in usable)
                circuit.circuit_no_response = (trig, onset, len(usable), tuple(usable))

    def _window_pause(self, entity, elapsed):
        window = self.windows.get(entity)
        if window is None:
            return
        window.paused += elapsed
        role = self.sensors[entity].role
        if window.paused > self._pause_limit(role, 'window'):
            del self.windows[entity]

    def _response(self, circuit, entity, sensor, now, elapsed, base, ok, airing, d, r, pump, flow, constants, limit):
        finding = self.no_response.get(entity)
        if finding is not None:
            if now - finding.onset >= NO_RESPONSE_EXPIRY_S or (d is not None and ok and d < 1.0):
                del self.no_response[entity]
        window = self.windows.get(entity)
        if window is None:
            return
        if base is None or not ok or airing:
            condition = None
        elif base is False:
            condition = False
        elif pump is None or flow is None:
            condition = None
        elif not pump or flow < r + constants['flow_margin']:
            # A starting pump or a still cold flow pauses during the start-up
            # allowance; afterwards it disarms (no heating, no judgement).
            condition = None if now - window.start < constants['startup'] else False
        else:
            condition = True
        if condition is False:
            del self.windows[entity]
            return
        if condition is None:
            window.paused += elapsed
            if window.paused > limit:
                del self.windows[entity]
            return
        window.paused = 0.0
        window.active += elapsed
        window.samples.append((window.active, r))
        if window.active < constants['window']:
            return
        length = constants['window']
        first = median(v for t, v in window.samples if t <= 600)
        last = median(v for t, v in window.samples if t >= length - 600)
        del self.windows[entity]
        min_rise = max(0.3, 1.5 * sensor.step_size())
        if first is None or last is None:
            return
        rise = last - first
        if rise >= min_rise - 1e-9:
            self.no_response.pop(entity, None)
        elif d is not None and d >= 1.0:
            self.no_response[entity] = NoResponse(window.trigger, window.start)

    # -- frost and aggregation (context-free)
    def _frost(self, now, elapsed):
        eligible = list(self.options.hc1 + self.options.hc2) + [e for e in self.options.haus if e in self.options.frost]
        in_frost = []
        for entity in eligible:
            sensor = self.sensors[entity]
            ok = sensor.usable(now)
            r = sensor.value(now) if ok else None
            latch = self.latch('room_frost', entity)
            latch.update(None if r is None else r <= 5.0, None if r is None else r <= 6.0,
                         now, elapsed, FROST_DWELL_S, CONTEXT_FREE_PAUSE_S,
                         lambda value=r: {'room': half(value)})
            if latch.active and ok:
                in_frost.append((entity, r))
        if len(in_frost) >= 2:
            if self.frost is None:
                self.frost = (now, {'room': half(min(r for _e, r in in_frost)), 'count': len(in_frost)})
        else:
            self.frost = None

    def _aggregate(self, now, elapsed):
        sensors = list(self.sensors.values())
        if len(sensors) < 2:
            self.all_unavailable.clear()
            return
        reasons = [s.unusable_reason(now) for s in sensors]
        condition = all(reason in ('missing', 'unavailable', 'stale') for reason in reasons)
        self.all_unavailable.update(condition, condition, now, elapsed, ALL_UNAVAILABLE_DWELL_S, CONTEXT_FREE_PAUSE_S)

    # -- judged (spec 6.1)
    def _judge(self, now, elapsed, ctx):
        for circuit in self.circuits.values():
            kind, _source = self.effective_type(circuit, ctx)
            entities = self.options.circuit_entities(circuit.name)
            if self.paused:
                raw = ('unknown', 'ha')
            elif ctx is None:
                raw = ('unknown', 'context')
            elif kind == 'absent':
                raw = ('unknown', 'absent')
            elif not ctx['online'] or ctx['maintenance']:
                raw = ('unknown', 'context')
            else:
                cls = self.status_class(ctx, circuit.name)
                source = ctx['circuits'][circuit.name]
                if cls in (LIMIT_OFF, INACTIVE):
                    raw = ('inactive', None)
                elif kind is None:
                    raw = ('unknown', 'type')
                elif STATUS_CLASS.get(source['status']) == SUPPLYING and (source['phase'] is None or source['target'] is None):
                    raw = ('unknown', 'phase')
                elif cls != SUPPLYING:
                    raw = ('unknown', 'context')
                elif not any(self.sensors[e].usable(now) for e in entities):
                    raw = ('unknown', 'sensors')
                else:
                    raw = ('yes', None)
            if raw[0] == 'unknown':
                if circuit.unknown_since is None:
                    circuit.unknown_since = now
                # The grace keeps short gaps quiet, but a report whose monitor
                # is unknown (HA, context or version) never says judged yes.
                if (circuit.judged is None or now - circuit.unknown_since >= UNKNOWN_GRACE_S
                        or self.monitor != 'ok'):
                    circuit.judged, circuit.why = raw
            else:
                circuit.unknown_since = None
                circuit.judged, circuit.why = raw
            if circuit.judged == 'unknown' and circuit.why in ('context', 'phase') and elapsed > 0:
                circuit.basis_unknown.append((now, elapsed))
            while circuit.basis_unknown and now - circuit.basis_unknown[0][0] > 7 * 86400:
                circuit.basis_unknown.popleft()

    def basis_unknown_minutes(self, now):
        result = {}
        for name, circuit in self.circuits.items():
            day = sum(seconds for when, seconds in circuit.basis_unknown if now - when <= 86400)
            week = sum(seconds for _when, seconds in circuit.basis_unknown)
            result[name] = {'24h': int(day // 60), '7d': int(week // 60)}
        return result

    # -- report rows
    def _row(self, code, now_held, label=None, circuit=None, room=None, target=None, count=None):
        return {'code': code, 'level': CODES[code][1], 'label': label, 'circuit': circuit,
                'held_s': now_held, 'room': room, 'target': target, 'count': count}

    def _findings(self, now, ctx):
        rows = []
        roles = {}
        for entity, sensor in self.sensors.items():
            roles[entity] = sensor.role if sensor.role in CIRCUITS else None
        absent = set()
        for circuit in self.circuits.values():
            if circuit.absent_since is not None and self.options.circuit_entities(circuit.name):
                absent.add(circuit.name)
                rows.append(self._row('config_circuit_absent', held_seconds(now, circuit.absent_since), circuit=circuit.name))
        for (code, entity), latch in self.latches.items():
            if not latch.active or latch.onset is None:
                continue
            if code != 'room_frost' and roles[entity] in absent:
                continue
            rows.append(self._row(code, latch.held(now), self.labels[entity], roles[entity],
                                  latch.onset.get('room'), latch.onset.get('target')))
        for entity, finding in self.no_response.items():
            if roles[entity] in absent:
                continue
            rows.append(self._row('no_response', held_seconds(now, finding.onset), self.labels[entity], roles[entity]))
        for circuit in self.circuits.values():
            if circuit.name in absent:
                continue
            if circuit.circuit_cold.active:
                onset = circuit.circuit_cold.onset
                rows.append(self._row('circuit_cold', circuit.circuit_cold.held(now), circuit=circuit.name,
                                      room=onset['room'], target=onset['target'], count=onset['count']))
            if circuit.circuit_warm.active:
                onset = circuit.circuit_warm.onset
                rows.append(self._row('circuit_warm', circuit.circuit_warm.held(now), circuit=circuit.name,
                                      room=onset['room'], target=onset['target']))
            if circuit.circuit_no_response is not None:
                _trig, onset, count, _members = circuit.circuit_no_response
                rows.append(self._row('circuit_no_response', held_seconds(now, onset), circuit=circuit.name, count=count))
        if self.frost is not None:
            since, onset = self.frost
            rows.append(self._row('frost', held_seconds(now, since), room=onset['room'], count=onset['count']))
        replaced = self.all_unavailable.active
        if replaced:
            rows.append(self._row('sensors_all_unavailable', self.all_unavailable.held(now), count=len(self.sensors)))
        for entity, sensor in self.sensors.items():
            label, circuit = self.labels[entity], roles[entity]
            if not replaced:
                if sensor.missing.active:
                    rows.append(self._row('sensor_missing', sensor.missing.held(now), label, circuit))
                if sensor.unavailable.active:
                    rows.append(self._row('sensor_unavailable', sensor.unavailable.held(now), label, circuit))
                if sensor.stale_active and sensor.presence == 'ok':
                    rows.append(self._row('sensor_stale', held_seconds(now, sensor.last_update), label, circuit))
            if sensor.implausible_latch.active:
                rows.append(self._row('sensor_implausible', sensor.implausible_latch.held(now), label, circuit))
            if sensor.jumping(now):
                rows.append(self._row('sensor_jumping', held_seconds(now, sensor.jump_first), label, circuit))
            if sensor.stuck(now) and sensor.presence == 'ok' and sensor.numeric:
                rows.append(self._row('sensor_stuck', held_seconds(now, sensor.same_since), label, circuit))
            if sensor.battery_latch.active:
                rows.append(self._row('sensor_battery', sensor.battery_latch.held(now), label, circuit))
        return rows

    # -- report parts
    def circuits_report(self):
        result = {}
        for name, circuit in self.circuits.items():
            kind, source = self.effective_type(circuit, self.ctx)
            result[name] = {'type': kind, 'type_source': source,
                            'sensors': len(self.options.circuit_entities(name)),
                            'judged': circuit.judged or 'unknown',
                            'why': circuit.why if (circuit.judged or 'unknown') == 'unknown' else None}
            if result[name]['judged'] == 'unknown' and result[name]['why'] is None:
                result[name]['why'] = 'context'
        return result

    def sensors_report(self, now):
        usable = unavailable = 0
        for sensor in self.sensors.values():
            reason = sensor.unusable_reason(now)
            if reason is None:
                usable += 1
            elif reason in ('missing', 'unavailable', 'stale'):
                unavailable += 1
        return {'configured': len(self.sensors), 'usable': usable, 'unavailable': unavailable}


# ---------------------------------------------------------------------------
# Report

def sort_key(row):
    check, _level = CODES[row['code']]
    return (0 if row['level'] == 'Störung' else 1, 0 if check == 'Räume' else 1,
            {'hc1': 0, 'hc2': 1}.get(row['circuit'], 2), row['label'] or '', CODE_ORDER[row['code']])


def build_report(*, instance, seq, context_id, mode, monitor, reason, sensors, circuits, findings):
    ordered = sorted(findings, key=sort_key)
    kept, folded = ordered[:MAX_FINDINGS], min(MAX_FOLDED, max(0, len(ordered) - MAX_FINDINGS))
    report = {'schema': REPORT_SCHEMA, 'context_majors': list(CONTEXT_MAJORS), 'app_version': APP_VERSION,
              'instance': instance, 'seq': seq, 'context': context_id, 'mode': mode,
              'monitor': monitor, 'reason': reason, 'sensors': sensors, 'circuits': circuits,
              'findings': kept, 'folded': folded}
    payload = canonical(report)
    if len(payload.encode('utf-8')) > MAX_REPORT_BYTES:
        raise ValueError('report_too_large')
    return report, payload


def invalid_config_parts(options, held_s):
    """Report parts without a valid configuration: one config_invalid row,
    `configured` >= 1 so the row stays visible, every circuit not judged
    (`why` "ha"), and as its sensors the distinct entries of its raw list."""
    circuits = {name: {'type': None, 'type_source': None, 'sensors': options.raw_circuits[index],
                       'judged': 'unknown', 'why': 'ha'}
                for index, name in enumerate(CIRCUITS)}
    sensors = {'configured': options.configured, 'usable': 0, 'unavailable': 0}
    finding = {'code': 'config_invalid', 'level': 'Hinweis', 'label': None, 'circuit': None,
               'held_s': int(min(MAX_HELD, max(0, held_s))), 'room': None, 'target': None, 'count': None}
    return sensors, circuits, [finding]


def identities(findings):
    return frozenset((row['code'], row['circuit'], row['label']) for row in findings)
