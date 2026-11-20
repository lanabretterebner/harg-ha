"""Room learning (app 1.6.9, sources from 1.6.10): a proposal for one room's
settings, started by the owner only, never applied without the owner's
acceptance.

"Raum anlernen" on the room page reads the past of the room and of its heating
circuits and proposes the room's allowance and its influence per circuit, with
the evidence. From 1.6.10 the data come from where they are kept longest:

- Heating circuits (phase Tag/Absenken, target, status code): the VM's own
  history, `GET /api/dashboard/v1/circuit-history` (5-minute channel rows and
  the parameter journal, kept 30 days), read through the app's Ingress
  credential as the administrator who pressed the button (an ordinary
  dashboard session, read-only). Older days, or every day when the VM cannot
  be read (an older VM release, refused, unreachable), from Home Assistant's
  recorder history of the VM's climate entities, as in 1.6.9.
- Room temperature: Home Assistant's long-term statistics (hourly mean, min
  and max, kept indefinitely for sensors with `state_class: measurement`)
  through the WebSocket command `recorder/statistics_during_period` for the
  allowance, and the recorder history (every change, kept `purge_keep_days`,
  10 days by default) for the heat-up response, where the finer resolution
  matters. A sensor without long-term statistics falls back to the recorder
  history for both, and the evidence says so.

When nothing older is kept, the owner can record from now on instead: the
room's own heating checks pause for the recording time (frost and sensor
checks stay on), and at its end the same analysis runs over the recorded
period.

Learning sees what the room did, not what it should do: a room whose radiator
fails would be learned as a cold room. That is why it is manual, visible and
reviewable, and why a room that never reaches its target or hardly warms on a
heating start carries a warning that needs an explicit "geprüft" to accept.

Requests: Home Assistant (LearningApi.allowed) GET /core/api/states (to find
the circuits' climate entities) and GET /core/api/history/period/<start>?
filter_entity_id=<room>[,<climate>...]&end_time=<end> in CHUNK_S pieces of at
most CHUNK_CAP bytes, newest first, stopping where the recorder has nothing
more; one WebSocket connection for the statistics (ha_websocket.py); the VM
(VmHistory) one GET of at most VM_CAP bytes. Nothing is written to Home
Assistant or the VM here; the room page writes accepted values to the app's
own options.
"""
from bisect import bisect_right
from datetime import datetime, timezone
import http.client
import json
import math
import os
import re
import ssl
import stat
import tempfile
import threading
import time
from urllib.parse import quote

import ha_websocket
import house_rules as rules
from house_monitor import ApiError, CoreApi, STATES_PATH

LEARNING_PATH = '/data/room_learning.json'
LEARNING_SCHEMA = 'harg-room-learning/1'
LEARNING_MAX_BYTES = 262144
HISTORY_PATH = '/core/api/history/period/'
# Periods offered (days); 0 is "alles Verfügbare", up to ALL_DAYS.
PERIODS = (3, 7, 14, 30, 90)
ALL_DAYS = 365
MAX_DAYS = ALL_DAYS
RECORD_PERIODS = (3, 7, 14)
# The VM keeps its circuit history 30 days (CHANNEL_HISTORY_RETENTION); one
# request covers it. The recorder history (room changes, climate fallback) is
# read for at most HA_HISTORY_DAYS, newest first, and stops after
# EMPTY_STOP pieces in a row without any row (the recorder purged them).
VM_DAYS = 30
VM_PATH = '/api/dashboard/v1/circuit-history'
VM_CAP = 2 * 1024 * 1024
VM_TIMEOUT_S = 15
HA_HISTORY_DAYS = 90
FINE_DAYS = 30
EMPTY_STOP = 2
# Documented HKZustand codes (STATE-CODES): 1 Heizen is day; 2 Absenken
# Rampe, 3 Absenken and 4 Tag/Nacht Abschaltung are setback, as the VM's
# commissioned phase mapping selects them for the climate entity (review M1:
# a morning 4 -> 1 is a heating start); others give no phase.
PHASE_BY_CODE = {1: 'day', 2: 'setback', 3: 'setback', 4: 'setback'}
CHUNK_S = 43200
CHUNK_CAP = 4 * 1024 * 1024
MAX_ROWS = 20000
# A chunk that fails (timeout, too large, refused) is a gap; more than this
# share of failed chunks stops the analysis instead of guessing.
MAX_FAILED_SHARE = 0.25
STEP_S = 600
ROOM_GAP_S = 10800
SUPPLYING_CODES = frozenset(code for code, cls in rules.STATUS_CLASS.items() if cls == rules.SUPPLYING)
# Day level: day-phase samples after the circuit's settle time; a day counts
# with at least two hours of them. The allowance is the 80th percentile of
# the daily deficits minus 1 K (room_cold fires 1.5 K below the limit), on
# the 0.5 K grid, 0 to 10 K.
SETTLE_S = {'radiator': 7200, 'floor': 14400}
DAY_SAMPLES_MIN = 12
DAYS_MIN = 3
# Heat-up: a setback-to-day change or a target rise of 1 K of a supplying
# circuit with the room at least 1 K below the new target; the room's rise
# over the window (median of the last 15 min minus median of the first). A
# start of the other circuit within SHARED_S makes it a shared start that
# says nothing about either circuit alone. A circuit's influence needs
# EVENTS_MIN of its own starts: 100 % at the reference rate.
WINDOW_S = {'radiator': 7200, 'floor': 21600}
REFERENCE_RATE = {'radiator': 0.8, 'floor': 0.25}
EVENT_DEFICIT = 1.0
SHARED_S = 3600
EVENTS_MIN = 3
WEAK_INFLUENCE = 50
PROPOSAL_MAX_AGE_S = 7 * 86400
CORRECTION_ID = re.compile(r'[a-z0-9_]{0,200}_number_heizkreis_([12])_raumkorrektur')
STAMP = r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}%2B00:00'
HISTORY_QUERY = re.compile(r'/core/api/history/period/(' + STAMP + r')\?filter_entity_id=([a-z0-9_.,]{1,700})'
                           r'&end_time=(' + STAMP + r')')


