"""Room page with the sensor picker (app 1.6.1; per-room allowance 1.6.8; free
allowance, circuit influence and room learning 1.6.9).

The page lists every temperature sensor of Home Assistant and lets a Home
Assistant administrator choose which ones the room monitoring watches. It
changes nothing in Home Assistant itself: it reads states, reads the app's own
options and saves the app's own options through the Supervisor, then hands
the new selection to the running house monitor (no restart).

The complete set of requests of this module (PickerApi.allowed, list_users):

- GET  /addons/self/info          the app's stored options (Supervisor)
- POST /addons/self/options       the app's own options, merged (Supervisor)
- POST /core/api/template         one fixed read-only template: the area name
                                  of each temperature sensor (Core)
- Core WebSocket config/auth/list whether the ingress user is an administrator
- GET  /core/api/states and /core/api/history/period/...  only for "Raum
                                  anlernen", read-only (room_learning.py)

Every other request of the page (the list of states) uses the worker's
CoreApi. A write is accepted only from an administrator, through Supervisor
ingress, same-origin, with the page token and a JSON body. Logs carry fixed
categories only, never entity ids, names or user ids.
"""
import hashlib
import hmac
import html
import json
import re
import secrets
import threading
import time
import unicodedata
from urllib.parse import urlsplit

import house_rules as rules
import room_learning as learning
from ha_websocket import WebSocketError, list_users
from house_monitor import (ApiError, CoreApi, CANDIDATE_CACHE_S, PAGE_STYLE, default_token, status_html, _time)

INFO_PATH = '/addons/self/info'
OPTIONS_PATH = '/addons/self/options'
TEMPLATE_PATH = '/core/api/template'
# Read-only: names the area of each temperature sensor that has one (its own
# area or its device's). Nothing else is rendered or changed.
AREA_TEMPLATE = (
    "{%- set ns = namespace(rows=[]) -%}"
    "{%- for s in states.sensor if s.attributes.device_class == 'temperature'"
    " or s.attributes.unit_of_measurement in ['°C', '°F'] -%}"
    "{%- set a = area_name(s.entity_id) -%}"
    "{%- if a and ns.rows | length < 1000 -%}{%- set ns.rows = ns.rows + [[s.entity_id, a]] -%}{%- endif -%}"
    "{%- endfor -%}"
    "{{ ns.rows | tojson }}")
INFO_CAP = 262144
AREA_CAP = 1024 * 1024
SAVE_BODY_CAP = 16384
TOKEN_TTL_S = 1800
ADMIN_TTL_S = 60
ADMIN_RETRY_S = 10
PAGE_CANDIDATE_AGE_S = 60
APPLY_WAIT_S = 5
TOKEN_HEADER = 'X-Raumsensoren-Token'
SETTINGS_KEY = rules.ROOM_SETTINGS['key']
HOUSE_KEYS = ('raumueberwachung_modus', 'heizkreis_1_art', 'heizkreis_2_art') + rules.LIST_KEYS + (SETTINGS_KEY,)
HOUSE_DEFAULTS = {'raumueberwachung_modus': 'beobachten', 'heizkreis_1_art': 'automatisch',
                  'heizkreis_2_art': 'automatisch', **{key: [] for key in rules.LIST_KEYS}, SETTINGS_KEY: []}
GROUPS = {'heizkreis_1': 'heizkreis_1_sensoren', 'heizkreis_2': 'heizkreis_2_sensoren', 'weitere': 'haus_sensoren'}
GROUP_TEXT = (('', 'nicht überwachen'), ('heizkreis_1', 'Heizkreis 1'), ('heizkreis_2', 'Heizkreis 2'),
              ('weitere', 'Weitere Räume'))
FLAGS = (('ofen', 'ohne_ueberwaermung', 'kreis', 'Ofen oder viel Sonne'),
         ('frost', 'haus_frost_sensoren', 'weitere', 'Frostwarnung'))
# The 1.6.8 choice lists, read for display (migration) and still written as a
# mirror for a downgrade: page value, option list.
ALLOWANCE_CHOICES = (('0', None), ('2', 'kuehlere_raeume'), ('4', 'kuehler_4_raeume'),
                     ('6', 'kuehler_6_raeume'), ('frost', rules.FROST_ONLY_LIST))
# The free settings of a room on a circuit (app 1.6.9).
ALLOWANCE_LABEL = 'Darf kühler sein um'
INFLUENCE_LABEL = 'Einfluss Heizkreis {index}'
INFLUENCE_FIELDS = ('einfluss_hk1', 'einfluss_hk2')
TYPE_CHOICES = (('automatisch', 'automatisch (aus der Steuerung)'), ('heizkoerper', 'Heizkörper'),
                ('fussboden', 'Fußboden'))
BODY_KEYS = frozenset(('revision', 'modus', 'heizkreis_1_art', 'heizkreis_2_art', 'sensoren'))
ROW_KEYS = frozenset(('entity_id', 'gruppe', 'kuehler', 'einfluss_hk1', 'einfluss_hk2', 'ofen', 'frost'))
LEARN_KEYS = frozenset(('aktion', 'entity_id', 'tage', 'geprueft', 'revision'))
LEARN_ACTIONS = ('anlernen', 'aufzeichnen', 'abbrechen', 'verwerfen', 'uebernehmen', 'zuruecksetzen')
LEARN_PERIOD_TEXT = ((0, 'alles Verfügbare (bis 1 Jahr)'), (3, 'letzte 3 Tage'), (7, 'letzte 7 Tage'),
                     (14, 'letzte 14 Tage'), (30, 'letzte 30 Tage'), (90, 'letzte 90 Tage'))
REVISION_PATTERN = re.compile(r'[0-9a-f]{64}')
LOG_CATEGORIES = frozenset(('saved', 'save_refused', 'save_unconfirmed', 'admin_lookup_failed',
                            'options_unreadable', 'areas_unavailable', 'learning'))

VIEWER_TEXT = {
    'admin': 'Sie sind Administrator von Home Assistant und können die Auswahl hier ändern.',
    'user': 'Nur Administratoren von Home Assistant können die Auswahl ändern. Sie sehen sie hier nur.',
    'unknown_user': 'Ihr Benutzer ist Home Assistant nicht bekannt. Die Seite ist deshalb nur lesbar.',
    'unavailable': 'Ob Sie Administrator sind, konnte gerade nicht geprüft werden. '
                   'Die Seite ist deshalb nur lesbar. Bitte später neu laden.',
}
REFUSED_TEXT = {
    'user': 'Nur Administratoren von Home Assistant können die Auswahl ändern. Es wurde nichts gespeichert.',
    'unknown_user': 'Ihr Benutzer ist Home Assistant nicht bekannt. Es wurde nichts gespeichert.',
    'unavailable': 'Ob Sie Administrator sind, konnte gerade nicht geprüft werden. Es wurde nichts gespeichert. '
                   'Bitte später erneut versuchen.',
}


ROOMS_SUFFIX = 'api/local-monitor/raumsensoren'


class SelectionError(Exception):
    """A refused selection; args[0] is the German message for the page."""


class SaveRefused(Exception):
    """The Supervisor answered the options POST with a non-200 status."""

    def __init__(self, status):
        super().__init__('refused')
        self.status = status


def _log_line(category):
    return f'Room page: {category}.'


