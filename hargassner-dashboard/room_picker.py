"""Room page with the sensor picker (app 1.6.1).

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
from ha_websocket import WebSocketError, list_users
from house_monitor import (ApiError, CoreApi, CANDIDATE_CACHE_S, PAGE_STYLE, default_token, status_html)

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
HOUSE_KEYS = ('raumueberwachung_modus', 'heizkreis_1_art', 'heizkreis_2_art') + rules.LIST_KEYS
HOUSE_DEFAULTS = {'raumueberwachung_modus': 'beobachten', 'heizkreis_1_art': 'automatisch',
                  'heizkreis_2_art': 'automatisch', **{key: [] for key in rules.LIST_KEYS}}
GROUPS = {'heizkreis_1': 'heizkreis_1_sensoren', 'heizkreis_2': 'heizkreis_2_sensoren', 'weitere': 'haus_sensoren'}
GROUP_TEXT = (('', 'nicht überwachen'), ('heizkreis_1', 'Heizkreis 1'), ('heizkreis_2', 'Heizkreis 2'),
              ('weitere', 'Weitere Räume'))
FLAGS = (('kuehler', 'kuehlere_raeume', 'kreis', 'darf kühler sein'),
         ('ofen', 'ohne_ueberwaermung', 'kreis', 'Ofen oder viel Sonne'),
         ('frost', 'haus_frost_sensoren', 'weitere', 'Frostwarnung'))
TYPE_CHOICES = (('automatisch', 'automatisch (aus der Steuerung)'), ('heizkoerper', 'Heizkörper'),
                ('fussboden', 'Fußboden'))
BODY_KEYS = frozenset(('revision', 'modus', 'heizkreis_1_art', 'heizkreis_2_art', 'sensoren'))
ROW_KEYS = frozenset(('entity_id', 'gruppe', 'kuehler', 'ofen', 'frost'))
REVISION_PATTERN = re.compile(r'[0-9a-f]{64}')
LOG_CATEGORIES = frozenset(('saved', 'save_refused', 'save_unconfirmed', 'admin_lookup_failed',
                            'options_unreadable', 'areas_unavailable'))

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

    One `config/auth/list` answer is cached for ADMIN_TTL_S; a failed lookup
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

    def lookup(self, user):
        """Return (kind, name): kind is admin, user, unknown_user or unavailable."""
        with self.lock:
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


def options_of(parsed):
    """Lists of a running HouseOptions, for display when the stored ones are unreadable."""
    if not parsed.valid:
        return dict(HOUSE_DEFAULTS, raumueberwachung_modus=parsed.mode)
    names = {value: key for key, value in rules.OPTION_TYPES.items()}
    return {'raumueberwachung_modus': parsed.mode, 'heizkreis_1_art': names[parsed.option_types[0]],
            'heizkreis_2_art': names[parsed.option_types[1]], 'heizkreis_1_sensoren': list(parsed.hc1),
            'heizkreis_2_sensoren': list(parsed.hc2), 'haus_sensoren': list(parsed.haus),
            'kuehlere_raeume': sorted(parsed.cooler), 'ohne_ueberwaermung': sorted(parsed.no_warm),
            'haus_frost_sensoren': sorted(parsed.frost)}


def parse_request(document):
    """Strict shape of a save request; raises SelectionError with German text."""
    if not isinstance(document, dict) or set(document) != BODY_KEYS:
        raise SelectionError('Ungültige Anfrage.')
    if not isinstance(document['revision'], str) or not REVISION_PATTERN.fullmatch(document['revision']):
        raise SelectionError('Ungültige Anfrage.')
    if document['modus'] not in rules.MODES:
        raise SelectionError('Unbekannter Modus der Raumüberwachung.')
    for key in ('heizkreis_1_art', 'heizkreis_2_art'):
        if not isinstance(document[key], str) or document[key] not in rules.OPTION_TYPES:
            raise SelectionError('Unbekannte Art des Heizkreises.')
    rows = document['sensoren']
    if not isinstance(rows, list):
        raise SelectionError('Ungültige Anfrage.')
    if len(rows) > rules.MAX_SENSORS:
        raise SelectionError(f'Höchstens {rules.MAX_SENSORS} Sensoren können überwacht werden.')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise SelectionError('Ungültige Anfrage.')
        entity = row['entity_id']
        if not isinstance(entity, str) or not rules.ENTITY_PATTERN.fullmatch(entity):
            raise SelectionError('Ungültiger Sensor.')
        if row['gruppe'] not in GROUPS:
            raise SelectionError('Unbekannte Zuordnung eines Sensors.')
        if any(type(row[name]) is not bool for name, _key, _scope, _text in FLAGS):
            raise SelectionError('Ungültige Anfrage.')
        if entity in seen:
            raise SelectionError('Ein Sensor kann nur einer Gruppe zugeordnet werden.')
        seen.add(entity)
        circuit = row['gruppe'] != 'weitere'
        if not circuit and (row['kuehler'] or row['ofen']):
            raise SelectionError('"darf kühler sein" und "Ofen oder viel Sonne" gelten nur für Räume an einem '
                                 'Heizkreis.')
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
    """Map the page's rows to the nine room options."""
    previous = _configured_lists(stored)
    rows = request['sensoren']
    house = {'raumueberwachung_modus': request['modus'], 'heizkreis_1_art': request['heizkreis_1_art'],
             'heizkreis_2_art': request['heizkreis_2_art']}
    for group, key in GROUPS.items():
        house[key] = _ordered(previous[key], [row['entity_id'] for row in rows if row['gruppe'] == group])
    for name, key, _scope, _text in FLAGS:
        house[key] = _ordered(previous[key], [row['entity_id'] for row in rows if row[name]])
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
  function sync(row) {
    var group = row.querySelector('select').value;
    Array.prototype.forEach.call(row.querySelectorAll('[data-gilt]'), function (label) {
      var on = label.getAttribute('data-gilt') === scope(group);
      label.hidden = !on;
      if (!on) { label.querySelector('input').checked = false; }
    });
    row.classList.toggle('aktiv', group !== '');
  }
  function selected() {
    return rows.filter(function (row) { return row.querySelector('select').value !== ''; });
  }
  function counted() {
    var n = selected().length;
    count.textContent = 'Ausgewählt: ' + n + ' von höchstens ' + max + ' Sensoren';
    count.classList.toggle('zuviel', n > max);
  }
  function filter() {
    var words = norm(search.value).split(/\s+/).filter(Boolean);
    var shown = 0;
    rows.forEach(function (row) {
      var text = norm(row.getAttribute('data-suche'));
      var match = words.every(function (word) { return text.indexOf(word) >= 0; });
      row.hidden = !match;
      if (match) { shown += 1; }
    });
    hits.textContent = shown + ' von ' + rows.length + ' Sensoren angezeigt';
  }
  function show(success, text) {
    ok.textContent = success ? text : '';
    bad.textContent = success ? '' : text;
  }
  rows.forEach(function (row) {
    row.querySelector('select').addEventListener('change', function () { sync(row); counted(); });
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
          gruppe: row.querySelector('select').value,
          kuehler: row.querySelector('input[name="kuehler"]').checked,
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
        show(false, 'Speichern nicht möglich (Fehler ' + result.status + '). Es wurde nichts gespeichert.');
      }
    }).catch(function () {
      show(false, 'Keine Verbindung zur App. Ob gespeichert wurde, ist unklar. Bitte die Seite neu laden.');
    }).then(function () { button.disabled = false; });
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
    ':focus-visible{outline:3px solid var(--accent);outline-offset:2px}'
    '.hinweis{border-left:4px solid var(--accent);padding:4px 10px;background:var(--card)}'
    '#meldung{color:var(--ok);font-weight:bold}#fehler{color:var(--bad);font-weight:bold}'
    '.zuviel{color:var(--bad);font-weight:bold}.fehlt{color:var(--bad)}'
    '.aktion{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:8px 0;'
    'margin-top:8px}[hidden]{display:none!important}')


