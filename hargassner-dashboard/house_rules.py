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

APP_VERSION = '1.6.14'
REPORT_SCHEMA = 'harg-house-report/1'
CONTEXT_SCHEMA_PREFIX = 'harg-house-context/'
# Context major 2 has the keys of 1; an adapter sends it only to an app that
# lists 2 in `context_majors`, and it means the adapter accepts the finding key
# `allowance` (app 1.6.8). Against a major 1 context the report keeps the exact
# 1.6.7 finding keys, so an older adapter never refuses it.
# (No new int constant here: rules_digest() hashes them and would make 1.6.8
# discard the room state saved by 1.6.7.)
CONTEXT_MAJORS = (1, 2)
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
# A no_response finding ends 48 h after the last heating start the room did not
# warm on (daily starts keep it; one day without a failed start does not end it).
NO_RESPONSE_EXPIRY_S = 172800
MEDIAN_WINDOW_S = 900
HISTORY_MAX = 12000
HISTORY_AGE_S = 7 * 86400
NOT_SUPPLYING_RESTART_S = 3600
MAX_ELAPSED_S = 300

# Robustness against normal household behaviour (owner, 2026-11-18): the room
# rules report a persistent heating problem, never one aired, cooled or
# turned-down afternoon. See DOCS.md "What is checked".
# Airing: a drop of AIRING_DROP within 15 min starts an episode. It lasts at
# least the type's 'airing' time after the last drop, then until the room is
# back (within AIRING_BACK of its level before, or less than 1 K below the
# target), at most AIRING_MAX_S. Room rules neither accrue nor count meanwhile.
AIRING_DROP = 1.0
AIRING_BACK = 0.5
AIRING_MAX_S = 43200
# Heating starts: a room is judged only when it starts at least ARM_DEFICIT
# below its target. no_response needs failed starts on NO_RESPONSE_DAYS
# different days (starts at least DAY_GAP_S apart), all within
# NO_RESPONSE_SPAN_S, without a start the room warmed on and without the room
# back within 1 K of its day target in between.
# circuit_no_response needs CIRCUIT_NO_RESPONSE_DAYS such starts of the circuit.
ARM_DEFICIT = 1.5
NO_RESPONSE_DAYS = 3
CIRCUIT_NO_RESPONSE_DAYS = 2
DAY_GAP_S = 72000
NO_RESPONSE_SPAN_S = 345600
# room_cold: the room has been cold (the type's 'cold_room' dwell) and has not
# come back to within 1 K of its day target for ROOM_COLD_PERSIST_S; it ends
# when the room gets there, or COLD_FORGET_S after the last cold dwell (also
# while the context is missing or the circuit is off).
ROOM_COLD_PERSIST_S = 43200
COLD_FORGET_S = 93600
# sensor_jumping: JUMP_EVENTS jumps within 24 h. A step (8 K or more) is one;
# a 3 K excursion that returns is one only when it is faster than JUMP_RATE
# K/min, which room air at a sensor cannot do (an open window can).
JUMP_EVENTS = 3
JUMP_RATE = 1.5