class PickerApi(CoreApi):
    """The page's own Supervisor and Core requests; nothing else passes."""

    def __init__(self, token, *args, **kwargs):
        super().__init__(token, (), *args, **kwargs)

    def allowed(self, method, path, body):
        if method == 'GET' and body is None:
            return path == INFO_PATH
        if method == 'POST' and path == OPTIONS_PATH:
            return isinstance(body, dict) and set(body) == {'options'} and isinstance(body['options'], dict)
        if method == 'POST' and path == TEMPLATE_PATH:
            return body == {'template': AREA_TEMPLATE}
        return False

    def stored_options(self):
        _status, document = self._json('GET', INFO_PATH, cap=INFO_CAP)
        data = document.get('data') if isinstance(document, dict) and document.get('result') == 'ok' else None
        options = data.get('options') if isinstance(data, dict) else None
        if not isinstance(options, dict):
            raise ApiError('json')
        return options

    def ingress_path(self):
        """This app's own ingress path, e.g. /api/hassio_ingress/<id>/; None if not reported."""
        _status, document = self._json('GET', INFO_PATH, cap=INFO_CAP)
        data = document.get('data') if isinstance(document, dict) and document.get('result') == 'ok' else None
        path = data.get('ingress_url') if isinstance(data, dict) else None
        if isinstance(path, str) and re.fullmatch(r'/api/hassio_ingress/[A-Za-z0-9_-]{8,128}/', path):
            return path
        return None

    def save_options(self, options):
        status, _raw = self.request('POST', OPTIONS_PATH, {'options': options})
        if status != 200:
            raise SaveRefused(status)

    def areas(self):
        status, raw = self.request('POST', TEMPLATE_PATH, {'template': AREA_TEMPLATE}, cap=AREA_CAP)
        if status != 200:
            raise ApiError('status')
        try:
            rows = rules.strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ApiError('json') from None
        if not isinstance(rows, list):
            raise ApiError('json')
        areas = {}
        for row in rows[:1000]:
            if (isinstance(row, list) and len(row) == 2 and isinstance(row[0], str)
                    and rules.ENTITY_PATTERN.fullmatch(row[0]) and isinstance(row[1], str) and row[1].strip()):
                areas[row[0]] = row[1].strip()[:100]
        return areas


def announce_room_page(token_source=default_token, api_factory=PickerApi, log=None):
    """Write the room page's address to the app log once, so it can be found
    without digging in the browser. Never raises; says nothing if unknown."""
    log = log or (lambda line: print(line, flush=True))
    try:
        path = api_factory(token_source()).ingress_path()
    except Exception:
        path = None
    if path:
        log('Raumsensoren auswählen: Adresse von Home Assistant + ' + path + ROOMS_SUFFIX
            + ' (vorher das Panel der App einmal öffnen).')
    return path


class AdminCheck:
    """Is this ingress user a Home Assistant administrator? Fails closed.

    Page rendering caches `config/auth/list` for ADMIN_TTL_S; saves bypass it. A failed lookup
    is retried after ADMIN_RETRY_S at the earliest and means view only.
    """

    def __init__(self, token_source=default_token, users=list_users, monotonic=time.monotonic, log=None):
        self.token_source, self.users, self.monotonic = token_source, users, monotonic
        self.log = log or (lambda line: print(line, flush=True))
        self.lock = threading.Lock()
        self.cache = None        # (mono, {id: (admin, name)})
        self.failed = None       # mono of the last failure

    def _table(self):
        now = self.monotonic()
        if self.cache is not None and now - self.cache[0] < ADMIN_TTL_S:
            return self.cache[1]
        if self.failed is not None and now - self.failed < ADMIN_RETRY_S:
            return None
        try:
            users = self.users(self.token_source())
            table = {}
            for user in users:
                if not isinstance(user, dict) or not isinstance(user.get('id'), str):
                    continue
                groups = user.get('group_ids')
                admin = (user.get('is_active') is True and user.get('system_generated') is not True
                         and (user.get('is_owner') is True
                              or (isinstance(groups, list) and 'system-admin' in groups)))
                name = user.get('name') if isinstance(user.get('name'), str) else ''
                table[user['id']] = (admin, name[:100])
        except (WebSocketError, ValueError, OSError):
            self.failed = now
            self.log(_log_line('admin_lookup_failed'))
            return None
        self.cache, self.failed = (now, table), None
        return table

    def lookup(self, user, *, refresh=False):
        """Return (kind, name): kind is admin, user, unknown_user or unavailable."""
        with self.lock:
            if refresh:
                # Display caching never authorizes a write after demotion or
                # loss of the Core permission service. Saves recheck now.
                self.cache = None
            table = self._table()
        if table is None:
            return 'unavailable', ''
        if user not in table:
            return 'unknown_user', ''
        admin, name = table[user]
        return ('admin' if admin else 'user'), name


class PageTokens:
    """Per-page write token bound to the ingress user; valid TOKEN_TTL_S."""

    def __init__(self, secret=None, monotonic=time.monotonic):
        self.secret = secret or secrets.token_bytes(32)
        self.monotonic = monotonic

    def _mac(self, user, issued):
        return hmac.new(self.secret, f'{user}\n{issued}'.encode(), hashlib.sha256).hexdigest()

    def issue(self, user):
        issued = int(self.monotonic())
        return f'{issued}.{self._mac(user, issued)}'

    def valid(self, user, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9]{1,15}\.[0-9a-f]{64}', token):
            return False
        issued, mac = token.split('.')
        if not hmac.compare_digest(mac, self._mac(user, int(issued))):
            return False
        age = self.monotonic() - int(issued)
        return -1 <= age <= TOKEN_TTL_S


def house_part(options):
    """The nine room keys of an options mapping, with the app's defaults."""
    return {key: options.get(key, HOUSE_DEFAULTS[key]) for key in HOUSE_KEYS}


def revision(options):
    return hashlib.sha256(rules.canonical(house_part(options)).encode()).hexdigest()


def same_origin(headers, prefix):
    """True only for a same-origin request sent from a page of this app's ingress path.

    Origin and Referer must both be present and name the same origin; the
    Referer path must lie below the ingress path the Supervisor sent; a
    Sec-Fetch-Site header, when the browser sends one, must be same-origin.
    """
    def single(name):
        values = headers.get_all(name, [])
        if len(values) != 1 or len(values[0]) > 4096 or re.search(r'[^\x21-\x7e]', values[0]):
            return None
        return values[0]
    if headers.get_all('Sec-Fetch-Site'):
        if single('Sec-Fetch-Site') != 'same-origin':
            return False
    origin, referer = single('Origin'), single('Referer')
    if origin is None or referer is None or origin == 'null':
        return False
    try:
        source, page = urlsplit(origin), urlsplit(referer)
    except ValueError:
        return False
    if (source.scheme not in ('http', 'https') or not source.netloc or source.path or source.query
            or page.scheme != source.scheme or page.netloc != source.netloc):
        return False
    return page.path.startswith(prefix + '/')


def _configured_lists(options):
    lists = {}
    for key in rules.LIST_KEYS:
        value = options.get(key) if isinstance(options, dict) else None
        lists[key] = [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
    return lists


def configured_entities(options):
    lists = _configured_lists(options)
    return {entity for key in rules.LIST_KEYS[:3] for entity in lists[key]}


def selection_of(options):
    """Display model of an options mapping: entity -> (group, flags). Tolerant
    of an invalid selection: the first circuit list naming a sensor wins."""
    lists = _configured_lists(options)
    chosen = {}
    for group, key in GROUPS.items():
        for entity in lists[key]:
            chosen.setdefault(entity, group)
    flags = {name: set(lists[key]) for name, key, _scope, _text in FLAGS}
    return chosen, flags


def allowances_of(options):
    """entity -> the 1.6.8 choice ('2', '4', '6' or 'frost'; absent: normal).
    The larger choice wins, as in house_rules (tolerant of an invalid
    selection); a 1.6.7 kuehlere_raeume entry alone is '2'."""
    lists = _configured_lists(options)
    levels = {}
    for value, key in ALLOWANCE_CHOICES[1:]:
        for entity in lists[key]:
            levels[entity] = value
    return levels


def _entries(options):
    """entity -> (allowance, hc1, hc2) of well-formed 1.6.9 entries (tolerant)."""
    raw = options.get(SETTINGS_KEY) if isinstance(options, dict) else None
    entries = {}
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, dict) or set(entry) != rules.ROOM_SETTING_KEYS or not isinstance(entry['sensor'], str):
            continue
        values = (rules.allowance_value(entry['kuehler']), rules.influence_value(entry['einfluss_hk1']),
                  rules.influence_value(entry['einfluss_hk2']))
        if None not in values:
            entries.setdefault(entry['sensor'], values)
    return entries