def stamp(wall):
    return quote(datetime.fromtimestamp(wall, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00'), safe=':')


class LearningApi(CoreApi):
    """The states list and history of the allowed entities; nothing else."""

    def __init__(self, token, entities, *args, **kwargs):
        super().__init__(token, (), *args, **kwargs)
        self.history_entities = frozenset(entities)

    def allowed(self, method, path, body):
        if method != 'GET' or body is not None:
            return False
        if path == STATES_PATH:
            return True
        match = HISTORY_QUERY.fullmatch(path)
        if match is None:
            return False
        names = match.group(2).split(',')
        return (0 < len(names) <= 3 and len(set(names)) == len(names)
                and all(name in self.history_entities for name in names))

    def history(self, entities, start, end):
        path = (HISTORY_PATH + stamp(start) + '?filter_entity_id=' + ','.join(entities)
                + '&end_time=' + stamp(end))
        status, raw = self.request('GET', path, cap=CHUNK_CAP)
        if status != 200:
            raise ApiError('status')
        try:
            return rules.strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ApiError('json') from None


def find_climates(states):
    """circuit -> climate entity of the VM's heating circuit, from the states list.

    Found by the attributes the VM publishes (control schema and the unique id
    of the circuit's room correction), never by a name the owner may change."""
    found = {name: [] for name in rules.CIRCUITS}
    for state in states if isinstance(states, list) else ():
        if not isinstance(state, dict) or not isinstance(state.get('entity_id'), str):
            continue
        entity = state['entity_id']
        attributes = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        if not re.fullmatch(r'climate\.[a-z0-9_]{1,200}', entity):
            continue
        if attributes.get('hargassner_control_schema') != 'configured_room_setpoint_v1':
            continue
        match = CORRECTION_ID.fullmatch(str(attributes.get('hargassner_correction_id') or ''))
        if match:
            found['hc' + match.group(1)].append(entity)
    return {name: entities[0] for name, entities in found.items() if len(entities) == 1}


class VmError(Exception):
    """Fixed category in args[0]: unsupported, refused, unreachable, response, no_identity."""


VM_ERROR_TEXT = {
    'unsupported': 'die VM bietet diese Abfrage noch nicht an (ältere VM-Version)',
    'refused': 'die VM hat den Zugriff abgelehnt',
    'unreachable': 'die VM war nicht erreichbar',
    'response': 'die Antwort der VM war ungültig',
    'no_identity': 'ohne Anmeldung über die Raumseite nicht abrufbar',
    'not_configured': 'keine Verbindung zur VM eingerichtet',
    'empty': 'die VM hat für diesen Zeitraum keine Heizkreisdaten mit Zieltemperatur',
}
INGRESS_USER = re.compile(r'[A-Za-z0-9_-]{1,128}')
INGRESS_PREFIX = re.compile(r'/api/hassio_ingress/[A-Za-z0-9_-]{16,256}')


class VmHistory:
    """One read-only GET of the VM's circuit history, as the HA user who
    started the learning (the Ingress credential the panel already uses: an
    ordinary dashboard session). No other path, no body, no write."""

    def __init__(self, settings, *, connection=None, context=None, timeout=VM_TIMEOUT_S):
        self.settings = settings
        self.connection = connection
        self.context = context
        self.timeout = timeout

    def read(self, identity, start, end):
        if (not isinstance(identity, tuple) or len(identity) != 2 or not isinstance(identity[0], str)
                or not isinstance(identity[1], str) or not INGRESS_USER.fullmatch(identity[0])
                or not INGRESS_PREFIX.fullmatch(identity[1].rstrip('/'))):
            raise VmError('no_identity')
        user, prefix = identity
        start, end = int(math.floor(start)), int(math.ceil(end))
        path = f'{VM_PATH}?from={start}&to={end}'
        connection = None
        try:
            if self.connection is not None:
                connection = self.connection(self.settings, self.timeout)
            else:
                import ingress_proxy
                connection = ingress_proxy.upstream_connection(
                    self.settings, self.context or ssl.create_default_context(), self.timeout)
            connection.request('GET', path, headers={
                'Host': self.settings.authority, 'Accept': 'application/json', 'Accept-Encoding': 'identity',
                'Connection': 'close', 'X-Hargassner-Ingress': self.settings.token,
                'X-Hargassner-User': user, 'X-Ingress-Path': prefix.rstrip('/')})
            response = connection.getresponse()
            if response.status == 404:
                raise VmError('unsupported')
            if response.status in (401, 403):
                raise VmError('refused')
            if response.status != 200:
                raise VmError('unreachable')
            if (response.getheader('Content-Encoding', 'identity') or 'identity').lower() != 'identity':
                raise VmError('response')
            raw = response.read(VM_CAP + 1)
            if len(raw) > VM_CAP:
                raise VmError('response')
        except VmError:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise VmError('unreachable') from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
        try:
            return rules.strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise VmError('response') from None


def _int_or_none(value):
    return value if type(value) is int and 0 <= value <= 65535 else None


def _target_or_none(value):
    return float(value) if rules.number(value) and 5 <= value <= 30 else None


def parse_vm(document, start, end):
    """{circuit: [(wall, phase, target, status)]} from the VM's answer.

    The phase follows the documented state code (PHASE_BY_CODE); the target is the circuit's day or setback target (configured
    target + room correction) at that time. A stored row ends after the VM's
    gap rule; a gap becomes an unknown row. Raises VmError('response')."""
    if (not isinstance(document, dict) or document.get('schemaVersion') != 1
            or not isinstance(document.get('states'), list) or len(document['states']) > 20000
            or not isinstance(document.get('targets'), dict)):
        raise VmError('response')
    gap = document.get('gapSeconds')
    cadence = document.get('cadenceSeconds')
    gap = gap if type(gap) is int and 0 < gap <= 3600 else 900
    cadence = cadence if type(cadence) is int and 0 < cadence <= gap else 300
    targets = {}
    for circuit in rules.CIRCUITS:
        series = document['targets'].get(circuit, [])
        if not isinstance(series, list) or len(series) > 10000:
            raise VmError('response')
        rows = []
        for row in series:
            if (not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int
                    or not all(item is None or rules.number(item) for item in row[1:])):
                raise VmError('response')
            rows.append((row[0], _target_or_none(row[1]), _target_or_none(row[2])))
        rows.sort(key=lambda row: row[0])
        targets[circuit] = Series(rows)
    result = {circuit: [] for circuit in rules.CIRCUITS}
    previous = None
    for row in document['states']:
        if (not isinstance(row, list) or len(row) != 5 or type(row[0]) is not int
                or not all(item is None or rules.number(item) for item in row[1:])):
            raise VmError('response')
        when = row[0]
        if not start - gap <= when <= end:
            continue
        if previous is not None and when - previous > gap:
            for circuit in rules.CIRCUITS:
                result[circuit].append((previous + cadence, None, None, None))
        previous = when
        for index, circuit in ((1, 'hc1'), (3, 'hc2')):
            code = _int_or_none(row[index])
            phase = PHASE_BY_CODE.get(code)
            known = targets[circuit].at(when)
            target = None
            if known is not None and phase is not None:
                target = known[1] if phase == 'day' else known[2]
            result[circuit].append((when, phase, target, code))
    if previous is not None:
        for circuit in rules.CIRCUITS:
            result[circuit].append((previous + cadence, None, None, None))
    return {circuit: rows for circuit, rows in result.items() if any(row[3] is not None for row in rows)}


def _wall(row):
    for key in ('last_updated', 'last_changed'):
        value = rules.parse_time(row.get(key))
        if value is not None:
            return value
    return None


def parse_history(document, room, climates):
    """(room rows, {circuit: climate rows}) of one history answer.

    room rows: (wall, value or None); climate rows: (wall, phase, target,
    status). Unknown, unavailable or odd rows become None values (gaps)."""
    climate_names = {entity: circuit for circuit, entity in climates.items()}
    room_rows, circuit_rows = [], {circuit: [] for circuit in climates}
    if not isinstance(document, list):
        raise ApiError('json')
    for series in document:
        if not isinstance(series, list) or not series:
            continue
        entity = None
        for row in series[:MAX_ROWS]:
            if not isinstance(row, dict):
                continue
            entity = row.get('entity_id', entity)
            when = _wall(row)
            if when is None:
                continue
            state = row.get('state')
            if room is not None and entity == room:
                try:
                    value = float(state) if isinstance(state, str) and len(state) <= 32 else None
                except ValueError:
                    value = None
                unit = (row.get('attributes') or {}).get('unit_of_measurement') if isinstance(row.get('attributes'), dict) else None
                if value is not None and unit == '°F':
                    value = (value - 32.0) * 5.0 / 9.0
                if value is not None and not (math.isfinite(value) and -30.0 <= value <= 60.0):
                    value = None
                room_rows.append((when, value))
            elif entity in climate_names:
                attributes = row.get('attributes') if isinstance(row.get('attributes'), dict) else {}
                available = state not in (None, 'unavailable', 'unknown')
                phase = {'Tag': 'day', 'Absenken': 'setback'}.get(attributes.get('Sollwertphase')) if available else None
                target = attributes.get('temperature') if available else None
                target = float(target) if rules.number(target) and 5 <= target <= 30 else None
                code = attributes.get('Heizkreisstatus-Code') if available else None
                code = code if type(code) is int and 0 <= code <= 65535 else None
                circuit_rows[climate_names[entity]].append((when, phase, target, code))
    return room_rows, circuit_rows


class Series:
    """Rows in time order; the value at t is the last row at or before t."""

    def __init__(self, rows, max_age=None):
        rows = sorted(rows, key=lambda row: row[0])
        self.times = [row[0] for row in rows]
        self.rows = rows
        self.max_age = max_age

    def at(self, when):
        index = bisect_right(self.times, when) - 1
        if index < 0:
            return None
        row = self.rows[index]
        if self.max_age is not None and when - row[0] > self.max_age:
            return None
        return row

    def values(self, start, end):
        """Room values in [start, end], else the last one before (within max_age)."""
        low, high = bisect_right(self.times, start - 1e-9), bisect_right(self.times, end)
        values = [row[1] for row in self.rows[low:high] if row[1] is not None]
        if values:
            return values
        row = self.at(start)
        return [row[1]] if row is not None and row[1] is not None else []


def _median(values):
    return rules.median(values) if values else None


def ceil_half(value):
    return math.ceil(value * 2 - 1e-9) / 2.0


def _round5(value):
    return int(min(100, max(0, 5 * round(value / 5.0))))


def degrees(value):
    return ('%.1f' % value).replace('.', ',') + ' °C'


def kelvin(value, digits=1):
    return (('%.' + str(digits) + 'f') % value).replace('.', ',')


class HourSeries:
    """Hourly statistics rows (hour start, mean, max): the value at t is its hour's mean."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda row: row[0])
        self.times = [row[0] for row in self.rows]

    def at(self, when):
        index = bisect_right(self.times, when) - 1
        if index < 0 or when - self.rows[index][0] >= 3600:
            return None
        return self.rows[index]


def analyse(room_rows, circuit_rows, *, start, end, home, kinds, current, utc_offset=0, hourly=None):
    """The proposal for one room (pure).

    room_rows: [(wall, value)] (every change, for the heat-up response and,
    without hourly, the level); hourly: [(hour start, mean, max)] long-term
    statistics for the day level, or None; circuit_rows: {circuit: [(wall,
    phase, target, status)]}; home: the room's circuit; kinds: circuit ->
    radiator/floor; current: (allowance, hc1, hc2). Returns a dict with
    'enough', 'proposal' (allowance, hc1, hc2), 'evidence', 'warnings' and
    'facts'."""
    room = Series([row for row in room_rows if start <= row[0] <= end or row[0] < start], ROOM_GAP_S)
    hours = [row for row in hourly or () if start - 3600 < row[0] <= end]
    level = HourSeries(hours) if hours else room
    circuits = {name: Series(rows) for name, rows in circuit_rows.items() if rows}
    evidence, warnings = [], []
    facts = {'days': 0, 'events': {}, 'shared': {}, 'rates': {}, 'reached_days': 0, 'deficit': None,
             'level': 'hourly' if hours else 'fine'}
    allowance, influence = current[0], {'hc1': current[1], 'hc2': current[2]}
    firsts = [when for when, value in room_rows if value is not None and when >= start]
    firsts += [max(start, row[0]) for row in hours]
    first_data = min(firsts) if firsts else None
    if first_data is None:
        return {'enough': False, 'proposal': None, 'facts': facts, 'warnings': [],
                'evidence': ['Home Assistant hat für diesen Zeitraum keine Werte des Raumsensors.']}

    # -- day level against the home circuit's target
    days = {}
    home_series = circuits.get(home)
    if home_series is not None:
        kind = kinds.get(home) or 'radiator'
        day_since, last_phase = None, None
        when = max(start, first_data)
        while when <= end:
            row = home_series.at(when)
            phase = row[1] if row else None
            if phase == 'day' and last_phase != 'day':
                day_since = when
            if phase != 'day':
                day_since = None
            last_phase = phase
            value = level.at(when)
            if (row is not None and phase == 'day' and row[2] is not None and row[3] in SUPPLYING_CODES
                    and day_since is not None and when - day_since >= SETTLE_S[kind]
                    and value is not None and value[1] is not None):
                key = math.floor((when + utc_offset) / 86400)
                peak = value[2] if len(value) > 2 else value[1]
                days.setdefault(key, []).append((value[1], row[2], peak))
            when += STEP_S
    used = {key: samples for key, samples in days.items() if len(samples) >= DAY_SAMPLES_MIN}
    facts['days'] = len(used)
    level_known = len(used) >= DAYS_MIN
    if level_known:
        means = sorted((sum(r for r, _t, _p in samples) / len(samples), sum(t for _r, t, _p in samples) / len(samples),
                        max(p for _r, _t, p in samples)) for samples in used.values())
        deficits = sorted(target - value for value, target, _peak in means)
        mean_room = sum(value for value, _t, _p in means) / len(means)
        mean_target = sum(target for _v, target, _p in means) / len(means)
        facts['deficit'] = round(mean_target - mean_room, 2)
        facts['reached_days'] = sum(1 for _value, target, peak in means if peak >= target - 1.0)
        q80 = deficits[max(0, math.ceil(0.8 * len(deficits)) - 1)]
        allowance = float(min(rules.ROOM_SETTINGS['allowance_max'], max(0.0, ceil_half(q80 - 1.0))))
        evidence.append(f'Tagesmittel {degrees(mean_room)} bei Ziel {degrees(mean_target)} '
                        f'({len(used)} Tage mit Tagbetrieb ausgewertet'
                        + (', aus Stundenmitteln' if hours else '') + ').')
        if facts['reached_days'] == 0:
            warnings.append(f'Der Raum erreicht das Ziel nie: an keinem der {len(used)} Tage näher als 1 °C. '
                            'Heizkörper, Ventil oder Stellantrieb prüfen, bevor Sie übernehmen: Anlernen würde '
                            'einen Fehler als normal übernehmen.')
        elif 2 * facts['reached_days'] < len(used):
            warnings.append(f'Der Raum erreicht das Ziel selten: nur an {facts["reached_days"]} von {len(used)} '
                            'Tagen näher als 1 °C. Heizkörper, Ventil oder Stellantrieb prüfen, bevor Sie übernehmen.')
        if allowance > current[0] and (allowance - current[0] > 1.0 or allowance > 2.0):
            warnings.append(f'Der Vorschlag erlaubt deutlich mehr Abweichung ({kelvin(allowance)} °C statt '
                            f'{kelvin(current[0])} °C). "Zu kalt" wird dann erst später gemeldet; nur übernehmen, '
                            'wenn der Raum wirklich so kühl bleiben soll.')
    else:
        evidence.append(f'Zu wenig Tage mit Tagbetrieb für die erlaubte Abweichung ({len(used)} von mindestens '
                        f'{DAYS_MIN}); sie bleibt unverändert.')

    # -- heat-up starts per circuit
    starts = {}
    for name, series in circuits.items():
        found, previous = [], None
        for when, phase, target, _code in series.rows:
            if when < start or when > end:
                previous = (phase, target)
                continue
            if previous is not None and phase == 'day' and target is not None:
                before_phase, before_target = previous
                if ((before_phase == 'setback')
                        or (before_phase == 'day' and before_target is not None and target - before_target >= 1.0 - 1e-9)):
                    found.append((when, target))
            previous = (phase, target)
        starts[name] = found
    rates_ok = {}
    for name, found in starts.items():
        kind = kinds.get(name) or 'radiator'
        window = WINDOW_S[kind]
        other = [when for circuit, items in starts.items() if circuit != name for when, _t in items]
        own, shared = [], 0
        for when, target in found:
            if when + window > end:
                continue
            status = circuits[name].at(when + 1800)
            later = circuits[name].at(when + window)
            if status is None or status[3] not in SUPPLYING_CODES or later is None or later[1] != 'day':
                continue
            first = _median(room.values(when, when + 900))
            if first is None or target - first < EVENT_DEFICIT - 1e-9:
                continue
            # A start together with the other circuit says nothing about either
            # alone, unless the room has no influence from the other circuit
            # (as configured now): then it belongs to the room's own circuit.
            alone = current[2 if name == 'hc1' else 1] == 0 and name == home
            if not alone and any(abs(when - moment) <= SHARED_S for moment in other):
                shared += 1
                continue
            last = _median(room.values(when + window - 900, when + window))
            if last is None:
                continue
            own.append((last - first) * 3600.0 / window)
        facts['events'][name] = len(own)
        facts['shared'][name] = shared
        label = 'HK' + name[-1]
        if len(own) >= EVENTS_MIN:
            rate = _median(own)
            facts['rates'][name] = round(rate, 3)
            rates_ok[name] = rate
            influence[name] = _round5(100.0 * max(0.0, rate) / REFERENCE_RATE[kind])
            evidence.append(f'Anstieg beim Aufheizen {kelvin(rate)} K/h, {label} ({len(own)} Aufheizvorgänge).')
        elif shared >= EVENTS_MIN and not own:
            evidence.append(f'{label}: nur gemeinsam mit dem anderen Heizkreis aufgeheizt ({shared}mal); '
                            'sein Anteil ist nicht trennbar und bleibt unverändert.')
        else:
            evidence.append(f'{label}: zu wenig Aufheizvorgänge ({len(own)} von mindestens {EVENTS_MIN}); '
                            'der Einfluss bleibt unverändert.')
    strong = rules.ROOM_SETTINGS['strong']
    if home in rates_ok and WEAK_INFLUENCE <= influence[home] < strong <= current[1 + rules.CIRCUITS.index(home)]:
        warnings.append(f'Der Einfluss von Heizkreis {home[-1]} sinkt unter {strong} %: der Raum zählt dann '
                        'nicht mehr voll für die Prüfung des Heizkreises. Heizkörper prüfen, wenn er früher '
                        'besser warm wurde.')
    for name in rates_ok:
        index = 1 + rules.CIRCUITS.index(name)
        if (name != home and influence[name] >= rules.ROOM_SETTINGS['judged']
                and current[index] < rules.ROOM_SETTINGS['judged']):
            warnings.append(f'Laut Verlauf heizt auch Heizkreis {name[-1]} den Raum ({influence[name]} %). '
                            'Prüfen, ob das stimmt: ein Raum an beiden Heizkreisen hält eine Störung des einen '
                            'nicht mehr auf, zählt aber auch weniger für ihn.')
    if home in rates_ok and influence[home] < WEAK_INFLUENCE:
        warnings.append(f'Der Raum wird beim Aufheizen von Heizkreis {home[-1]} kaum wärmer '
                        f'({kelvin(rates_ok[home])} K/h). Wenn dieser Heizkreis ihn eigentlich heizt, '
                        'Heizkörper oder Ventil prüfen.')
    enough = level_known or bool(rates_ok)
    if not enough:
        return {'enough': False, 'proposal': None, 'facts': facts, 'warnings': [], 'evidence': evidence}
    other = 'hc2' if home == 'hc1' else 'hc1'
    if influence[other] > influence[home]:
        evidence.append(f'Heizkreis {other[-1]} heizt den Raum stärker: beim Übernehmen wird der Raum '
                        f'Heizkreis {other[-1]} zugeordnet.')
    return {'enough': True, 'proposal': (allowance, influence['hc1'], influence['hc2']),
            'evidence': evidence, 'warnings': warnings, 'facts': facts}


def collect(api, room, climates, start, end, cancelled=lambda: False, *, newest_first=False, stop_empty=None):
    """Read the history in CHUNK_S pieces. Returns (room rows, circuit rows,
    failed pieces) or raises ApiError('history') when too many pieces fail;
    'cancelled' when cancelled. room None reads only the climates.

    newest_first with stop_empty=N reads backwards from `end` and stops after
    N readable pieces in a row without any row: the recorder keeps nothing
    older (purge_keep_days)."""
    entities = ([room] if room else []) + [climates[name] for name in rules.CIRCUITS if name in climates]
    room_rows, circuit_rows = [], {name: [] for name in climates}
    pieces = failed = empty = 0
    if not entities:
        return room_rows, circuit_rows, 0
    bounds, when = [], start
    while when < end:
        bounds.append((when, min(end, when + CHUNK_S)))
        when += CHUNK_S
    if newest_first:
        bounds.reverse()
    for low, upper in bounds:
        if cancelled():
            raise ApiError('cancelled')
        pieces += 1
        try:
            document = api.history(entities, low, upper)
            rows, circuits = parse_history(document, room, climates)
            room_rows.extend(rows)
            for name, items in circuits.items():
                circuit_rows[name].extend(items)
            empty = 0 if rows or any(circuits.values()) else empty + 1
        except ApiError as error:
            if error.args and error.args[0] == 'not_allowed':
                raise
            failed += 1
        if stop_empty is not None and empty >= stop_empty:
            break
    if pieces and failed / pieces > MAX_FAILED_SHARE:
        raise ApiError('history')
    return room_rows, circuit_rows, failed


def read_statistics(token, entity, start, end):
    """Hourly long-term statistics of the room sensor (ha_websocket)."""
    return ha_websocket.statistics_during_period(token, entity, start, end)


def hourly_rows(statistics):
    """[(wall, mean, max)] of plausible hours (°C, -30 to 60)."""
    rows = []
    for moment, mean, _low, high in statistics:
        if mean is None or not -30.0 <= mean <= 60.0:
            continue
        peak = high if high is not None and -30.0 <= high <= 60.0 and high >= mean else mean
        rows.append((moment, mean, peak))
    return rows


def _span_days(times):
    """Whole days between the first and the last time (at least 1 when any)."""
    times = [when for when in times if when is not None]
    if not times:
        return 0
    return max(1, int(round((max(times) - min(times)) / 86400.0)))


def _days(count):
    return '1 Tag' if count == 1 else f'{count} Tage'


def gather(api, room, climates, start, end, *, token=None, statistics=None, vm=None, cancelled=lambda: False):
    """Read every source for one analysis. Returns a dict: room (fine rows),
    hourly (rows or None), circuits ({circuit: rows}), failed (pieces) and
    sources (German evidence lines naming each source and its resolution)."""
    sources, failed = [], 0
    # -- heating circuits: the VM first
    circuits, vm_note, vm_low = None, None, end
    vm_start = max(start, end - VM_DAYS * 86400)
    if vm is None:
        vm_note = 'not_configured'
    else:
        try:
            circuits = parse_vm(vm(vm_start, end), vm_start, end)
        except VmError as error:
            vm_note = error.args[0] if error.args and error.args[0] in VM_ERROR_TEXT else 'unreachable'
        if circuits is not None:
            # The VM counts from its first row with a phase and a target; an
            # answer without one (journal purged, new database) is not usable
            # and Home Assistant stays the source (review H1/M2).
            known = [row[0] for rows in circuits.values() for row in rows if row[2] is not None]
            if known:
                vm_low = min(known)
            else:
                circuits, vm_note = None, 'empty'
    if cancelled():
        raise ApiError('cancelled')
    # -- room: long-term statistics
    hourly, stats_note = None, None
    if statistics is None or token is None:
        stats_note = 'unavailable'
    else:
        try:
            hourly = hourly_rows(statistics(token, room, start, end)) or None
            stats_note = None if hourly else 'none'
        except (ha_websocket.WebSocketError, OSError):
            stats_note = 'unavailable'
    if cancelled():
        raise ApiError('cancelled')
    # -- recorder history: the room's changes (and the climates without the VM)
    fine_start = max(start, end - (FINE_DAYS if hourly else HA_HISTORY_DAYS) * 86400)
    climate_set = climates if circuits is None else {}
    room_rows, ha_circuits, failed = collect(api, room, climate_set, fine_start, end, cancelled,
                                             newest_first=True, stop_empty=EMPTY_STOP)
    # -- older circuit context from the recorder where the VM does not reach
    older_low = max(start, end - HA_HISTORY_DAYS * 86400)
    older_high = vm_low if circuits is not None else fine_start
    if climates and older_low < older_high:
        _rows, older, more = collect(api, None, climates, older_low, older_high, cancelled,
                                     newest_first=True, stop_empty=EMPTY_STOP)
        failed += more
        for name, rows in older.items():
            ha_circuits.setdefault(name, []).extend(rows)
    merged = {}
    for name in rules.CIRCUITS:
        rows = list(ha_circuits.get(name, []))
        if circuits is not None:
            # VM rows win from their first known row; recorder rows only
            # before it, ended there so none is held into the VM's part (M3).
            rows = [row for row in rows if row[0] < vm_low]
            if rows:
                rows.append((vm_low, None, None, None))
            rows += [row for row in circuits.get(name, []) if row[0] >= vm_low]
        if rows:
            merged[name] = rows
    # -- evidence of the sources
    fine_days = _span_days([when for when, value in room_rows if value is not None])
    if hourly:
        line = (f'Raumtemperatur: Langzeitstatistik von Home Assistant, stündlich, '
                f'{_days(_span_days([row[0] for row in hourly]))} vorhanden')
        line += (f'; feiner Verlauf (jede Änderung) {_days(fine_days)} für das Aufheizen.' if fine_days
                 else '; kein feiner Verlauf für das Aufheizen.')
    else:
        reason = ('keine Langzeitstatistik in Home Assistant (dem Sensor fehlt wohl state_class: measurement)'
                  if stats_note == 'none' else 'Langzeitstatistik von Home Assistant nicht lesbar')
        line = (f'Raumtemperatur: {reason}; ersatzweise Verlauf von Home Assistant (jede Änderung), '
                f'{_days(fine_days)}.' if fine_days else f'Raumtemperatur: {reason}; auch kein Verlauf.')
    sources.append(line)
    vm_days = _span_days([row[0] for rows in (circuits or {}).values() for row in rows if row[2] is not None])
    ha_days = _span_days([row[0] for name, rows in ha_circuits.items() for row in rows
                          if row[1] is not None and (circuits is None or row[0] < vm_low)])
    if circuits is not None:
        line = (f'Heizkreise: VM-Verlauf, alle 5 Minuten, {_days(vm_days)}' if vm_days
                else 'Heizkreise: VM-Verlauf ohne Daten im Zeitraum')
        if ha_days:
            line += f'; davor Verlauf der Heizkreise in Home Assistant, {_days(ha_days)}'
        sources.append(line + '.')
    else:
        line = f'Heizkreise: VM-Verlauf nicht verwendet ({VM_ERROR_TEXT[vm_note]}); '
        line += (f'ersatzweise Verlauf der Heizkreise in Home Assistant, {_days(ha_days)}.' if ha_days
                 else 'auch Home Assistant hat keinen Verlauf der Heizkreise.')
        sources.append(line)
    return {'room': room_rows, 'hourly': hourly, 'circuits': merged, 'failed': failed, 'sources': sources,
            'vm': vm_note, 'statistics': stats_note}


# ---------------------------------------------------------------------------
# Persistent learning state (/data/room_learning.json)

ENTRY_STATES = ('recording', 'proposal', 'insufficient')


def _text_list(value, limit=12):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError
    for item in value:
        if not isinstance(item, str) or len(item) > 400:
            raise ValueError
    return list(value)


def _clean_entry(entry, now):
    """A saved per-room entry, checked; raises ValueError."""
    if not isinstance(entry, dict):
        raise ValueError
    state = entry.get('state')
    if state == 'recording':
        if set(entry) != {'state', 'start', 'end', 'days'} or entry['days'] not in RECORD_PERIODS:
            raise ValueError
        if not all(rules.number(entry[key]) for key in ('start', 'end')) or entry['end'] - entry['start'] != entry['days'] * 86400:
            raise ValueError
        return dict(entry)
    if state in ('proposal', 'insufficient'):
        keys = {'state', 'made', 'start', 'end', 'evidence', 'warnings', 'proposal', 'current', 'home'}
        if set(entry) != keys or not all(rules.number(entry[key]) for key in ('made', 'start', 'end')):
            raise ValueError
        _text_list(entry['evidence']), _text_list(entry['warnings'])
        if entry['home'] not in rules.CIRCUITS:
            raise ValueError
        for key in ('proposal', 'current'):
            value = entry[key]
            if value is None and state == 'insufficient':
                continue
            if (not isinstance(value, list) or len(value) != 3 or rules.allowance_value(value[0]) is None
                    or rules.influence_value(value[1]) is None or rules.influence_value(value[2]) is None):
                raise ValueError
        if state == 'proposal' and entry['proposal'] is None:
            raise ValueError
        return dict(entry)
    raise ValueError


def _clean_learned(entry):
    if (not isinstance(entry, dict) or set(entry) != {'wall', 'values'} or not rules.number(entry['wall'])
            or not isinstance(entry['values'], list) or len(entry['values']) != 3
            or rules.allowance_value(entry['values'][0]) is None
            or rules.influence_value(entry['values'][1]) is None or rules.influence_value(entry['values'][2]) is None):
        raise ValueError
    return {'wall': entry['wall'], 'values': list(entry['values'])}


class LearningFile:
    """The learning state: written whole (temporary file, fsync, rename), 0600."""

    def __init__(self, path=LEARNING_PATH):
        self.path = path

    def load(self, now=None):
        """{'rooms': {...}, 'learned': {...}}; an unreadable file gives empty state."""
        empty = {'rooms': {}, 'learned': {}}
        try:
            with open(self.path, 'rb') as stream:
                raw = stream.read(LEARNING_MAX_BYTES + 1)
        except OSError:
            return empty
        try:
            if len(raw) > LEARNING_MAX_BYTES:
                raise ValueError
            document = rules.strict_json(raw)
            if not isinstance(document, dict) or set(document) != {'schema', 'rooms', 'learned'} \
                    or document['schema'] != LEARNING_SCHEMA:
                raise ValueError
            rooms, learned = document['rooms'], document['learned']
            if not isinstance(rooms, dict) or not isinstance(learned, dict) \
                    or len(rooms) > rules.MAX_SENSORS or len(learned) > rules.MAX_SENSORS:
                raise ValueError
            result = {'rooms': {}, 'learned': {}}
            for entity, entry in rooms.items():
                if not rules.ENTITY_PATTERN.fullmatch(entity):
                    raise ValueError
                result['rooms'][entity] = _clean_entry(entry, now)
            for entity, entry in learned.items():
                if not rules.ENTITY_PATTERN.fullmatch(entity):
                    raise ValueError
                result['learned'][entity] = _clean_learned(entry)
            return result
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return empty

    def save(self, state):
        document = {'schema': LEARNING_SCHEMA, 'rooms': state['rooms'], 'learned': state['learned']}
        payload = rules.canonical(document).encode('utf-8')
        if len(payload) > LEARNING_MAX_BYTES:
            return False
        directory = os.path.dirname(self.path) or '.'
        handle, temporary = None, None
        try:
            handle, temporary = tempfile.mkstemp(prefix='.room-learning-', dir=directory)
            os.fchmod(handle, stat.S_IRUSR | stat.S_IWUSR)
            os.write(handle, payload)
            os.fsync(handle)
            os.close(handle)
            handle = None
            os.replace(temporary, self.path)
            temporary = None
            return True
        except OSError:
            return False
        finally:
            if handle is not None:
                os.close(handle)
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


class RoomLearning:
    """The owner's learning runs; one analysis at a time, in its own thread.

    The room page calls start/record/cancel/discard/forget and reads `view`;
    acceptance is written by the room page (room_picker.py), which then calls
    `accepted`. `quiet()` names the rooms being recorded (their own heating
    checks pause)."""

    def __init__(self, *, file=None, token_source=None, api_factory=LearningApi, clock=time.time,
                 kinds=lambda: {}, utc_offset=lambda: 0, on_quiet=lambda rooms: None,
                 statistics=None, vm=None):
        """statistics(token, entity, start, end) -> [(start, mean, min, max)]
        (read_statistics in the app); vm: a VmHistory or None (no VM: the
        circuits come from Home Assistant's recorder, as in 1.6.9)."""
        self.file = file
        self.token_source, self.api_factory = token_source, api_factory
        self.statistics, self.vm = statistics, vm
        self.clock = clock
        self.kinds, self.utc_offset = kinds, utc_offset
        self.on_quiet = on_quiet
        self.lock = threading.Lock()
        self.state = file.load(clock()) if file is not None else {'rooms': {}, 'learned': {}}
        self.running = None      # (entity, threading.Event cancel, threading.Event done)
        self._recordings = ()    # snapshot for quiet(), replaced under the lock
        self._snapshot()
        self.on_quiet(self.quiet())

    # -- state
    def _snapshot(self):
        self._recordings = tuple((entity, entry['start'], entry['end']) for entity, entry in self.state['rooms'].items()
                                 if entry['state'] == 'recording')

    def _save(self):
        """Called with the lock held."""
        if self.file is not None:
            self.file.save(self.state)
        self._snapshot()
        self.on_quiet(self.quiet())

    def quiet(self):
        """Rooms being recorded now. Lock-free: the monitor thread reads one
        immutable snapshot, replaced whole under the lock."""
        now = self.clock()
        return frozenset(entity for entity, start, end in self._recordings if start <= now < end)

    def view(self, entity):
        """(entry or None, learned or None, analysing: bool)."""
        with self.lock:
            entry = self.state['rooms'].get(entity)
            learned = self.state['learned'].get(entity)
            analysing = self.running is not None and self.running[0] == entity and not self.running[2].is_set()
            return (dict(entry) if entry else None), (dict(learned) if learned else None), analysing

    def prune(self, entities):
        """Forget rooms no longer configured on a circuit."""
        with self.lock:
            changed = False
            for part in ('rooms', 'learned'):
                for entity in [e for e in self.state[part] if e not in entities]:
                    del self.state[part][entity]
                    changed = True
            if changed:
                self._save()

    def due(self):
        """Recordings that have ended: (entity, entry) to analyse."""
        now = self.clock()
        with self.lock:
            return [(entity, dict(entry)) for entity, entry in self.state['rooms'].items()
                    if entry['state'] == 'recording' and now >= entry['end']]

    # -- actions
    def start(self, entity, home, current, days=None, wait=20.0, period=None, identity=None):
        """Analyse the history now; returns (status, German message).
        identity: (HA user id, Ingress path) of the administrator who started
        it, for the VM read; None reads no VM history."""
        with self.lock:
            if self.running is not None and not self.running[2].is_set():
                return 409, 'Es wird gerade ein Raum angelernt. Bitte warten oder abbrechen.'
            entry = self.state['rooms'].get(entity)
            if entry is not None and entry['state'] == 'recording' and period is None:
                return 409, 'Für diesen Raum läuft eine Aufzeichnung. Erst abbrechen.'
            cancel, done = threading.Event(), threading.Event()
            self.running = (entity, cancel, done)
        now = self.clock()
        if period is None:
            span = (days or MAX_DAYS) * 86400
            period = (now - span, now)
        thread = threading.Thread(target=self._run, args=(entity, home, current, period, cancel, done, identity),
                                  name='room-learning', daemon=True)
        thread.start()
        if done.wait(wait):
            entry, _learned, _analysing = self.view(entity)
            if entry is None:
                return 200, 'Abgebrochen.'
            if entry['state'] == 'proposal':
                return 200, 'Vorschlag erstellt. Bitte prüfen und übernehmen oder verwerfen.'
            return 200, 'Zu wenig Daten für einen Vorschlag.'
        return 202, 'Die Auswertung läuft noch. Die Seite zeigt das Ergebnis nach dem Neuladen.'

    def _run(self, entity, home, current, period, cancel, done, identity=None):
        start, end = period
        try:
            token = self.token_source() if self.token_source else None
            states = self.api_factory(token, ()).states() if token else None
            climates = find_climates(states) if states is not None else {}
            api = self.api_factory(token, [entity] + list(climates.values()))
            vm = None
            if self.vm is not None:
                def vm(low, high):
                    return self.vm.read(identity, low, high)
            data = gather(api, entity, climates, start, end, token=token, statistics=self.statistics, vm=vm,
                          cancelled=cancel.is_set)
            result = analyse(data['room'], data['circuits'], start=start, end=end, home=home, kinds=self.kinds(),
                             current=current, utc_offset=self.utc_offset(), hourly=data['hourly'])
            evidence = list(result['evidence']) + data['sources']
            missing = [name for name in rules.CIRCUITS if name not in data['circuits']]
            if missing:
                evidence.append('Keine Heizkreisdaten für '
                                + ', '.join('Heizkreis ' + name[-1] for name in missing) + '.')
            if data['failed']:
                evidence.append(f'{data["failed"]} Abschnitte des Verlaufs von Home Assistant waren nicht '
                                'lesbar (Lücken).')
            entry = {'state': 'proposal' if result['enough'] else 'insufficient', 'made': self.clock(),
                     'start': start, 'end': end, 'evidence': evidence[:12], 'warnings': result['warnings'][:6],
                     'proposal': list(result['proposal']) if result['proposal'] else None,
                     'current': list(current), 'home': home}
        except ApiError as error:
            if error.args and error.args[0] == 'cancelled':
                entry = None
            else:
                entry = {'state': 'insufficient', 'made': self.clock(), 'start': start, 'end': end,
                         'evidence': ['Der Verlauf von Home Assistant war nicht lesbar. Später erneut versuchen.'],
                         'warnings': [], 'proposal': None, 'current': list(current), 'home': home}
        except Exception:
            entry = {'state': 'insufficient', 'made': self.clock(), 'start': start, 'end': end,
                     'evidence': ['Interner Fehler bei der Auswertung.'], 'warnings': [], 'proposal': None,
                     'current': list(current), 'home': home}
        with self.lock:
            if cancel.is_set() or entry is None:
                self.state['rooms'].pop(entity, None)
            else:
                self.state['rooms'][entity] = entry
            self._save()
        done.set()

    def record(self, entity, days):
        with self.lock:
            if self.running is not None and self.running[0] == entity and not self.running[2].is_set():
                return 409, 'Die Auswertung läuft noch.'
            now = self.clock()
            self.state['rooms'][entity] = {'state': 'recording', 'start': now, 'end': now + days * 86400,
                                           'days': days}
            self._save()
        return 200, (f'Aufzeichnung für {days} Tage gestartet. Bis dahin meldet dieser Raum keine '
                     'Heizungsprobleme; Frost und Sensor werden weiter geprüft.')

    def cancel(self, entity):
        with self.lock:
            if self.running is not None and self.running[0] == entity and not self.running[2].is_set():
                self.running[1].set()
            removed = self.state['rooms'].pop(entity, None) is not None
            self._save()
        return 200, 'Abgebrochen.' if removed else 'Nichts zum Abbrechen.'

    def discard(self, entity):
        with self.lock:
            entry = self.state['rooms'].get(entity)
            if entry is None or entry['state'] == 'recording':
                return 409, 'Kein Vorschlag zum Verwerfen.'
            del self.state['rooms'][entity]
            self._save()
        return 200, 'Vorschlag verworfen. Es wurde nichts geändert.'

    def proposal(self, entity):
        with self.lock:
            entry = self.state['rooms'].get(entity)
            return dict(entry) if entry and entry['state'] == 'proposal' else None

    def accepted(self, entity, values, made=None):
        """Mark the room learned; the accepted proposal is removed only if it
        is still the one shown (a new analysis meanwhile stays)."""
        with self.lock:
            entry = self.state['rooms'].get(entity)
            if entry is not None and (made is None or entry.get('made') == made):
                self.state['rooms'].pop(entity, None)
            self.state['learned'][entity] = {'wall': self.clock(), 'values': list(values)}
            self._save()

    def forget(self, entity):
        with self.lock:
            self.state['learned'].pop(entity, None)
            self._save()