# Per circuit type (spec 6.2).
TYPE_CONSTANTS = {
    'radiator': {'settle': 7200, 'airing': 3600, 'cold_room': 5400, 'cold_circuit': 10800,
                 'window': 3600, 'flow_margin': 8.0, 'warm_flow': 5.0, 'warm_night': 7200,
                 'warm_circuit': 21600, 'hot': 3600, 'limit_cold': 21600, 'pause': 7200,
                 'startup': 1800, 'trend': 3600},
    'floor': {'settle': 28800, 'airing': 10800, 'cold_room': 14400, 'cold_circuit': 10800,
              'window': 21600, 'flow_margin': 3.0, 'warm_flow': 2.0, 'warm_night': 14400,
              'warm_circuit': 43200, 'hot': 3600, 'limit_cold': 43200, 'pause': 21600,
              'startup': 1800, 'cold_day_only': True, 'cold_settle': 14400, 'cold_pause': 86400,
              'trend': 10800},
}
# Radiator circuit_cold follows the same day-phase judging from app 1.6.12 (owner
# decision 2026-11-23), with its own 2 h settle and the same 24 h night hold; from
# 1.6.13 the dwell adds up over the day phases of one local day for both types and
# setback never ends a finding (owner, 2026-11-24; see _circuit).
# Underfloor circuit_cold (owner decision 2026-11-18; designed for split daily
# programmes such as 06:00-09:00 and 15:00-23:00 with setback between). With the general 8 h settle
# and 8 h dwell the check needed one 16 h phase and could never fire. Now it is
# judged only in a day phase, against the day target:
# - cold_settle 4 h after the day phase starts (or the target rises): a floor
#   circuit brings rooms back from setback within this time (a few tenths of a
#   degree per hour); the heat-up never counts, so the 3-hour morning window is
#   never judged at all;
# - cold_circuit 3 h of every usable room at least 3 K below the day target
#   (2.5 K holds), so 4 h + 3 h = 7 h fit into the 8 h evening phase: a circuit
#   that is still that cold 7 h after the day phase began does not heat;
# - never judged during setback; an active finding holds through setback and the
#   next heat-up (pause up to cold_pause 24 h) and is cleared only when the next
#   day phase is judged warm enough, so it does not end and return every night.
# 'startup': after a heat-up trigger the pump may still be starting and the
# flow still cold. During this allowance a pump off or a flow below the margin
# pauses the response window instead of disarming it (W starts counting once
# the circuit really supplies).
# 'trend': a room counts as warming when the median of its last 15 min is at
# least the minimum rise above the median 15 min ending 'trend' earlier. A room
# that warms is not "staying cold" (room_cold, circuit_cold wait meanwhile).
# Warm rules settle on a change of U = max(T, day), the mode or a restart of
# supplying, not on every phase change: U stays the day target across a normal
# day/setback cycle, so the night setback is no reason to stop judging them.
OPTION_TYPES = {'automatisch': None, 'heizkoerper': 'radiator', 'fussboden': 'floor'}
MODES = ('beobachten', 'melden')
COOLER_ALLOWANCE = 2.0
# Per-room allowance (app 1.6.8, owner decision 2026-11-19), the room page's
# choice for a room on a circuit: normal, 2, 4 or 6 K cooler, or frost only.
# Every room with a choice other than normal is also listed in kuehlere_raeume,
# so an older app version (which drops the new keys) still treats it as 2 K
# cooler. The larger allowance wins; a room may be in at most one of the three
# new lists. A frost-only room keeps the frost and sensor checks only, like a
# Weitere-Räume room, but stays on its circuit.
ALLOWANCE_LISTS = (('kuehler_4_raeume', 4.0), ('kuehler_6_raeume', 6.0))
FROST_ONLY_LIST = 'nur_frost_raeume'
FROST_ONLY = 'frost'
# The codes whose text names the room's allowance (VM, context major 2).
ALLOWANCE_CODES = ('room_cold', 'limit_cold')
# Free room settings (app 1.6.9, owner request 2026-11-19): per room an
# allowance of 0 to 10 K on the 0.5 K grid and an influence of 0 to 100 % per
# circuit, in the option `raum_einstellungen` (a list of objects). A room
# without an entry keeps the 1.6.8 lists (allowance 0/2/4/6, frost only = no
# influence) and 100 % on its own circuit. Thresholds (percent of influence):
# - judged: a room is judged against a circuit (no_response, its own cold
#   checks over the sum of both circuits) only from 30 %;
# - member: it counts for the circuit-wide checks of a circuit from 50 %, its
#   cold limits scaled by influence/100 (100 %: the 1.6.8 limits);
# - strong: with two or more rooms of at least 80 % (and at most 2 K
#   allowance) on a circuit, only those decide the circuit-wide checks, so a
#   weakly heated room can neither raise nor hold off a circuit fault.
# Kept in a dict on purpose: house_state.rules_digest() hashes the int/float
# constants, and with default settings the rules are exactly those of 1.6.8,
# so a state saved by 1.6.8 continues. Raise house_state.STATE_SCHEMA if these
# change.
ROOM_SETTINGS = {'key': 'raum_einstellungen', 'allowance_max': 10.0, 'judged': 30, 'member': 50,
                 'strong': 80, 'rise_floor': 0.2}