def default_values(group):
    """A room new on a circuit: no allowance, 100 % on its own circuit."""
    return (0.0, 100 if group == 'heizkreis_1' else 0, 100 if group == 'heizkreis_2' else 0)


def room_values(options):
    """entity -> (allowance, hc1 %, hc2 %) of every room on a circuit.

    A 1.6.9 entry wins; otherwise the 1.6.8 lists are migrated: 0/2/4/6 K keep
    their number with 100 % on the room's circuit, "nur Frost" is no
    influence on either circuit."""
    chosen, _flags = selection_of(options)
    levels, entries = allowances_of(options), _entries(options)
    values = {}
    for entity, group in chosen.items():
        if group == 'weitere':
            continue
        if entity in entries:
            values[entity] = entries[entity]
            continue
        level = levels.get(entity, '0')
        if level == 'frost':
            values[entity] = (0.0, 0, 0)
        else:
            base = default_values(group)
            values[entity] = (float(level),) + base[1:]
    return values


def mirror_keys(allowance, first, second):
    """The 1.6.8 lists a room is written to, so a downgrade keeps the nearest
    stricter choice: no influence = nur Frost; else the allowance rounded down
    to 0/2/4/6 K. Every one of them is also in kuehlere_raeume (1.6.7: 2 K)."""
    if min(100, first + second) == 0:
        return ('kuehlere_raeume', rules.FROST_ONLY_LIST)
    if allowance >= 6:
        return ('kuehlere_raeume', 'kuehler_6_raeume')
    if allowance >= 4:
        return ('kuehlere_raeume', 'kuehler_4_raeume')
    if allowance >= 2:
        return ('kuehlere_raeume',)
    return ()


def legacy_exact(values, group):
    """True when the 1.6.8 lists alone say exactly this (no entry needed):
    0/2/4/6 K at 100 % on the own circuit, or no influence without allowance."""
    if tuple(values) == (0.0, 0, 0):
        return True
    return values[0] in (0.0, 2.0, 4.0, 6.0) and tuple(values[1:]) == default_values(group)[1:]


def settings_entry(entity, values):
    return {'sensor': entity, 'kuehler': float(values[0]), 'einfluss_hk1': int(values[1]),
            'einfluss_hk2': int(values[2])}


def options_of(parsed):
    """Lists of a running HouseOptions, for display when the stored ones are unreadable."""
    if not parsed.valid:
        return dict(HOUSE_DEFAULTS, raumueberwachung_modus=parsed.mode)
    names = {value: key for key, value in rules.OPTION_TYPES.items()}
    return {'raumueberwachung_modus': parsed.mode, 'heizkreis_1_art': names[parsed.option_types[0]],
            'heizkreis_2_art': names[parsed.option_types[1]], 'heizkreis_1_sensoren': list(parsed.hc1),
            'heizkreis_2_sensoren': list(parsed.hc2), 'haus_sensoren': list(parsed.haus),
            'kuehlere_raeume': sorted(parsed.cooler), 'ohne_ueberwaermung': sorted(parsed.no_warm),
            'haus_frost_sensoren': sorted(parsed.frost), 'kuehler_4_raeume': sorted(parsed.cooler4),
            'kuehler_6_raeume': sorted(parsed.cooler6), rules.FROST_ONLY_LIST: sorted(parsed.frost_only),
            SETTINGS_KEY: [settings_entry(row[0], row[1:]) for row in parsed.settings]}


def parse_request(document):
    """Strict shape of a save request; raises SelectionError with German text."""
    if not isinstance(document, dict) or set(document) != BODY_KEYS:
        raise SelectionError('Die Anfrage ist ungültig.')
    if not isinstance(document['revision'], str) or not REVISION_PATTERN.fullmatch(document['revision']):
        raise SelectionError('Die Anfrage ist ungültig.')
    if document['modus'] not in rules.MODES:
        raise SelectionError('Der Modus der Raumüberwachung ist unbekannt.')
    for key in ('heizkreis_1_art', 'heizkreis_2_art'):
        if not isinstance(document[key], str) or document[key] not in rules.OPTION_TYPES:
            raise SelectionError('Die Art des Heizkreises ist unbekannt.')
    rows = document['sensoren']
    if not isinstance(rows, list):
        raise SelectionError('Die Anfrage ist ungültig.')
    if len(rows) > rules.MAX_SENSORS:
        raise SelectionError(f'Höchstens {rules.MAX_SENSORS} Sensoren können überwacht werden.')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise SelectionError('Die Anfrage ist ungültig.')
        entity = row['entity_id']
        if not isinstance(entity, str) or not rules.ENTITY_PATTERN.fullmatch(entity):
            raise SelectionError('Ein Sensor ist ungültig.')
        if not isinstance(row['gruppe'], str) or row['gruppe'] not in GROUPS:
            raise SelectionError('Die Zuordnung eines Sensors ist unbekannt.')
        if any(type(row[name]) is not bool for name, _key, _scope, _text in FLAGS):
            raise SelectionError('Die Anfrage ist ungültig.')
        if rules.allowance_value(row['kuehler']) is None:
            raise SelectionError('"Darf kühler sein um" muss zwischen 0 und 10 °C in Schritten von 0,5 °C liegen.')
        if any(rules.influence_value(row[name]) is None for name in INFLUENCE_FIELDS):
            raise SelectionError('Der Einfluss eines Heizkreises muss eine ganze Zahl von 0 bis 100 % sein.')
        if entity in seen:
            raise SelectionError('Ein Sensor kann nur einer Gruppe zugeordnet werden.')
        seen.add(entity)
        circuit = row['gruppe'] != 'weitere'
        if not circuit and (row['kuehler'] != 0 or row['ofen'] or row['einfluss_hk1'] or row['einfluss_hk2']):
            raise SelectionError('"Darf kühler sein um", der Einfluss und "Ofen oder viel Sonne" gelten nur für '
                                 'Räume an einem Heizkreis.')
        if circuit:
            own, other = ((row['einfluss_hk1'], row['einfluss_hk2']) if row['gruppe'] == 'heizkreis_1'
                          else (row['einfluss_hk2'], row['einfluss_hk1']))
            if own < other:
                raise SelectionError('Der Einfluss des eigenen Heizkreises muss mindestens so groß sein wie der des '
                                     'anderen. Den Raum dem anderen Heizkreis zuordnen.')
        if circuit and row['frost']:
            raise SelectionError('"Frostwarnung" gilt nur für weitere Räume.')
    return document


def _ordered(previous, chosen):
    """Keep the stored order of sensors that stay; append new ones in page order."""
    wanted = set(chosen)
    kept = [entity for entity in previous if entity in wanted]
    known = set(kept)
    return kept + [entity for entity in chosen if entity not in known]


def build_house(stored, request):
    """Map the page's rows to the room options."""
    previous = _configured_lists(stored)
    rows = request['sensoren']
    house = {'raumueberwachung_modus': request['modus'], 'heizkreis_1_art': request['heizkreis_1_art'],
             'heizkreis_2_art': request['heizkreis_2_art']}
    for group, key in GROUPS.items():
        house[key] = _ordered(previous[key], [row['entity_id'] for row in rows if row['gruppe'] == group])
    for name, key, _scope, _text in FLAGS:
        house[key] = _ordered(previous[key], [row['entity_id'] for row in rows if row[name]])
    values = {row['entity_id']: (float(row['kuehler']), int(row['einfluss_hk1']), int(row['einfluss_hk2']))
              for row in rows if row['gruppe'] != 'weitere'}
    groups = {row['entity_id']: row['gruppe'] for row in rows}
    _write_settings(house, previous, stored, values, groups)
    return house