def render_picker(status, rows, areas, stored, viewer, nonce, *, zone=None, zones=None, running=None):
    """The complete room page.

    rows: candidate rows (entity, name, value) or None; areas: entity -> area
    or None; stored: the app's stored options or None when unreadable;
    viewer: {'kind', 'name', 'token'}; running: the running HouseOptions.
    """
    escape = html.escape
    editable = viewer['kind'] == 'admin' and stored is not None and viewer.get('token')
    shown = stored if stored is not None else (options_of(running) if running is not None else HOUSE_DEFAULTS)
    chosen, flags = selection_of(shown)
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
    lines.append('<p>Jeder Sensor gehört höchstens zu einer Gruppe: Heizkreis 1, Heizkreis 2 oder Weitere Räume '
                 '(Räume ohne Heizkreis, z. B. Keller). Nach dem Speichern gilt die Auswahl sofort; die Wartezeiten '
                 'der Überwachung beginnen dann neu. Die Einstellungen bleiben im Tab Konfiguration der App sichtbar '
                 'und dort änderbar.</p>')
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
        for flag, _key, flag_scope, text in FLAGS:
            checked = ' checked' if entity in flags[flag] and flag_scope == scope else ''
            hidden = '' if flag_scope == scope else ' hidden'
            out.append(f'<label data-gilt="{flag_scope}"{hidden}><input type="checkbox" name="{flag}"'
                       f'{checked}{disabled}>{escape(text)}</label>')
        out.append('</div></fieldset>')
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
                 monotonic=time.monotonic, log=None, apply_wait=APPLY_WAIT_S):
        self.monitor = monitor
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

    def page(self, user, nonce):
        kind, name = self.admin.lookup(user)
        api = self._api()
        stored = self._stored(api)
        rows = self.monitor._candidates(PAGE_CANDIDATE_AGE_S)
        areas = self._areas(api) if rows else None
        viewer = {'kind': kind, 'name': name, 'token': self.tokens.issue(user) if kind == 'admin' else None}
        return render_picker(self.monitor.status(), rows, areas, stored, viewer, nonce,
                             zone=self.monitor.zone, zones=self.monitor.zones, running=self.monitor.options)

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
        kind, _name = self.admin.lookup(user)
        if kind != 'admin':
            return refuse(403, REFUSED_TEXT[kind])
        if not self.tokens.valid(user, token):
            return refuse(403, 'Die Seite ist abgelaufen. Bitte neu laden. Es wurde nichts gespeichert.')
        try:
            request = parse_request(rules.strict_json(raw))
        except (ValueError, UnicodeError, RecursionError):
            return refuse(400, 'Ungültige Anfrage. Es wurde nichts gespeichert.')
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
        house = build_house(stored, request)
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
                return refuse(504, 'Keine Antwort von Home Assistant. Ob gespeichert wurde, ist unklar. '
                                   'Bitte die Seite neu laden und prüfen.', 'save_unconfirmed')
            confirmed = self._stored(api)
            if confirmed is None or house_part(confirmed) != house:
                return refuse(502, 'Home Assistant hat das Speichern gemeldet, die Prüfung ist aber '
                                   'fehlgeschlagen. Bitte die Seite neu laden und prüfen.', 'save_unconfirmed')
            parsed = rules.parse_house_options(confirmed)
            message = 'Gespeichert. '
        else:
            confirmed = stored
            message = 'Keine Änderung zu speichern. '
        if parsed == self.monitor.options and self.monitor.pending is None:
            applied, message = True, message + 'Die Überwachung verwendet diese Auswahl bereits.'
        else:
            applied = self.monitor.request_reconfigure(parsed).wait(self.apply_wait)
            message += ('Die Überwachung verwendet die neue Auswahl ab sofort; ihre Wartezeiten beginnen neu.'
                        if applied else 'Die Überwachung übernimmt die neue Auswahl in wenigen Sekunden.')
        self._log('saved')
        return 200, {'ok': True, 'message': message, 'revision': revision(confirmed)}