ROOM_SETTING_KEYS = frozenset(('sensor', 'kuehler', 'einfluss_hk1', 'einfluss_hk2'))
# Codes of a room's own heating checks, silenced while the room is being
# recorded for learning (room_learning.py); frost and sensor checks stay on.
ROOM_HEATING_CODES = frozenset(('room_cold', 'limit_cold', 'room_warm', 'room_hot', 'no_response'))

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
             'kuehlere_raeume', 'ohne_ueberwaermung', 'haus_frost_sensoren',
             'kuehler_4_raeume', 'kuehler_6_raeume', 'nur_frost_raeume')


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
    # The larger allowances of app 1.6.8 (see ALLOWANCE_LISTS).
    cooler4: frozenset = frozenset()
    cooler6: frozenset = frozenset()
    frost_only: frozenset = frozenset()
    # app 1.6.9: ((entity, allowance, influence hc1, influence hc2), ...).
    settings: tuple = ()

    @property
    def entities(self):
        return self.hc1 + self.hc2 + self.haus

    @property
    def configured(self):
        return len(self.entities) if self.valid else self.raw_count

    def circuit_entities(self, circuit):
        return self.hc1 if circuit == 'hc1' else self.hc2

    def setting(self, entity):
        """(allowance, influence hc1, influence hc2) of a 1.6.9 entry, or None."""
        for row in self.settings:
            if row[0] == entity:
                return row[1:]
        return None

    def level(self, entity):
        """The room's 1.6.8 choice: 0.0, 2.0, 4.0, 6.0 or FROST_ONLY (lists only)."""
        if entity in self.frost_only:
            return FROST_ONLY
        if entity in self.cooler6:
            return 6.0
        if entity in self.cooler4:
            return 4.0
        return COOLER_ALLOWANCE if entity in self.cooler else 0.0

    def allowance(self, entity):
        """Kelvin the room may stay below its target (0.0 for a room without
        influence, which no heating rule judges)."""
        row = self.setting(entity)
        if row is not None:
            return row[0] if self.total(entity) > 0 else 0.0
        level = self.level(entity)
        return 0.0 if level == FROST_ONLY else level

    def influence(self, entity, circuit):
        """Percent 0..100 the circuit heats the room."""
        row = self.setting(entity)
        if row is not None:
            return row[1 + CIRCUITS.index(circuit)]
        if entity in self.frost_only:
            return 0
        return 100 if entity in self.circuit_entities(circuit) else 0

    def total(self, entity):
        return min(100, sum(self.influence(entity, circuit) for circuit in CIRCUITS))

    def default_setting(self, entity):
        """True when the room is judged exactly as by app 1.6.8."""
        row = self.setting(entity)
        if row is None:
            return True
        home = 'hc1' if entity in self.hc1 else 'hc2'
        return (row[0] in (0.0, 2.0, 4.0, 6.0) and self.influence(entity, home) == 100
                and self.influence(entity, 'hc2' if home == 'hc1' else 'hc1') == 0)

    def heated(self, circuit):
        """The circuit's own rooms its heating rules judge (some influence)."""
        return tuple(entity for entity in self.circuit_entities(circuit) if self.total(entity) > 0)

    def cross(self, circuit):
        """Rooms of the other circuit that this one also heats (member influence)."""
        other = 'hc2' if circuit == 'hc1' else 'hc1'
        return tuple(entity for entity in self.circuit_entities(other)
                     if self.influence(entity, circuit) >= ROOM_SETTINGS['member'])


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
        wider = [lists[key] for key, _value in ALLOWANCE_LISTS] + [lists[FROST_ONLY_LIST]]
        for key in ('kuehlere_raeume', 'ohne_ueberwaermung') + tuple(key for key, _v in ALLOWANCE_LISTS) + (FROST_ONLY_LIST,):
            if not set(lists[key]) <= circuit:
                raise ValueError
        # One room, one larger choice (4 K, 6 K or frost only).
        chosen = [entity for value in wider for entity in value]
        if len(set(chosen)) != len(chosen):
            raise ValueError
        if not set(lists['haus_frost_sensoren']) <= set(haus):
            raise ValueError
        settings = parse_room_settings(options.get(ROOM_SETTINGS['key'], []), hc1, hc2)
        return HouseOptions(True, mode, tuple(types), hc1, hc2, haus,
                            frozenset(lists['kuehlere_raeume']), frozenset(lists['ohne_ueberwaermung']),
                            frozenset(lists['haus_frost_sensoren']), len(assigned),
                            cooler4=frozenset(lists['kuehler_4_raeume']), cooler6=frozenset(lists['kuehler_6_raeume']),
                            frost_only=frozenset(lists[FROST_ONLY_LIST]), settings=settings)
    except (ValueError, TypeError):
        mode = options.get('raumueberwachung_modus') if isinstance(options, dict) else None
        return HouseOptions(False, mode if mode in MODES else 'beobachten', raw_count=_raw_count(options),
                            raw_circuits=_raw_circuits(options))


def allowance_value(value):
    """A valid allowance as float (0..10 K on the 0.5 K grid), else None."""
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    if not 0 <= value <= ROOM_SETTINGS['allowance_max'] or value * 2 != math.floor(value * 2):
        return None
    return float(value)


def influence_value(value):
    """A valid influence as int (0..100 %), else None (an integral float passes)."""
    if type(value) not in (int, float) or not math.isfinite(value) or value != math.floor(value):
        return None
    return int(value) if 0 <= value <= 100 else None