def _write_settings(house, previous, stored, values, groups):
    """The 1.6.9 entries (a room the 1.6.8 lists cannot express) and the 1.6.8
    mirror lists, in stored order (house_rules.ROOM_SETTINGS)."""
    order = list(values)
    for key in ('kuehlere_raeume',) + tuple(key for _value, key in ALLOWANCE_CHOICES[2:]):
        house[key] = _ordered(previous[key], [e for e in order if key in mirror_keys(*values[e])])
    stored_order = [entity for entity in _entries(stored)]
    chosen = [e for e in order if not legacy_exact(values[e], groups[e])]
    house[SETTINGS_KEY] = [settings_entry(e, values[e]) for e in _ordered(stored_order, chosen)]


def with_room(stored, entity, values, group):
    """The room options with one room's values (and circuit) replaced."""
    house = json.loads(json.dumps(house_part(stored)))
    previous = _configured_lists(stored)
    chosen, _flags = selection_of(stored)
    current = room_values(stored)
    groups = {e: g for e, g in chosen.items() if g != 'weitere'}
    if groups.get(entity) != group:
        for key in (GROUPS['heizkreis_1'], GROUPS['heizkreis_2']):
            house[key] = [e for e in house[key] if e != entity]
        house[GROUPS[group]].append(entity)
        groups[entity] = group
    current[entity] = tuple(values)
    _write_settings(house, previous, stored, current, groups)
    return house


def _value_text(value):
    """German decimal comma for a plain number such as "21.5 °C"."""
    state = value.split(' ', 1)[0]
    if state in ('unavailable', 'unknown', ''):
        return 'nicht verfügbar' if state == 'unavailable' else 'unbekannt'
    if re.fullmatch(r'-?[0-9]{1,6}\.[0-9]{1,6}(?: .*)?', value):
        return value.replace('.', ',', 1)
    return value


def _search_text(*parts):
    text = ' '.join(part for part in parts if part).lower()
    return ''.join(char for char in unicodedata.normalize('NFD', text) if not unicodedata.combining(char))


SCRIPT = r"""
(function () {
  'use strict';
  var form = document.getElementById('auswahl');
  if (!form) { return; }
  var rows = Array.prototype.slice.call(form.querySelectorAll('fieldset.sensor'));
  var search = document.getElementById('suche');
  var hits = document.getElementById('treffer');
  var count = document.getElementById('anzahl');
  var ok = document.getElementById('meldung');
  var bad = document.getElementById('fehler');
  var button = document.getElementById('speichern');
  var token = form.getAttribute('data-token');
  var max = parseInt(form.getAttribute('data-max'), 10);
  function norm(text) {
    return text.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  }
  function scope(group) { return group === 'weitere' ? 'weitere' : (group ? 'kreis' : ''); }
  function group(row) { return row.querySelector('select[name="gruppe"]'); }
  function sync(row) {
    var chosen = group(row).value;
    Array.prototype.forEach.call(row.querySelectorAll('[data-gilt]'), function (label) {
      var on = label.getAttribute('data-gilt') === scope(chosen);
      label.hidden = !on;
      if (!on) {
        var box = label.querySelector('input');
        if (box && box.type === 'checkbox') { box.checked = false; }
        if (box && box.type === 'number') { box.value = '0'; }
      }
    });
    row.classList.toggle('aktiv', chosen !== '');
  }
  function defaults(row) {
    // A room new on a circuit: no allowance, 100 % on its own circuit. Moved
    // between the circuits it keeps its allowance and swaps the influences.
    var chosen = group(row).value;
    var before = row.getAttribute('data-vorher') || '';
    row.setAttribute('data-vorher', chosen);
    if (chosen !== 'heizkreis_1' && chosen !== 'heizkreis_2') { return; }
    var first = row.querySelector('input[name="einfluss_hk1"]');
    var second = row.querySelector('input[name="einfluss_hk2"]');
    if (before === 'heizkreis_1' || before === 'heizkreis_2') {
      var value = first.value;
      first.value = second.value;
      second.value = value;
      return;
    }
    row.querySelector('input[name="kuehler"]').value = '0';
    first.value = chosen === 'heizkreis_1' ? '100' : '0';
    second.value = chosen === 'heizkreis_2' ? '100' : '0';
  }
  function numeric(row, name) {
    var text = row.querySelector('input[name="' + name + '"]').value.replace(',', '.');
    return text === '' ? null : Number(text);
  }
  function selected() {
    return rows.filter(function (row) { return group(row).value !== ''; });
  }
  function counted() {
    var n = selected().length;
    count.textContent = 'Ausgewählt: ' + n + ' von höchstens ' + max + ' Sensoren';
    count.classList.toggle('zuviel', n > max);
  }
  // Normalised once (the server already folds the text); a keystroke only
  // compares, which stays quick on a phone with many sensors (review 2026-11-23).
  var texts = rows.map(function (row) { return norm(row.getAttribute('data-suche') || ''); });
  function filter() {
    var words = norm(search.value).split(/\s+/).filter(Boolean);
    var shown = 0;
    rows.forEach(function (row, index) {
      var match = words.every(function (word) { return texts[index].indexOf(word) >= 0; });
      if (row.hidden !== !match) { row.hidden = !match; }
      if (match) { shown += 1; }
    });
    hits.textContent = shown + ' von ' + rows.length + ' Sensoren angezeigt';
  }
  function show(success, text) {
    ok.textContent = success ? text : '';
    bad.textContent = success ? '' : text;
  }
  rows.forEach(function (row) {
    row.setAttribute('data-vorher', group(row).value);
    group(row).addEventListener('change', function () { sync(row); defaults(row); counted(); });
    sync(row);
  });
  document.getElementById('suchfeld').hidden = false;
  search.addEventListener('input', filter);
  filter();
  counted();
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!token || !button) { return; }
    var chosen = selected();
    if (chosen.length > max) {
      show(false, 'Höchstens ' + max + ' Sensoren können überwacht werden. Es wurde nichts gespeichert.');
      return;
    }
    var mode = form.querySelector('input[name="modus"]:checked');
    var body = {
      revision: form.getAttribute('data-revision'),
      modus: mode ? mode.value : '',
      heizkreis_1_art: document.getElementById('art1').value,
      heizkreis_2_art: document.getElementById('art2').value,
      sensoren: chosen.map(function (row) {
        return {
          entity_id: row.getAttribute('data-entity'),
          gruppe: group(row).value,
          kuehler: numeric(row, 'kuehler'),
          einfluss_hk1: numeric(row, 'einfluss_hk1'),
          einfluss_hk2: numeric(row, 'einfluss_hk2'),
          ofen: row.querySelector('input[name="ofen"]').checked,
          frost: row.querySelector('input[name="frost"]').checked
        };
      })
    };
    button.disabled = true;
    show(true, 'Wird gespeichert ...');
    fetch(window.location.pathname, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', referrerPolicy: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Raumsensoren-Token': token },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = JSON.parse(text); } catch (error) { data = null; }
        return { status: response.status, data: data };
      });
    }).then(function (result) {
      var data = result.data;
      if (data && typeof data.message === 'string') {
        show(data.ok === true, data.message);
        if (data.ok === true && typeof data.revision === 'string') {
          form.setAttribute('data-revision', data.revision);
        }
      } else {
        show(false, 'Das Speichern ist nicht möglich (Fehler ' + result.status + '). Es wurde nichts gespeichert.');
      }
    }).catch(function () {
      show(false, 'Die Seite erreicht die App nicht. Ob gespeichert wurde, ist unklar. Bitte die Seite neu laden.');
    }).then(function () { button.disabled = false; });
  });
  // Room learning (app 1.6.9): one action per button; the page reloads after it.
  form.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.getAttribute || !target.getAttribute('data-aktion') || !token) { return; }
    var panel = target.closest('.anlernen');
    var period = panel.querySelector('select[name="zeitraum"]');
    var record = panel.querySelector('select[name="aufzeichnung"]');
    var checked = panel.querySelector('input[name="geprueft"]');
    var action = target.getAttribute('data-aktion');
    // Zurücksetzen writes the room options: a second tap confirms it, so a
    // mis-tap on a phone changes nothing (review 2026-11-23).
    if (action === 'zuruecksetzen' && target.getAttribute('data-bestaetigen') !== 'ja') {
      target.setAttribute('data-bestaetigen', 'ja');
      target.textContent = 'Wirklich auf Standardwerte zurücksetzen?';
      show(true, 'Zum Zurücksetzen auf die Standardwerte innerhalb von 5 Sekunden noch einmal tippen. '
        + 'Es wurde nichts geändert.');
      window.setTimeout(function () {
        target.removeAttribute('data-bestaetigen');
        target.textContent = 'Zurücksetzen';
      }, 5000);
      return;
    }
    if (action === 'zuruecksetzen') { target.removeAttribute('data-bestaetigen'); }
    var days = action === 'aufzeichnen' ? (record ? parseInt(record.value, 10) : null)
      : (action === 'anlernen' && period ? parseInt(period.value, 10) : null);
    var body = { aktion: action, entity_id: panel.getAttribute('data-raum'), tage: days,
                 geprueft: checked ? checked.checked : false, revision: form.getAttribute('data-revision') };
    target.disabled = true;
    show(true, action === 'anlernen' ? 'Der Verlauf wird ausgewertet ...' : 'Bitte warten ...');
    fetch(window.location.pathname + '/anlernen', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', referrerPolicy: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Raumsensoren-Token': token },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = JSON.parse(text); } catch (error) { data = null; }
        return { status: response.status, data: data };
      });
    }).then(function (result) {
      var data = result.data;
      if (data && typeof data.message === 'string') {
        show(data.ok === true, data.message);
        if (data.ok === true) { window.setTimeout(function () { window.location.reload(); }, 1500); }
      } else {
        show(false, 'Die Aktion ist nicht möglich (Fehler ' + result.status + '). Es wurde nichts geändert.');
      }
    }).catch(function () {
      show(false, 'Die Seite erreicht die App nicht. Bitte die Seite neu laden.');
    }).then(function () { target.disabled = false; });
  });
}());
"""

PICKER_STYLE = (
    'h2{margin-top:1.6rem}fieldset{border:1px solid var(--line);border-radius:8px;margin:0 0 12px;padding:8px 12px}'
    'legend{font-weight:bold;padding:0 4px;overflow-wrap:anywhere}'
    '.sensor.aktiv{border-color:var(--accent);background:var(--card)}'
    '.meta{margin:0 0 6px;color:var(--muted);font-size:.9rem;display:flex;flex-wrap:wrap;gap:4px 12px;'
    'overflow-wrap:anywhere}code{font-size:.9rem}'
    '.zeile{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center}'
    'label{cursor:pointer}select,input[type=search],button{font:inherit;color:inherit;background:var(--bg);'
    'border:1px solid var(--line);border-radius:6px;padding:6px 8px;min-height:2.5rem;max-width:100%}'
    'input[type=search]{width:100%;box-sizing:border-box}input[type=checkbox],input[type=radio]'
    '{width:1.2rem;height:1.2rem;vertical-align:middle;margin:0 6px 0 0}'
    '.wahl label{display:block;margin:4px 0}'
    'button{background:var(--accent);color:#000;border-color:var(--accent);font-weight:bold;padding:8px 20px}'
    'button:disabled{opacity:.6;cursor:wait}'
    'input[type=number]{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);'
    'border-radius:6px;padding:6px 8px;min-height:2.5rem;width:5.5rem;box-sizing:border-box}'
    '.anlernen{border-top:1px dashed var(--line);margin-top:8px;padding-top:6px;overflow-wrap:anywhere}'
    '.anlernen p{margin:4px 0}.lern-titel{font-weight:bold}.beleg{margin:4px 0;padding-left:1.2rem}'
    '.warnung{border-left:4px solid var(--bad);padding:4px 10px;background:var(--card)}'
    'button.zweit{background:var(--bg);color:var(--fg);border-color:var(--line);font-weight:normal}'
    ':focus-visible{outline:3px solid var(--accent);outline-offset:2px}'
    '.hinweis{border-left:4px solid var(--accent);padding:4px 10px;background:var(--card)}'
    '#meldung{color:var(--ok);font-weight:bold}#fehler{color:var(--bad);font-weight:bold}'
    '.zuviel{color:var(--bad);font-weight:bold}.fehlt{color:var(--bad)}'
    '.aktion{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:8px 0;'
    'margin-top:8px}[hidden]{display:none!important}')


SETTINGS_HELP = (
    'Nur Räume an einem Heizkreis: "Darf kühler sein um" (0 bis 10 °C) sagt, wie weit der Raum unter dem '
    'Sollwert des Heizkreises bleiben darf; die Meldung "zu kalt" nennt dann die erlaubte Temperatur. '
    '"Einfluss Heizkreis 1/2" (0 bis 100 %) sagt, wie stark der Heizkreis den Raum heizt; ein Raum kann an '
    'beiden Heizkreisen hängen. Unter 30 % wird der Raum für diesen Heizkreis nicht bewertet, ab 50 % zählt er '
    'für "Heizkreis zu kalt". Bei 0 % an beiden Heizkreisen werden nur Frost und Sensor geprüft.')
LEARN_HELP = (
    '"Raum anlernen" wertet den Verlauf des Raums aus Home Assistant aus und schlägt Werte vor. Übernommen wird '
    'nur, was Sie bestätigen. Achtung: Ein Raum mit defektem Heizkörper oder Ventil würde als "kühler Raum" '
    'angelernt. Deshalb zeigt der Vorschlag seine Grundlage und warnt, wenn der Raum das Ziel nie erreicht.')


def _number_text(value):
    return ('%g' % value).replace('.', ',')


def learning_html(entity, index, view, editable, zone, zones):
    """The learning part of one room on a circuit (status, evidence, actions)."""
    escape = html.escape
    entry, learned, analysing = view
    out = [f'<div class="anlernen" data-raum="{escape(entity)}"><p class="lern-titel">Raum anlernen</p>']
    if learned is not None:
        values = learned['values']
        out.append(f'<p>Angelernt am {escape(_time(learned["wall"], zone, zones))} '
                   f'({escape(_number_text(values[0]))} °C, Heizkreis 1 {values[1]} %, Heizkreis 2 {values[2]} %).'
                   + (' <button type="button" class="zweit" data-aktion="zuruecksetzen">Zurücksetzen</button>'
                      if editable else '') + '</p>')
    if analysing:
        out.append('<p role="status">Wird ausgewertet ...</p>')
        if editable:
            out.append('<button type="button" class="zweit" data-aktion="abbrechen">Abbrechen</button>')
    elif entry is None:
        if editable:
            out.append(f'<label for="z{index}">Zeitraum</label> <select id="z{index}" name="zeitraum">')
            for days, text in LEARN_PERIOD_TEXT:
                out.append(f'<option value="{days}">{escape(text)}</option>')
            out.append('</select> <button type="button" data-aktion="anlernen">Raum anlernen</button>')
        elif learned is None:
            out.append('<p>Noch nicht angelernt.</p>')
    elif entry['state'] == 'recording':
        out.append(f'<p>Die Aufzeichnung läuft bis {escape(_time(entry["end"], zone, zones))}. Bis dahin meldet '
                   'dieser Raum keine Heizungsprobleme, Frost und Sensor werden aber weiter geprüft.</p>')
        if editable:
            out.append('<button type="button" class="zweit" data-aktion="abbrechen">Aufzeichnung abbrechen</button>')
    else:
        period = (f'Verlauf {escape(_time(entry["start"], zone, zones))} bis '
                  f'{escape(_time(entry["end"], zone, zones))}')
        if entry['state'] == 'proposal':
            value = entry['proposal']
            current = entry['current']
            out.append(f'<p><strong>Vorschlag</strong> ({period}): darf {escape(_number_text(value[0]))} °C kühler '
                       f'sein, Einfluss Heizkreis 1 {value[1]} %, Heizkreis 2 {value[2]} %'
                       + (f' (bisher {escape(_number_text(current[0]))} °C, {current[1]} %, {current[2]} %)'
                          if current else '') + '.</p>')
        else:
            out.append(f'<p><strong>Zu wenig Daten</strong> ({period}) für einen Vorschlag. Mindestens '
                       f'{learning.DAYS_MIN} Tage mit Tagbetrieb oder {learning.EVENTS_MIN} Aufheizvorgänge sind '
                       'nötig. Sie können stattdessen ab jetzt aufzeichnen.</p>')
        out.append('<ul class="beleg">' + ''.join(f'<li>{escape(line)}</li>' for line in entry['evidence']) + '</ul>')
        for warning in entry['warnings']:
            out.append(f'<p class="warnung">{escape(warning)}</p>')
        if editable and entry['state'] == 'proposal':
            if entry['warnings']:
                out.append(f'<label><input type="checkbox" name="geprueft" id="p{index}">Ich habe den Raum geprüft; '
                           'die Werte sind gewollt.</label>')
            out.append('<button type="button" data-aktion="uebernehmen">Übernehmen</button> '
                       '<button type="button" class="zweit" data-aktion="verwerfen">Verwerfen</button>')
        elif editable:
            out.append(f'<label for="a{index}">Aufzeichnen für</label> <select id="a{index}" name="aufzeichnung">')
            for days in learning.RECORD_PERIODS:
                out.append(f'<option value="{days}">{days} Tage</option>')
            out.append('</select> <button type="button" data-aktion="aufzeichnen">Ab jetzt aufzeichnen</button> '
                       '<button type="button" class="zweit" data-aktion="verwerfen">Verwerfen</button>')
    out.append('</div>')
    return ''.join(out)