def parse_room_settings(value, hc1, hc2):
    """The 1.6.9 option `raum_einstellungen`; raises ValueError when invalid.

    Each entry names a room on a circuit, at most once, with its allowance and
    both influences; the room's own circuit has at least the other's influence
    (the room's own checks follow its own circuit's targets)."""
    if not isinstance(value, list) or len(value) > MAX_SENSORS:
        raise ValueError
    rows, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != ROOM_SETTING_KEYS:
            raise ValueError
        entity = entry['sensor']
        if not isinstance(entity, str) or entity in seen or entity not in set(hc1) | set(hc2):
            raise ValueError
        allowance = allowance_value(entry['kuehler'])
        first, second = influence_value(entry['einfluss_hk1']), influence_value(entry['einfluss_hk2'])
        if allowance is None or first is None or second is None:
            raise ValueError
        own, other = (first, second) if entity in hc1 else (second, first)
        if own < other:
            raise ValueError
        seen.add(entity)
        rows.append((entity, allowance, first, second))
    return tuple(rows)


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
    # v2 has the same keys (it only tells that the adapter reads `allowance`).
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
    result = {'major': major, 'context': context, 'online': document['online'], 'maintenance': document['maintenance'],
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
        self.last_accepted_mono = None
        self.pending = None                         # spike candidate (mono, value, previous, previous mono)
        self.spikes = deque()                       # jump events (counted spikes and steps) of the last 24 h
        self.step_until = None
        self.jump_first = self.jump_last = None
        self.airing_drop = None                     # mono of the last fast drop
        self.airing_start = None                    # first drop of the current airing episode
        self.airing_ref = None                      # the level before that episode
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
        if recent and max(recent) - value >= AIRING_DROP - 1e-9:
            if self.airing_start is None:
                self.airing_start, self.airing_ref = mono, max(recent)
            self.airing_drop = mono
        self.history.append((mono, value))
        self.last_accepted = value
        self.last_accepted_mono = mono

    def _occurrence(self, mono):
        if self.jump_last is None or mono - self.jump_last >= EVENT_EXPIRY_S:
            self.jump_first = mono
        self.jump_last = mono

    def _event(self, mono):
        """One jump; JUMP_EVENTS of them within 24 h make the sensor jumping."""
        self.spikes.append(mono)
        while self.spikes and mono - self.spikes[0] > EVENT_EXPIRY_S:
            self.spikes.popleft()
        if len(self.spikes) >= JUMP_EVENTS:
            self._occurrence(mono)

    def ingest(self, mono, value):
        """One distinct numeric in-range update."""
        if self.pending is not None:
            when, candidate, before, before_mono = self.pending
            if mono - when <= SPIKE_WINDOW_S and abs(value - before) <= 1.0 and abs(value - candidate) >= 3.0:
                # Discarded either way; counted only when faster than room air
                # can change (a sparse sensor at an open window is not jumping).
                # The shorter leg: an on-change sensor may have been silent
                # for an hour before a glitch that is gone a minute later.
                self.pending = None
                legs = [mono - when] + ([when - before_mono] if before_mono is not None else [])
                gap = max(60.0, min(legs))
                if abs(candidate - before) * 60.0 / gap >= JUMP_RATE - 1e-9:
                    self._event(when)
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
            self.last_accepted_mono = mono
            self.airing_start = self.airing_ref = None
            self.history.append((mono, value))
            self.step_until = mono + STEP_UNUSABLE_S
            self._event(mono)
        elif abs(value - self.last_accepted) >= 3.0:
            self.pending = (mono, value, self.last_accepted, self.last_accepted_mono)
        else:
            self._accept(mono, value)

    def settle_pending(self, now):
        if self.pending is not None and now - self.pending[0] > SPIKE_WINDOW_S:
            when, candidate = self.pending[0], self.pending[1]
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
        """The short pause after the last fast drop (circuit-wide rules)."""
        return self.airing_drop is not None and now - self.airing_drop < recovery

    def airing_episode(self, now, recovery, recovered):
        """An airing episode (room rules): at least `recovery` after the last fast
        drop, then until the room is back, at most AIRING_MAX_S. `recovered`:
        the room is less than 1 K below its current target. Once over, it stays
        over until the next fast drop."""
        if self.airing_start is None:
            return False
        if self.airing_drop is not None and now - self.airing_drop < recovery and now - self.airing_start < AIRING_MAX_S:
            return True
        value = self.value(now)
        if (recovered or now - self.airing_start >= AIRING_MAX_S
                or (value is not None and value >= self.airing_ref - AIRING_BACK - 1e-9)):
            self.airing_start = self.airing_ref = None
            return False
        return True

    def trend(self, now, span):
        """Median of the last 15 min minus the median of the 15 min ending
        `span` earlier. A window without an update takes the last value before
        its end (an on-change sensor reports only changes); None without one."""
        later, earlier, before_later, before_earlier = [], [], None, None
        for when, value in reversed(self.history):
            if when > now:
                continue
            if when > now - MEDIAN_WINDOW_S:
                later.append(value)
                continue
            if before_later is None:
                before_later = value
            if when > now - span:
                continue
            if when > now - span - MEDIAN_WINDOW_S:
                earlier.append(value)
                continue
            before_earlier = value
            break
        later = later or ([before_later] if before_later is not None else [])
        earlier = earlier or ([before_earlier] if before_earlier is not None else [])
        if not later or not earlier:
            return None
        return median(later) - median(earlier)

    def min_rise(self):
        """The smallest rise that counts as warming, for this sensor's resolution."""
        return max(0.3, 1.5 * self.step_size())

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
    """A no_response finding. last: start of the latest heating start without effect."""
    __slots__ = ('trigger', 'onset', 'last')

    def __init__(self, trigger, onset, last=None):
        self.trigger, self.onset = trigger, onset
        self.last = onset if last is None else last


def expected_rise(sensor, weight):
    """The rise a heating start must bring: proportional to the room's
    influence (100 %: the 1.6.8 rise), never below the noise floor."""
    if weight >= 100:
        return sensor.min_rise()
    return max(ROOM_SETTINGS['rise_floor'], 1.5 * sensor.step_size(), sensor.min_rise() * weight / 100.0)


def distinct_days(times):
    """How many of the (sorted) start times lie at least DAY_GAP_S apart."""
    count, anchor = 0, None
    for when in times:
        if anchor is None or when - anchor >= DAY_GAP_S:
            count, anchor = count + 1, when
    return count


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
        self.no_response_last = None      # start of the circuit's latest heating start without effect
        self.starts = {}                  # trigger -> {'start', 'armed', 'fail', 'pass'} until resolved
        self.failed_starts = []           # start times of heating starts without effect in any room
        self.failed_rooms = set()         # the rooms that failed those starts
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
        self.response_fails = {}  # entity -> start times of heating starts without effect
        self.cold_dwell = {}    # entity -> Latch: the room_cold dwell (one cold occasion)
        self.cold_since = {}    # entity -> first cold occasion since the room last reached its day level
        self.cold_seen = {}     # entity -> last time a cold occasion was active
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
        # Rooms being recorded for learning (room_learning.py): their own
        # heating findings are not reported; every rule still runs.
        self.quiet = frozenset()

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
        for entity in [e for e, f in self.no_response.items() if now - f.last >= NO_RESPONSE_EXPIRY_S]:
            del self.no_response[entity]
        for circuit in self.circuits.values():
            current = circuit.circuit_no_response
            if current is not None:
                last = current[1] if circuit.no_response_last is None else circuit.no_response_last
                if now - last >= NO_RESPONSE_EXPIRY_S:
                    circuit.circuit_no_response = None
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
                latch.update(None, None, now, elapsed, math.inf, CONTEXT_FREE_PAUSE_S)  # a pause never completes a dwell
        for (code, entity), latch in self.latches.items():
            circuit = self.sensors[entity].role
            limit = self._pause_limit(circuit, code)
            if code == 'limit_cold' and latch.active:
                limit = TYPE_CONSTANTS['floor']['cold_pause']   # held through a night (see _circuit)
            latch.update(None, None, now, elapsed, math.inf, limit)
        for entity, latch in self.cold_dwell.items():
            latch.update(None, None, now, elapsed, math.inf, self._pause_limit(self.sensors[entity].role, 'room_cold'))
        for circuit in self.circuits.values():
            limit = self._pause_limit(circuit.name, 'circuit')
            # circuit_cold holds through a night (see _circuit): HA or the app
            # being away for a moment must not end it after hours of setback.
            hold_limit = TYPE_CONSTANTS['floor']['cold_pause'] if circuit.circuit_cold.active else limit
            circuit.circuit_cold.update(None, None, now, elapsed, math.inf, hold_limit)
            circuit.circuit_warm.update(None, None, now, elapsed, math.inf, limit)
        for entity in list(self.windows):
            self._window_pause(entity, elapsed, now)
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
                    latch.update(None, None, now, elapsed, math.inf, CONTEXT_FREE_PAUSE_S)  # a pause never completes a dwell
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
        # Frost-only rooms (room page) are left out of every heating rule of
        # the room and of the circuit (room_cold, limit_cold, room_warm,
        # room_hot, no_response, circuit_cold, circuit_warm,
        # circuit_no_response): they neither raise nor hold off a circuit
        # finding. Their frost and sensor checks run elsewhere.
        entities = self.options.heated(circuit.name)
        # Rooms of the other circuit this one heats too (1.6.9 influence): they
        # count for this circuit's circuit-wide checks only; their own checks
        # follow their own circuit.
        cross = self.options.cross(circuit.name)
        options = self.options
        weight = {entity: options.influence(entity, circuit.name) for entity in entities + cross}
        values, usable = {}, []
        for entity in entities + cross:
            sensor = self.sensors[entity]
            if sensor.usable(now):
                values[entity] = sensor.value(now)
                usable.append(entity)

        def deficit(entity):
            if entity not in values or target is None:
                return None
            return (target - self.options.allowance(entity)) - values[entity]

        recovery = constants['airing']
        # Underfloor rules by the last known type (an unknown type falls back
        # to the floor constants above, which must not change a radiator's pause).
        floor_rules = TYPE_CONSTANTS.get(kind if kind in TYPE_CONSTANTS else circuit.last_kind, {})
        day_only = bool(floor_rules.get('cold_day_only'))
        cold_settling = day_only and (circuit.settle_start is None
                                      or now - circuit.settle_start < floor_rules['cold_settle'])
        # Per room this step: in an airing episode, warming, back at its day level.
        airing, warming, reached = {}, {}, {}
        for entity in entities:
            sensor = self.sensors[entity]
            d = deficit(entity)
            airing[entity] = sensor.airing_episode(now, recovery, d is not None and d < 1.0)
            rise = sensor.trend(now, constants['trend']) if entity in values else None
            warming[entity] = rise is not None and rise >= sensor.min_rise() - 1e-9
            reached[entity] = (usable_ctx and entity in values and upper is not None
                               and values[entity] >= upper - self.options.allowance(entity) - 1.0 - 1e-9)
        for entity in cross:
            rise = self.sensors[entity].trend(now, constants['trend']) if entity in values else None
            warming[entity] = rise is not None and rise >= self.sensors[entity].min_rise() - 1e-9
        # The rooms that decide the circuit-wide checks (see ROOM_SETTINGS and
        # the 1.6.8 reference rooms): rooms of at least `member` influence; when
        # two or more of them have at least `strong` influence and at most 2 K
        # allowance, only those. A sensor that drops out pauses, never switches the set.
        members = [e for e in entities + cross if weight[e] >= ROOM_SETTINGS['member']]
        reference = [e for e in members if options.allowance(e) <= COOLER_ALLOWANCE
                     and weight[e] >= ROOM_SETTINGS['strong']]
        deciding = reference if len(reference) >= 2 else members
        # A room the other circuit heats too (judged influence there) may stay
        # warm while this circuit fails: it must never hold off this circuit's
        # fault or end its failed starts. With two or more rooms only this
        # circuit heats, only those decide (independent review 2026-11-19).
        other = 'hc2' if circuit.name == 'hc1' else 'hc1'
        alone = {e for e in entities + cross if options.influence(e, other) < ROOM_SETTINGS['judged']}

        def only_this(rooms):
            mine = [e for e in rooms if e in alone]
            return mine if len(mine) >= 2 else list(rooms)
        deciding = only_this(deciding)
        # Heating starts of the circuit (circuit_no_response): by influence
        # only, as 1.6.8 counted every room (allowances do not matter there).
        strong = [e for e in entities if weight[e] >= ROOM_SETTINGS['strong']]
        booking = set(only_this(strong if len(strong) >= 2
                                else [e for e in entities if weight[e] >= ROOM_SETTINGS['member']]))
        for key in [k for k, start in circuit.starts.items() if now - start['start'] > NO_RESPONSE_SPAN_S]:
            del circuit.starts[key]
        # Heat-up trigger: arm the response windows (never in an airing episode).
        if trigger and known and not absent:
            circuit.triggers += 1
            key = (circuit.name, circuit.triggers)
            circuit.starts[key] = {'start': now, 'armed': set(), 'fail': set(), 'pass': set(),
                                   'usable': len([e for e in usable if e in booking])}
            for entity in usable:
                if entity not in entities:
                    continue    # a cross room's windows follow its own circuit
                self._close(circuit, entity, self.windows.pop(entity, None), None, now)
                sensor = self.sensors[entity]
                d0 = deficit(entity)
                if weight[entity] < ROOM_SETTINGS['judged']:
                    continue    # not expected to warm on this circuit's start
                if (d0 is not None and not airing[entity]
                        and d0 >= max(ARM_DEFICIT, sensor.min_rise() + 0.5) - 1e-9):
                    self.windows[entity] = Window(key, now, values[entity])
                    # Only deciding rooms book into the circuit's start: a
                    # weakly heated room must not clear a circuit failure.
                    if entity in booking:
                        circuit.starts[key]['armed'].add(entity)
            if not circuit.starts[key]['armed']:
                del circuit.starts[key]
        for entity in entities:
            sensor = self.sensors[entity]
            ok = entity in values
            aired = airing[entity]
            d = deficit(entity)
            # room_cold: one cold occasion is the type's dwell of a room 1.5 K
            # below its target that does not warm; the finding needs the room
            # to stay away from its day level for ROOM_COLD_PERSIST_S.
            # Underfloor (cold_day_only, as circuit_cold): judged only in a day
            # phase after cold_settle; setback and heat-up pause the dwell (up
            # to cold_pause), so it can add up over the evening phases of a
            # programme like 06:00-09:00 and 15:00-23:00 instead of never
            # completing behind an 8 h settle.
            if day_only and base is not None and source['phase'] != 'day':
                fire = hold = None
            elif base is not True:
                fire = hold = base
            elif day_only and cold_settling:
                fire = hold = None
            elif settling and not day_only:
                fire = hold = False
            elif options.total(entity) < ROOM_SETTINGS['judged']:
                fire = hold = False     # weakly heated: frost and sensor checks only
            elif not ok or aired or d is None:
                fire = hold = None
            else:
                fire, hold = d >= 1.5 and not warming[entity], d >= 1.0
            dwell = self.cold_dwell.setdefault(entity, Latch())
            dwell.update(fire, hold, now, elapsed, constants['cold_room'],
                         floor_rules['cold_pause'] if day_only else limit)
            self._room_cold(entity, dwell, base, reached[entity], now, values.get(entity), target)
            # limit_cold: judged in the day phase only. During setback no new
            # notice starts and an existing one holds through the night (up to
            # 24 h), so it no longer ends every evening and needs another 6 h
            # (12 h underfloor) the next day (review 2026-11-23).
            limit_latch = self.latch('limit_cold', entity)
            limit_pause = TYPE_CONSTANTS['floor']['cold_pause'] if limit_latch.active else limit
            if absent or not known or cls in (UNKNOWN, TRANSITION):
                fire = hold = None
            elif source['phase'] is not None and source['phase'] != 'day':
                fire, hold = False, None
            elif cls != LIMIT_OFF:
                fire = hold = False
            elif source['phase'] is None:
                fire = hold = None
            elif options.total(entity) < ROOM_SETTINGS['judged']:
                fire = hold = False
            elif not ok or aired or d is None:
                fire = hold = None
            else:
                if outdoor_avg is None:
                    fire = None
                else:
                    fire = outdoor_avg < 10.0 and d >= 2.5
                hold = d >= 2.0
            limit_latch.update(
                fire, hold, now, elapsed, constants['limit_cold'], limit_pause,
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
            self._response(circuit, entity, sensor, now, elapsed, base, ok, aired, d,
                           values.get(entity), pump, flow, constants, limit, weight[entity])
        # Circuit-wide cold, over the circuit's reference rooms: those with
        # "normal" or 2 K (as up to 1.6.7). A room with 4 or 6 K is one the
        # owner keeps unusually cool (a sunroom, a stove): it neither raises
        # the Störung at its allowed temperature nor holds it off while sun or
        # a stove keeps it warm. Only a circuit with fewer than two reference
        # rooms configured judges those rooms, each against its own limit; a
        # reference sensor that drops out pauses the check (fewer than two
        # usable), it never switches the set. Frost-only rooms never count
        # (see `heated`).
        judged = [e for e in usable if e in deciding]

        def scaled(limit_k, entity):
            # A room the circuit heats only partly falls less when it fails.
            return limit_k * weight[entity] / 100.0

        def cold(entities_, fire_k, hold_k):
            deficits = [(deficit(e), e) for e in entities_]
            return (all(x >= scaled(fire_k, e) for x, e in deficits),
                    all(x >= scaled(hold_k, e) for x, e in deficits))
        # circuit_cold, both circuit types (owner decisions 2026-11-18 underfloor,
        # 2026-11-23 radiators, 2026-11-24 app 1.6.13). Judged in a day phase only,
        # against the day target, after that phase's settle (radiator 2 h,
        # underfloor 4 h): every usable reference room 3 K below (2.5 K holds).
        # - Setback (whatever the status: heating, heating limit or inactive) and
        #   each day phase's settle are not judged: no new finding; an existing one
        #   holds until a later day phase decides, at most 24 h (the floor
        #   cold_pause bound for both types, so the rule constants and the saved
        #   state stay as they are).
        # - The 3 h dwell towards a new finding adds up over the day phases of one
        #   local day: setback and the next phase's settle pause it instead of
        #   resetting it; a judged day phase that is not that cold resets it, and
        #   a dwell not yet complete is reset at local midnight (without a known
        #   local time after the 24 h bound). Warming, airing or too few usable
        #   rooms inside a day phase pause it with the type's own pause, as before.
        # - Unknown basis (VM offline or in maintenance, status 2, unknown phase or
        #   type): never judged; an existing finding holds up to 24 h.
        big = TYPE_CONSTANTS['floor']['cold_pause']
        floor_branch = bool(constants.get('cold_day_only'))
        cold_limit = limit
        phase_known = bool(source) and source['phase'] is not None
        day_phase = phase_known and source['phase'] == 'day'
        if base is None:
            fire = hold = None
            if circuit.circuit_cold.active:
                cold_limit = big
        elif phase_known and not day_phase:
            fire = hold = None              # setback, heating or switched off: not judged, a finding holds
            cold_limit = big
        elif base is False:
            fire = hold = False             # switched off in a day phase (or with no known phase): rule does not apply
        elif not day_phase:
            fire = hold = None              # no known phase: never judged
            if floor_branch or circuit.circuit_cold.active:
                cold_limit = big
        elif cold_settling if floor_branch else settling:
            fire = hold = None              # this day phase's settle (or a dial tap, mode change): paused
            cold_limit = big
        else:
            if floor_branch or circuit.circuit_cold.active:
                cold_limit = big
            if (len(judged) < 2 or target is None or source.get('day_target') is None
                    or any(self.sensors[e].airing(now, recovery) for e in judged)):
                fire = hold = None
            else:
                fire, hold = cold(judged, 3.0, 2.5)   # target is the day target in the day phase
                if fire and any(warming[e] for e in judged):
                    fire = None  # still warming (after a holiday, a cold morning): wait, a working circuit
        if (local_minutes is not None and local_minutes * 60 < max(elapsed, 1)
                and not circuit.circuit_cold.active):
            circuit.circuit_cold.timer.reset()    # a dwell adds up over one local day only
        circuit.circuit_cold.update(
            fire, hold, now, elapsed, constants['cold_circuit'], cold_limit,
            # The room furthest below its own limit (with allowances the lowest
            # value need not be the coldest).
            lambda: {'room': half(values[max(judged, key=lambda e: (deficit(e) * 100.0 / max(1, weight[e]), -values[e]))]),
                     'target': half(target), 'count': len(judged)})
        # Circuit-wide warm.
        warm = [e for e in usable if e not in self.options.no_warm and weight[e] >= ROOM_SETTINGS['member']]
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
        # A room back at its day level heats well enough: its failed starts
        # (a door open every morning, a slow start) and its finding are over.
        for entity in entities:
            if reached[entity]:
                self.response_fails.pop(entity, None)
                self.no_response.pop(entity, None)
        # The circuit's: once every room that failed is back at its day level.
        if circuit.failed_rooms and all(reached.get(e) for e in circuit.failed_rooms):
            circuit.failed_starts, circuit.failed_rooms = [], set()
            circuit.circuit_no_response = None

    def _room_cold(self, entity, dwell, base, reached, now, value, target):
        """The room_cold finding from its dwell latch (see ROOM_COLD_PERSIST_S)."""
        if dwell.active:
            self.cold_since.setdefault(entity, now)
            self.cold_seen[entity] = now
        if reached or (entity in self.cold_seen and now - self.cold_seen[entity] > COLD_FORGET_S):
            self.cold_since.pop(entity, None)
            self.cold_seen.pop(entity, None)
        latch = self.latch('room_cold', entity)
        if latch.active:
            # Only the room itself ends it (back at its day level), or no cold
            # spell for COLD_FORGET_S: a night off (heating limit) does not.
            if entity not in self.cold_since:
                latch.clear()
            else:
                latch.timer.paused = 0.0
        elif (dwell.active and entity in self.cold_since and now - self.cold_since[entity] >= ROOM_COLD_PERSIST_S
              and value is not None and target is not None):
            latch.timer.reset()
            latch.timer.since = now
            latch.active, latch.onset = True, {'room': half(value), 'target': half(target)}

    def _window_pause(self, entity, elapsed, now):
        window = self.windows.get(entity)
        if window is None:
            return
        window.paused += elapsed
        role = self.sensors[entity].role
        if window.paused > self._pause_limit(role, 'window'):
            del self.windows[entity]
            self._close(self.circuits.get(role), entity, window, None, now)

    def _response(self, circuit, entity, sensor, now, elapsed, base, ok, aired, d, r, pump, flow,
                  constants, limit, weight=100):
        window = self.windows.get(entity)
        if window is None:
            return
        if aired:
            # An airing in the window: this heating start does not count.
            del self.windows[entity]
            self._close(circuit, entity, window, None, now)
            return
        if base is None or not ok:
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
            self._close(circuit, entity, window, None, now)
            return
        if condition is None:
            window.paused += elapsed
            if window.paused > limit:
                del self.windows[entity]
                self._close(circuit, entity, window, None, now)
            return
        window.paused = 0.0
        window.active += elapsed
        window.samples.append((window.active, r))
        if window.active < constants['window']:
            return
        # The trend over the window: median of its last quarter minus median of
        # its first quarter (at least 10 min each), robust to noise and dips.
        length = constants['window']
        quarter = max(600.0, length / 4)
        first = median([v for t, v in window.samples if t <= quarter])
        last = median([v for t, v in window.samples if t >= length - quarter])
        del self.windows[entity]
        if first is None or last is None:
            outcome = None
        elif last - first >= expected_rise(sensor, weight) - 1e-9:
            outcome = 'pass'
        elif d is not None and d >= 1.0:
            outcome = 'fail'
        else:
            outcome = None     # still short, but near its target: not "not responding"
        self._close(circuit, entity, window, outcome, now)

    def _close(self, circuit, entity, window, outcome, now):
        """Book one ended response window: 'pass', 'fail' or None (does not count)."""
        if window is None:
            return
        fails = self.response_fails.setdefault(entity, [])
        if outcome == 'pass':
            fails.clear()
            self.no_response.pop(entity, None)
        elif outcome == 'fail':
            fails.append(window.start)
            fails[:] = [when for when in fails if now - when <= NO_RESPONSE_SPAN_S]
            if distinct_days(fails) >= NO_RESPONSE_DAYS:
                finding = self.no_response.get(entity)
                if finding is None:
                    self.no_response[entity] = NoResponse(window.trigger, now, window.start)
                else:
                    finding.trigger, finding.last = window.trigger, window.start
        start = circuit.starts.get(window.trigger) if circuit is not None else None
        if start is None or entity not in start['armed']:
            return      # not booked into the circuit's start (weak influence)
        start['armed'].discard(entity)
        if outcome is not None:
            start[outcome].add(entity)
        if start['armed']:
            return
        # Every room armed at this start has ended: judge the circuit's start.
        del circuit.starts[window.trigger]
        if start['pass']:
            circuit.failed_starts, circuit.failed_rooms = [], set()
            circuit.circuit_no_response = None
        elif len(start['fail']) >= 2 and 2 * len(start['fail']) >= start['usable']:
            circuit.failed_starts.append(start['start'])
            circuit.failed_rooms |= start['fail']
            circuit.failed_starts = [when for when in circuit.failed_starts if now - when <= NO_RESPONSE_SPAN_S]
            if distinct_days(circuit.failed_starts) >= CIRCUIT_NO_RESPONSE_DAYS:
                members = tuple(e for e in self.options.circuit_entities(circuit.name) if e in start['fail'])
                current = circuit.circuit_no_response
                if current is None:
                    circuit.circuit_no_response = (window.trigger, now, len(members), members)
                else:
                    # Onset and count stay frozen while the finding lasts.
                    circuit.circuit_no_response = (window.trigger, current[1], current[2], members)
                circuit.no_response_last = start['start']

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
            if latch.active:
                # A room in frost stays counted while its sensor is unusable: its
                # latch only pauses (at most CONTEXT_FREE_PAUSE_S), so must the
                # fault, or a sensor that drops out in the cold ends it.
                in_frost.append((entity, r if ok else latch.onset['room']))
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
            if code in ROOM_HEATING_CODES and entity in self.quiet:
                continue
            row = self._row(code, latch.held(now), self.labels[entity], roles[entity],
                            latch.onset.get('room'), latch.onset.get('target'))
            allowance = self.options.allowance(entity)
            if code in ALLOWANCE_CODES and allowance > 0 and row['target'] is not None:
                # Reported only against a context major 2 (build_report).
                row['allowance'] = allowance
            rows.append(row)
        for entity, finding in self.no_response.items():
            if roles[entity] in absent or entity in self.quiet:
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


def wire_finding(row, context_major):
    """One finding as sent: with `allowance` (null unless set) for an adapter
    whose context is major 2 or later, else exactly the major 1 keys."""
    base = {key: value for key, value in row.items() if key != 'allowance'}
    if context_major is not None and context_major >= 2:
        allowance = row.get('allowance')
        base['allowance'] = allowance if row['code'] in ALLOWANCE_CODES and number(allowance) else None
    return base


def build_report(*, instance, seq, context_id, mode, monitor, reason, sensors, circuits, findings,
                 context_major=None):
    ordered = [wire_finding(row, context_major if context_id is not None else None)
               for row in sorted(findings, key=sort_key)]
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