def render_picker(status, rows, areas, stored, viewer, nonce, *, zone=None, zones=None, running=None,
                  learning_view=None):
    """The complete room page.

    rows: candidate rows (entity, name, value) or None; areas: entity -> area
    or None; stored: the app's stored options or None when unreadable;
    viewer: {'kind', 'name', 'token'}; running: the running HouseOptions.
    """
    escape = html.escape
    editable = viewer['kind'] == 'admin' and stored is not None and viewer.get('token')
    shown = stored if stored is not None else (options_of(running) if running is not None else HOUSE_DEFAULTS)
    chosen, flags = selection_of(shown)
    values = room_values(shown)
    stored_groups = selection_of(stored)[0] if stored is not None else {}
    candidates = {row[0]: row for row in rows} if rows is not None else {}
    areas_unavailable = areas is None and bool(rows)
    areas = areas or {}
    lines = ['<!doctype html><html lang="de"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>Raumsensoren</title><style nonce="{nonce}">{PAGE_STYLE}{PICKER_STYLE}</style></head><body>',
             status_html(status, zone, zones),
             '<h2 id="auswahl-titel">Raumsensoren auswählen</h2>']
    notes = [VIEWER_TEXT[viewer['kind']]]
    if viewer['kind'] == 'admin' and viewer.get('name'):
        notes[0] = f'Angemeldet als {viewer["name"]}. ' + notes[0]
    if stored is None:
        notes.append('Die gespeicherten Einstellungen der App sind gerade nicht lesbar. '
                     'Speichern ist deshalb nicht möglich. Angezeigt wird die laufende Auswahl.')
    elif running is not None and rules.parse_house_options(stored) != running:
        if editable:
            notes.append('Im Tab Konfiguration gespeicherte Änderungen sind noch nicht aktiv. Speichern hier '
                         'übernimmt die angezeigte Auswahl sofort, sonst gilt sie nach dem nächsten Neustart der App.')
        else:
            notes.append('Gespeicherte Änderungen der Auswahl sind noch nicht aktiv. Sie gelten nach dem '
                         'Speichern durch einen Administrator oder nach dem nächsten Neustart der App.')
    for note in notes:
        lines.append(f'<p class="hinweis">{escape(note)}</p>')
    learn_hint = learning_view is not None
    lines.append('<p>Jeder Sensor gehört höchstens zu einer Gruppe: Heizkreis 1, Heizkreis 2 oder Weitere Räume '
                 '(Räume ohne Heizkreis, etwa der Keller). Nach dem Speichern gilt die Auswahl sofort; die Wartezeiten '
                 'der Überwachung beginnen dann neu. Die Einstellungen bleiben im Tab Konfiguration der App sichtbar '
                 'und dort änderbar.</p>')
    lines.append(f'<p>{escape(SETTINGS_HELP)}</p>')
    if learn_hint:
        lines.append(f'<p>{escape(LEARN_HELP)}</p>')
    attributes = ''
    if editable:
        attributes = (f' data-token="{escape(viewer["token"])}" data-revision="{escape(revision(stored))}"')
    disabled = '' if editable else ' disabled'
    lines.append(f'<form id="auswahl" aria-labelledby="auswahl-titel" data-max="{rules.MAX_SENSORS}"{attributes}>')
    mode = shown.get('raumueberwachung_modus')
    lines.append('<fieldset class="wahl"><legend>Raumüberwachung</legend>')
    for value, text in (('beobachten', 'beobachten - Meldungen werden nur angezeigt'),
                        ('melden', 'melden - Meldungen zählen für den Gesamtzustand')):
        checked = ' checked' if mode == value else ''
        lines.append(f'<label><input type="radio" name="modus" value="{value}"{checked}{disabled}>{escape(text)}</label>')
    lines.append('</fieldset><fieldset><legend>Art der Heizkreise</legend><div class="zeile">')
    for index in (1, 2):
        current = shown.get(f'heizkreis_{index}_art')
        lines.append(f'<label for="art{index}">Heizkreis {index} ist</label><select id="art{index}"{disabled}>')
        for value, text in TYPE_CHOICES:
            selected = ' selected' if current == value else ''
            lines.append(f'<option value="{value}"{selected}>{escape(text)}</option>')
        lines.append('</select>')
    lines.append('</div></fieldset>')
    lines.append('<div id="suchfeld" hidden><label for="suche">Sensoren suchen (Name, Bereich oder Entität)</label>'
                 '<input id="suche" type="search" autocomplete="off" spellcheck="false">'
                 '<p id="treffer" role="status" aria-live="polite"></p></div>')
    if rows is None:
        lines.append('<p class="hinweis">Die Liste der Temperatursensoren von Home Assistant ist gerade nicht '
                     'verfügbar. Angezeigt werden nur die ausgewählten Sensoren.</p>')
    if areas_unavailable:
        lines.append('<p>Die Bereiche der Sensoren sind gerade nicht verfügbar.</p>')

    position = [0]

    def row_html(entity, name, value, missing):
        position[0] += 1
        index = position[0]
        group = chosen.get(entity, '')
        area = areas.get(entity, '')
        scope = 'weitere' if group == 'weitere' else ('kreis' if group else '')
        title = name or entity
        meta = [f'<code>{escape(entity)}</code>']
        if missing:
            meta.append('<span class="fehlt">nicht mehr in Home Assistant gefunden</span>')
        elif value:
            meta.append(f'<span>{escape(_value_text(value)[:64])}</span>')
        elif rows is None:
            meta.append('<span>Wert gerade unbekannt</span>')
        if area:
            meta.append(f'<span>Bereich: {escape(area)}</span>')
        active = ' aktiv' if group else ''
        out = [f'<fieldset class="sensor{active}" data-entity="{escape(entity)}" '
               f'data-suche="{escape(_search_text(name, entity, area))}">',
               f'<legend>{escape(title[:200])}</legend><p class="meta">{" ".join(meta)}</p><div class="zeile">',
               f'<label for="g{index}">Überwachung</label><select id="g{index}" name="gruppe"{disabled}>']
        for value_, text in GROUP_TEXT:
            selected = ' selected' if group == value_ else ''
            out.append(f'<option value="{value_}"{selected}>{escape(text)}</option>')
        out.append('</select>')
        # Free settings (rooms on a circuit, app 1.6.9): allowance and influence.
        current = values.get(entity, (0.0, 0, 0)) if scope == 'kreis' else (0.0, 0, 0)
        hidden = '' if scope == 'kreis' else ' hidden'
        out.append(f'<label data-gilt="kreis"{hidden}>{escape(ALLOWANCE_LABEL)} '
                   f'<input type="number" name="kuehler" min="0" max="10" step="0.5" inputmode="decimal" '
                   f'value="{_number_text(current[0]).replace(",", ".")}"{disabled}> °C</label>')
        for number, name in enumerate(INFLUENCE_FIELDS, 1):
            out.append(f'<label data-gilt="kreis"{hidden}>{escape(INFLUENCE_LABEL.format(index=number))} '
                       f'<input type="number" name="{name}" min="0" max="100" step="1" inputmode="numeric" '
                       f'value="{current[number]}"{disabled}> %</label>')
        for flag, _key, flag_scope, text in FLAGS:
            checked = ' checked' if entity in flags[flag] and flag_scope == scope else ''
            hidden = '' if flag_scope == scope else ' hidden'
            out.append(f'<label data-gilt="{flag_scope}"{hidden}><input type="checkbox" name="{flag}"'
                       f'{checked}{disabled}>{escape(text)}</label>')
        out.append('</div>')
        # Learning only for a room saved on a circuit (its history and circuit are known).
        if learning_view is not None and stored_groups.get(entity) in ('heizkreis_1', 'heizkreis_2'):
            out.append(learning_html(entity, index, learning_view(entity), editable, zone, zones))
        out.append('</fieldset>')
        return ''.join(out)

    configured = [entity for key in rules.LIST_KEYS[:3] for entity in _configured_lists(shown)[key]]
    configured = list(dict.fromkeys(configured))
    missing = [entity for entity in configured if rows is not None and entity not in candidates]
    if missing:
        lines.append('<h3>Ausgewählt, aber nicht mehr in Home Assistant</h3>'
                     '<p>Diese Sensoren gibt es in Home Assistant nicht mehr. Zum Entfernen "nicht überwachen" '
                     'wählen und speichern.</p>')
        for entity in missing:
            lines.append(row_html(entity, '', '', True))
    lines.append('<h3>Temperatursensoren</h3>')
    order = [entity for entity in configured if entity in candidates or rows is None]
    rest = sorted((entity for entity in candidates if entity not in chosen),
                  key=lambda entity: ((candidates[entity][1] or entity).casefold(), entity))
    for entity in order + rest:
        row = candidates.get(entity, (entity, '', ''))
        lines.append(row_html(entity, row[1], row[2], False))
    if not order and not rest:
        lines.append('<p>Home Assistant meldet keine Temperatursensoren.</p>')
    lines.append('<div class="aktion"><p id="anzahl"></p>')
    if editable:
        lines.append('<button id="speichern" type="submit">Speichern</button>')
    lines.append('<p id="meldung" role="status" aria-live="polite"></p>'
                 '<p id="fehler" role="alert" aria-live="assertive"></p></div></form>')
    lines.append(f'<script nonce="{nonce}">{SCRIPT}</script></body></html>')
    return ''.join(lines).encode('utf-8')


class RoomPicker:
    """GET renders the page; save() is the only write. One save at a time."""

    def __init__(self, monitor, *, token_source=default_token, api_factory=PickerApi, admin=None, tokens=None,
                 monotonic=time.monotonic, log=None, apply_wait=APPLY_WAIT_S, learning=None, learn_wait=20.0):
        self.monitor = monitor
        self.learning = learning
        self.learn_wait = learn_wait
        self.token_source, self.api_factory = token_source, api_factory
        self.monotonic = monotonic
        self.log_sink = log or (lambda line: print(line, flush=True))
        self.admin = admin or AdminCheck(token_source, monotonic=monotonic, log=self.log_sink)
        self.tokens = tokens or PageTokens(monotonic=monotonic)
        self.apply_wait = apply_wait
        self.save_lock = threading.Lock()
        self.area_lock = threading.Lock()
        self.area_cache = None
        self.logged = {}

    def _log(self, category):
        now = self.monotonic()
        if category not in LOG_CATEGORIES or (category in self.logged and now - self.logged[category] < 300):
            return
        self.logged[category] = now
        self.log_sink(_log_line(category))

    def _api(self):
        try:
            token = self.token_source()
        except Exception:
            return None
        return self.api_factory(token)

    def _stored(self, api):
        if api is None:
            return None
        try:
            return api.stored_options()
        except ApiError:
            self._log('options_unreadable')
            return None

    def _areas(self, api):
        now = self.monotonic()
        with self.area_lock:
            if self.area_cache is not None and now - self.area_cache[0] < PAGE_CANDIDATE_AGE_S:
                return self.area_cache[1]
            areas = None
            if api is not None:
                try:
                    areas = api.areas()
                except ApiError:
                    self._log('areas_unavailable')
            self.area_cache = (now, areas)
            return areas

    def page(self, user, nonce, prefix=None):
        kind, name = self.admin.lookup(user)
        api = self._api()
        stored = self._stored(api)
        rows = self.monitor._candidates(PAGE_CANDIDATE_AGE_S)
        areas = self._areas(api) if rows else None
        viewer = {'kind': kind, 'name': name, 'token': self.tokens.issue(user) if kind == 'admin' else None}
        learning_view = None
        if self.learning is not None and stored is not None:
            if kind == 'admin':
                # Writes and history reads only on an administrator's visit.
                self._learning_upkeep(stored, (user, prefix) if prefix else None)
            learning_view = self.learning.view
        return render_picker(self.monitor.status(), rows, areas, stored, viewer, nonce,
                             zone=self.monitor.zone, zones=self.monitor.zones, running=self.monitor.options,
                             learning_view=learning_view)

    def _learning_upkeep(self, stored, identity=None):
        """Forget rooms no longer on a circuit; analyse a recording that ended."""
        groups = {e: g for e, g in selection_of(stored)[0].items() if g != 'weitere'}
        self.learning.prune(set(groups))
        values = room_values(stored)
        for entity, entry in self.learning.due():
            home = 'hc1' if groups.get(entity) == 'heizkreis_1' else 'hc2'
            self.learning.start(entity, home, values[entity], wait=0, period=(entry['start'], entry['end']),
                                identity=identity)
            break

    def save(self, user, token, raw):
        """Return (HTTP status, {'ok', 'message'[, 'revision']}). Never raises for bad input."""
        if not self.save_lock.acquire(blocking=False):
            return 409, {'ok': False, 'message': 'Es wird gerade gespeichert. Bitte kurz warten.'}
        try:
            return self._save(user, token, raw)
        finally:
            self.save_lock.release()

    def _save(self, user, token, raw):
        def refuse(status, message, category='save_refused'):
            self._log(category)
            return status, {'ok': False, 'message': message}
        kind, _name = self.admin.lookup(user, refresh=True)
        if kind != 'admin':
            return refuse(403, REFUSED_TEXT[kind])
        if not self.tokens.valid(user, token):
            return refuse(403, 'Die Seite ist abgelaufen. Bitte neu laden. Es wurde nichts gespeichert.')
        try:
            request = parse_request(rules.strict_json(raw))
        except (ValueError, UnicodeError, RecursionError):
            return refuse(400, 'Die Anfrage ist ungültig. Es wurde nichts gespeichert.')
        except SelectionError as error:
            return refuse(400, error.args[0] + ' Es wurde nichts gespeichert.')
        api = self._api()
        stored = self._stored(api)
        if stored is None:
            return refuse(503, 'Die gespeicherten Einstellungen der App sind gerade nicht lesbar. '
                               'Es wurde nichts gespeichert.')
        if revision(stored) != request['revision']:
            return refuse(409, 'Die Einstellungen wurden inzwischen geändert. Bitte die Seite neu laden. '
                               'Es wurde nichts gespeichert.')
        rows = self.monitor._candidates()
        known = configured_entities(stored) | ({row[0] for row in rows} if rows is not None else set())
        for row in request['sensoren']:
            if row['entity_id'] not in known:
                message = (f'Unbekannter Sensor {row["entity_id"]}: er ist kein Temperatursensor von Home '
                           'Assistant. Es wurde nichts gespeichert.')
                if rows is None:
                    message = ('Die Liste der Temperatursensoren ist gerade nicht verfügbar; neue Sensoren '
                               'können deshalb nicht gespeichert werden. Es wurde nichts gespeichert.')
                return refuse(400, message)
        return self._write(stored, build_house(stored, request), api, refuse)

    def _write(self, stored, house, api, refuse):
        """Save the room options (when changed), confirm, hand them to the monitor."""
        merged = {**stored, **house}
        parsed = rules.parse_house_options(merged)
        if not parsed.valid:
            return refuse(400, 'Die Auswahl verletzt die Regeln der Raumüberwachung. Es wurde nichts gespeichert.')
        if house_part(stored) != house:
            try:
                api.save_options(merged)
            except SaveRefused as error:
                return refuse(502, f'Home Assistant hat die Einstellungen nicht gespeichert (Fehler {error.status}). '
                                   'Es wurde nichts geändert.')
            except ApiError:
                return refuse(504, 'Home Assistant antwortet nicht. Ob gespeichert wurde, ist unklar. '
                                   'Bitte die Seite neu laden und prüfen.', 'save_unconfirmed')
            confirmed = self._stored(api)
            if confirmed is None or house_part(confirmed) != house:
                return refuse(502, 'Home Assistant hat das Speichern gemeldet, die Prüfung ist aber '
                                   'fehlgeschlagen. Bitte die Seite neu laden und prüfen.', 'save_unconfirmed')
            parsed = rules.parse_house_options(confirmed)
            message = 'Die Einstellungen sind gespeichert. '
        else:
            confirmed = stored
            message = 'Es gibt keine Änderung zu speichern. '
        if parsed == self.monitor.options and self.monitor.pending is None:
            applied, message = True, message + 'Die Überwachung verwendet diese Auswahl bereits.'
        else:
            applied = self.monitor.request_reconfigure(parsed).wait(self.apply_wait)
            message += ('Die Überwachung verwendet die neue Auswahl ab sofort; ihre Wartezeiten beginnen neu.'
                        if applied else 'Die Überwachung übernimmt die neue Auswahl in wenigen Sekunden.')
        self._log('saved')
        return 200, {'ok': True, 'message': message, 'revision': revision(confirmed)}

    # -- room learning (app 1.6.9)
    def learn(self, user, token, raw, prefix=None):
        """One learning action. Return (HTTP status, {'ok', 'message'}). Never raises for bad input.
        prefix: the Ingress path of the request (the VM history is read as this user)."""
        if not self.save_lock.acquire(blocking=False):
            return 409, {'ok': False, 'message': 'Es wird gerade gespeichert. Bitte kurz warten.'}
        try:
            return self._learn(user, token, raw, prefix)
        finally:
            self.save_lock.release()

    def _learn(self, user, token, raw, prefix=None):
        def refuse(status, message, category='learning'):
            self._log(category)
            return status, {'ok': False, 'message': message}
        if self.learning is None:
            return refuse(503, 'Anlernen ist gerade nicht verfügbar. Es wurde nichts geändert.')
        kind, _name = self.admin.lookup(user, refresh=True)
        if kind != 'admin':
            return refuse(403, REFUSED_TEXT[kind].replace('gespeichert', 'geändert'))
        if not self.tokens.valid(user, token):
            return refuse(403, 'Die Seite ist abgelaufen. Bitte neu laden. Es wurde nichts geändert.')
        try:
            document = rules.strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            document = None
        if (not isinstance(document, dict) or set(document) != LEARN_KEYS or document['aktion'] not in LEARN_ACTIONS
                or not isinstance(document['entity_id'], str) or not rules.ENTITY_PATTERN.fullmatch(document['entity_id'])
                or type(document['geprueft']) is not bool
                or not (document['tage'] is None or type(document['tage']) is int)
                or not isinstance(document['revision'], str) or not REVISION_PATTERN.fullmatch(document['revision'])):
            return refuse(400, 'Die Anfrage ist ungültig. Es wurde nichts geändert.')
        action, entity, days = document['aktion'], document['entity_id'], document['tage']
        if action == 'abbrechen':
            status, message = self.learning.cancel(entity)
            return status, {'ok': True, 'message': message}
        if action == 'verwerfen':
            status, message = self.learning.discard(entity)
            return status, {'ok': status == 200, 'message': message}
        api = self._api()
        stored = self._stored(api)
        if stored is None:
            return refuse(503, 'Die gespeicherten Einstellungen der App sind gerade nicht lesbar. '
                               'Es wurde nichts geändert.')
        groups = {e: g for e, g in selection_of(stored)[0].items() if g != 'weitere'}
        if entity not in groups:
            return refuse(400, 'Nur ein gespeicherter Raum an einem Heizkreis kann angelernt werden. '
                               'Bitte zuerst speichern.')
        home = 'hc1' if groups[entity] == 'heizkreis_1' else 'hc2'
        current = room_values(stored)[entity]
        if action == 'anlernen':
            if days not in (0,) + learning.PERIODS:
                return refuse(400, 'Der Zeitraum ist ungültig. Es wurde nichts geändert.')
            status, message = self.learning.start(entity, home, current, days=days or learning.MAX_DAYS,
                                                  wait=self.learn_wait, identity=(user, prefix) if prefix else None)
            return status, {'ok': status in (200, 202), 'message': message}
        if action == 'aufzeichnen':
            if days not in learning.RECORD_PERIODS:
                return refuse(400, 'Die Dauer der Aufzeichnung ist ungültig. Es wurde nichts geändert.')
            status, message = self.learning.record(entity, days)
            return status, {'ok': status == 200, 'message': message}
        # uebernehmen and zuruecksetzen write the room options.
        if revision(stored) != document['revision']:
            return refuse(409, 'Die Einstellungen wurden inzwischen geändert. Bitte die Seite neu laden. '
                               'Es wurde nichts geändert.')
        if action == 'zuruecksetzen':
            values, group = default_values(groups[entity]), groups[entity]
        else:
            entry = self.learning.proposal(entity)
            if entry is None:
                return refuse(409, 'Es gibt keinen Vorschlag zum Übernehmen. Bitte die Seite neu laden.')
            if self.learning.clock() - entry['made'] > learning.PROPOSAL_MAX_AGE_S:
                return refuse(409, 'Der Vorschlag ist älter als 7 Tage. Bitte neu anlernen. Es wurde nichts geändert.')
            if entry['home'] != home or entry['current'] is None or tuple(entry['current']) != tuple(current):
                return refuse(409, 'Der Raum wurde seit dem Vorschlag geändert. Bitte neu anlernen. '
                                   'Es wurde nichts geändert.')
            if entry['warnings'] and not document['geprueft']:
                return refuse(400, 'Der Vorschlag hat eine Warnung. Bitte erst bestätigen, dass Sie den Raum '
                                   'geprüft haben. Es wurde nichts geändert.')
            values = tuple(entry['proposal'])
            own, other = (values[1], values[2]) if home == 'hc1' else (values[2], values[1])
            group = groups[entity] if own >= other else ('heizkreis_2' if home == 'hc1' else 'heizkreis_1')
        status, answer = self._write(stored, with_room(stored, entity, values, group), api,
                                     lambda code, text, category='save_refused': refuse(code, text, category))
        if status == 200:
            if action == 'zuruecksetzen':
                self.learning.forget(entity)
                answer['message'] = 'Der Raum ist auf die Standardwerte zurückgesetzt. ' + answer['message']
            else:
                self.learning.accepted(entity, values, entry['made'])
                answer['message'] = 'Der Vorschlag ist übernommen. ' + answer['message']
        return status, answer
