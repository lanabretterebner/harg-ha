"""Room page picker: rendering, mapping, validation, admin gate, CSRF, saving and reload (synthetic, loopback only)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import re
import threading
import unittest
from unittest.mock import patch

import ha_websocket as ws
import house_monitor as hm
import house_rules as r
import ingress_proxy
import room_picker as rp
import run_app
import test_ha_websocket as wstest
import test_house_transport as transport

PREFIX = '/api/hassio_ingress/synthetic_test_path_1234'
PAGE = 'http://ha.example:8123' + PREFIX + '/api/local-monitor/raumsensoren'
ADMIN, USER, SYSTEM, INACTIVE, OWNER = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32, 'e' * 32
USERS = [
    {'id': ADMIN, 'name': 'Lena Admin', 'is_owner': False, 'is_active': True, 'system_generated': False,
     'group_ids': ['system-admin']},
    {'id': USER, 'name': 'Kind', 'is_owner': False, 'is_active': True, 'system_generated': False,
     'group_ids': ['system-users']},
    {'id': SYSTEM, 'name': 'Supervisor', 'is_owner': False, 'is_active': True, 'system_generated': True,
     'group_ids': ['system-admin']},
    {'id': INACTIVE, 'name': 'Alt', 'is_owner': False, 'is_active': False, 'system_generated': False,
     'group_ids': ['system-admin']},
    {'id': OWNER, 'name': 'Besitzer', 'is_owner': True, 'is_active': True, 'system_generated': False,
     'group_ids': []},
]
SECRET_OPTIONS = {'upstream_url': 'https://dashboard.example', 'ingress_token': 'i' * 43,
                  'monitor_enabled': True, 'monitor_token': 'm' * 43, 'monitor_poll_interval': 45,
                  'upstream_connect_host': '192.0.2.10'}
STATES = [
    {'entity_id': 'sensor.wohnzimmer', 'state': '21.5',
     'attributes': {'device_class': 'temperature', 'unit_of_measurement': '°C', 'friendly_name': 'Wohnzimmer <b>'}},
    {'entity_id': 'sensor.kueche', 'state': '20.0',
     'attributes': {'device_class': 'temperature', 'unit_of_measurement': '°C', 'friendly_name': 'Küche'}},
    {'entity_id': 'sensor.bad', 'state': '23.0',
     'attributes': {'device_class': 'temperature', 'unit_of_measurement': '°C', 'friendly_name': 'Bad'}},
    {'entity_id': 'sensor.keller', 'state': '12.0',
     'attributes': {'unit_of_measurement': '°C', 'friendly_name': 'Keller'}},
    {'entity_id': 'sensor.feuchte', 'state': '40', 'attributes': {'unit_of_measurement': '%'}},
]


def stored_options(**house):
    return {**SECRET_OPTIONS, **rp.HOUSE_DEFAULTS, **house}


class FakePickerApi:
    def __init__(self, world):
        self.world = world

    def stored_options(self):
        self.world.calls.append('info')
        if self.world.info_fails:
            raise hm.ApiError('unavailable')
        if self.world.readback_differs and 'save' in self.world.calls:
            return {**self.world.stored, 'haus_sensoren': ['sensor.keller']}
        return json.loads(json.dumps(self.world.stored))

    def save_options(self, options):
        self.world.calls.append('save')
        self.world.posted.append(json.loads(json.dumps(options)))
        if self.world.save_status is not None:
            raise rp.SaveRefused(self.world.save_status)
        if self.world.save_timeout:
            raise hm.ApiError('deadline')
        self.world.stored = json.loads(json.dumps(options))

    def areas(self):
        self.world.calls.append('areas')
        if self.world.areas is None:
            raise hm.ApiError('unavailable')
        return dict(self.world.areas)


class PickerWorld(transport.World):
    def __init__(self, house=None, states=STATES, users=USERS):
        stored = stored_options(**(house or {}))
        super().__init__(options=stored, values={e: 21.0 for e in ('sensor.wohnzimmer', 'sensor.kueche',
                                                                   'sensor.bad', 'sensor.keller', 'sensor.alt')})
        self.all_states = states
        self.stored = stored
        self.posted = []
        self.info_fails = self.save_timeout = self.readback_differs = False
        self.save_status = None
        self.areas = {'sensor.wohnzimmer': 'Wohnzimmer', 'sensor.keller': 'Keller'}
        self.users = users
        self.user_lookups = 0
        self.users_fail = False
        self.picker_logs = []

        def users_source(_token):
            self.user_lookups += 1
            if self.users_fail:
                raise ws.WebSocketError('timeout')
            return self.users
        mono = lambda: self.clock.mono   # noqa: E731
        self.admin = rp.AdminCheck(lambda: 'synthetic-supervisor-token', users=users_source, monotonic=mono,
                                   log=self.picker_logs.append)
        self.picker = rp.RoomPicker(self.monitor, token_source=lambda: 'synthetic-supervisor-token',
                                    api_factory=lambda _token: FakePickerApi(self), admin=self.admin,
                                    monotonic=mono, log=self.picker_logs.append, apply_wait=0)

    def page(self, user=ADMIN):
        return self.picker.page(user, 'synthetic-nonce').decode()

    def token(self, user=ADMIN):
        return re.search(r'data-token="([^"]+)"', self.page(user)).group(1)

    def body(self, rows, *, modus='beobachten', art1='automatisch', art2='automatisch', revision=None, **extra):
        document = {'revision': revision or rp.revision(self.stored), 'modus': modus, 'heizkreis_1_art': art1,
                    'heizkreis_2_art': art2, 'sensoren': rows, **extra}
        return json.dumps(document).encode()

    def save(self, rows, user=ADMIN, token=None, **kwargs):
        token = self.token() if token is None else token
        return self.picker.save(user, token, self.body(rows, **kwargs))


def row(entity, gruppe, kuehler=False, ofen=False, frost=False):
    return {'entity_id': entity, 'gruppe': gruppe, 'kuehler': kuehler, 'ofen': ofen, 'frost': frost}


class RenderTests(unittest.TestCase):
    def test_lists_candidates_with_name_value_area_and_search_markup(self):
        world = PickerWorld()
        page = world.page()
        for entity in ('sensor.wohnzimmer', 'sensor.kueche', 'sensor.bad', 'sensor.keller'):
            self.assertIn(f'data-entity="{entity}"', page)
        self.assertNotIn('sensor.feuchte', page)
        self.assertIn('Wohnzimmer &lt;b&gt;', page)
        self.assertNotIn('<b>', page)
        self.assertIn('21,5 °C', page)
        self.assertIn('Bereich: Keller', page)
        self.assertIn('data-suche="kuche sensor.kueche"', page)    # accents folded for the search
        self.assertIn('<label for="suche">', page)
        self.assertIn('<input id="suche" type="search"', page)
        self.assertIn('id="treffer" role="status" aria-live="polite"', page)
        self.assertIn('id="fehler" role="alert"', page)
        self.assertIn('<style nonce="synthetic-nonce">', page)
        self.assertIn('<script nonce="synthetic-nonce">', page)
        self.assertNotIn('<script>', page)
        self.assertNotIn('style="', page)
        self.assertNotRegex(page, r'(?:src|href)=')                  # nothing external
        self.assertNotRegex(page, '[‐-―]')                 # ordinary hyphens only
        for value, text in rp.GROUP_TEXT:
            self.assertRegex(page, f'<option value="{value}"(?: selected)?>{text}</option>')
        self.assertIn('Noch keine Daten von der VM empfangen', page)
        self.assertIn('Minuten ohne gültigen Sollwert', page)
        for secret in ('i' * 43, 'm' * 43, 'synthetic-supervisor-token', '192.0.2.10'):
            self.assertNotIn(secret, page)

    def test_configured_selection_flags_and_missing_sensor_stay_visible(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.wohnzimmer', 'sensor.alt'],
                             'heizkreis_2_sensoren': ['sensor.bad'], 'haus_sensoren': ['sensor.keller'],
                             'kuehlere_raeume': ['sensor.bad'], 'ohne_ueberwaermung': ['sensor.wohnzimmer'],
                             'haus_frost_sensoren': ['sensor.keller'], 'raumueberwachung_modus': 'melden',
                             'heizkreis_2_art': 'fussboden'})
        page = world.page()
        self.assertIn('Ausgewählt, aber nicht mehr in Home Assistant', page)
        alt = page[page.index('data-entity="sensor.alt"'):]
        alt = alt[:alt.index('</fieldset>')]
        self.assertIn('nicht mehr in Home Assistant gefunden', alt)
        self.assertIn('<option value="heizkreis_1" selected>', alt)
        # Configured sensors come first, in their option order.
        order = [m for m in re.findall(r'data-entity="([^"]+)"', page)]
        self.assertEqual(order[:5], ['sensor.alt', 'sensor.wohnzimmer', 'sensor.bad', 'sensor.keller', 'sensor.kueche'])

        def block(entity):
            part = page[page.index(f'data-entity="{entity}"'):]
            return part[:part.index('</fieldset>')]
        self.assertIn('<input type="checkbox" name="ofen" checked>', block('sensor.wohnzimmer'))
        self.assertIn('<input type="checkbox" name="kuehler" checked>', block('sensor.bad'))
        self.assertIn('<label data-gilt="weitere" hidden>', block('sensor.bad'))
        self.assertIn('<input type="checkbox" name="frost" checked>', block('sensor.keller'))
        self.assertIn('<label data-gilt="kreis" hidden>', block('sensor.keller'))
        self.assertIn('<label data-gilt="kreis" hidden>', block('sensor.kueche'))
        self.assertIn('<label data-gilt="weitere" hidden>', block('sensor.kueche'))
        self.assertIn('value="melden" checked', page)
        art2 = page[page.index('id="art2"'):]
        self.assertIn('<option value="fussboden" selected>', art2[:art2.index('</select>')])

    def test_admin_sees_controls_and_token_others_read_only(self):
        world = PickerWorld()
        page = world.page(ADMIN)
        self.assertIn('Angemeldet als Lena Admin.', page)
        self.assertRegex(page, r'data-token="[0-9]+\.[0-9a-f]{64}"')
        self.assertRegex(page, r'data-revision="[0-9a-f]{64}"')
        self.assertIn('<button id="speichern" type="submit">Speichern</button>', page)
        self.assertNotIn(' disabled', page)
        cases = {USER: 'Nur Administratoren von Home Assistant', SYSTEM: 'Nur Administratoren',
                 INACTIVE: 'Nur Administratoren', 'f' * 32: 'nicht bekannt'}
        for user, text in cases.items():
            with self.subTest(user=user):
                page = world.page(user)
                self.assertIn(text, page)
                self.assertNotIn('data-token="', page)
                self.assertNotIn('id="speichern"', page)
                self.assertIn('<select id="g1" name="gruppe" disabled>', page)
                self.assertIn('data-entity="sensor.wohnzimmer"', page)    # still visible
        self.assertIn('Angemeldet als Besitzer.', world.page(OWNER))
        self.assertEqual(world.posted, [])

    def test_lookup_failure_is_view_only_and_retried_later(self):
        world = PickerWorld()
        world.users_fail = True
        page = world.page(ADMIN)
        self.assertIn('konnte gerade nicht geprüft werden', page)
        self.assertNotIn('data-token="', page)
        world.page(ADMIN)
        self.assertEqual(world.user_lookups, 1)          # no lookup storm
        world.clock.advance(rp.ADMIN_RETRY_S)
        world.users_fail = False
        self.assertIn('data-token="', world.page(ADMIN))
        self.assertEqual(world.picker_logs, ['Room page: admin_lookup_failed.'])
        world.page(ADMIN)
        self.assertEqual(world.user_lookups, 2)          # cached for a minute
        world.clock.advance(rp.ADMIN_TTL_S)
        world.page(ADMIN)
        self.assertEqual(world.user_lookups, 3)

    def test_unreadable_options_and_states(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.wohnzimmer']})
        world.info_fails = True
        page = world.page()
        self.assertIn('Speichern ist deshalb nicht möglich', page)
        self.assertNotIn('data-token="', page)
        self.assertIn('<option value="heizkreis_1" selected>', page)   # the running selection is shown
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.wohnzimmer']}, states=[])
        world.all_states = None
        with patch.object(transport.FakeApi, 'states', side_effect=hm.ApiError('unavailable')):
            page = world.page()
        self.assertIn('gerade nicht verfügbar. Angezeigt werden nur die ausgewählten Sensoren.', page)
        self.assertIn('Wert gerade unbekannt', page)
        self.assertNotIn('nicht mehr in Home Assistant gefunden', page)
        world = PickerWorld()
        world.areas = None
        self.assertIn('Die Bereiche der Sensoren sind gerade nicht verfügbar.', world.page())

    def test_configuration_tab_changes_not_yet_active_are_named(self):
        world = PickerWorld()
        self.assertNotIn('noch nicht aktiv', world.page())
        world.stored['heizkreis_1_sensoren'] = ['sensor.kueche']
        self.assertIn('Im Tab Konfiguration gespeicherte Änderungen sind noch nicht aktiv.', world.page())
        viewer = world.page(USER)
        self.assertIn('Gespeicherte Änderungen der Auswahl sind noch nicht aktiv.', viewer)
        self.assertNotIn('Speichern hier', viewer)

    def test_get_never_writes(self):
        world = PickerWorld()
        for user in (ADMIN, USER, 'f' * 32):
            world.page(user)
        self.assertNotIn('save', world.calls)
        self.assertEqual(world.posted, [])
        self.assertIsNone(world.monitor.pending)


class MappingTests(unittest.TestCase):
    def test_each_choice_and_flag_maps_to_its_list(self):
        world = PickerWorld()
        status, document = world.save([
            row('sensor.wohnzimmer', 'heizkreis_1', ofen=True), row('sensor.kueche', 'heizkreis_1', kuehler=True),
            row('sensor.bad', 'heizkreis_2', kuehler=True, ofen=True), row('sensor.keller', 'weitere', frost=True)],
            modus='melden', art1='heizkoerper', art2='fussboden')
        self.assertEqual((status, document['ok']), (200, True), document)
        saved = world.posted[-1]
        self.assertEqual(rp.house_part(saved), {
            'raumueberwachung_modus': 'melden', 'heizkreis_1_art': 'heizkoerper', 'heizkreis_2_art': 'fussboden',
            'heizkreis_1_sensoren': ['sensor.wohnzimmer', 'sensor.kueche'], 'heizkreis_2_sensoren': ['sensor.bad'],
            'haus_sensoren': ['sensor.keller'], 'kuehlere_raeume': ['sensor.kueche', 'sensor.bad'],
            'ohne_ueberwaermung': ['sensor.wohnzimmer', 'sensor.bad'], 'haus_frost_sensoren': ['sensor.keller']})
        self.assertTrue(r.parse_house_options(saved).valid)

    def test_merge_keeps_every_other_option(self):
        world = PickerWorld()
        world.stored['future_option'] = {'nested': [1, 2]}
        world.save([row('sensor.kueche', 'weitere')])
        saved = world.posted[-1]
        for key, value in {**SECRET_OPTIONS, 'future_option': {'nested': [1, 2]}}.items():
            self.assertEqual(saved[key], value, key)
        self.assertEqual(set(saved), set(SECRET_OPTIONS) | set(rp.HOUSE_KEYS) | {'future_option'})

    def test_stored_order_is_kept_and_new_sensors_appended(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.kueche', 'sensor.wohnzimmer']})
        world.save([row('sensor.bad', 'heizkreis_1'), row('sensor.wohnzimmer', 'heizkreis_1'),
                    row('sensor.kueche', 'heizkreis_1')])
        self.assertEqual(world.posted[-1]['heizkreis_1_sensoren'], ['sensor.kueche', 'sensor.wohnzimmer', 'sensor.bad'])

    def test_removing_everything_and_removing_a_missing_sensor(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.alt', 'sensor.kueche'],
                             'kuehlere_raeume': ['sensor.alt']})
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')])
        self.assertEqual(status, 200, document)
        self.assertEqual(world.posted[-1]['heizkreis_1_sensoren'], ['sensor.kueche'])
        self.assertEqual(world.posted[-1]['kuehlere_raeume'], [])
        status, _document = world.save([])
        self.assertEqual(status, 200)
        self.assertEqual({key: world.posted[-1][key] for key in r.LIST_KEYS}, {key: [] for key in r.LIST_KEYS})

    def test_already_configured_unknown_sensor_may_stay(self):
        world = PickerWorld({'haus_sensoren': ['sensor.alt']})
        status, document = world.save([row('sensor.alt', 'heizkreis_2')])
        self.assertEqual(status, 200, document)
        self.assertEqual(world.posted[-1]['heizkreis_2_sensoren'], ['sensor.alt'])

    def test_unchanged_selection_is_not_posted(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.kueche']})
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')])
        self.assertEqual(status, 200)
        self.assertNotIn('save', world.calls)
        self.assertIn('Keine Änderung', document['message'])
        self.assertIn('bereits', document['message'])


class ValidationTests(unittest.TestCase):
    def refused(self, world, *args, status=400, text=None, **kwargs):
        got, document = world.save(*args, **kwargs)
        self.assertEqual(got, status, document)
        self.assertIs(document['ok'], False)
        self.assertIn('nichts gespeichert', document['message'])
        if text:
            self.assertIn(text, document['message'])
        self.assertEqual(world.posted, [])
        self.assertIsNone(world.monitor.pending)
        return document

    def test_unknown_entity_is_refused(self):
        world = PickerWorld()
        self.refused(world, [row('sensor.feuchte', 'weitere')], text='Unbekannter Sensor sensor.feuchte')
        self.refused(world, [row('sensor.nirgends', 'heizkreis_1')], text='Unbekannter Sensor')

    def test_new_sensor_refused_while_the_list_is_unavailable(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.kueche']})
        with patch.object(transport.FakeApi, 'states', side_effect=hm.ApiError('unavailable')):
            self.refused(world, [row('sensor.kueche', 'heizkreis_1'), row('sensor.bad', 'heizkreis_2')],
                         text='gerade nicht verfügbar')

    def test_entity_in_two_groups(self):
        self.refused(PickerWorld(), [row('sensor.kueche', 'heizkreis_1'), row('sensor.kueche', 'heizkreis_2')],
                     text='nur einer Gruppe')

    def test_flags_on_the_wrong_group(self):
        for bad, text in ((row('sensor.keller', 'weitere', kuehler=True), 'nur für Räume an einem Heizkreis'),
                          (row('sensor.keller', 'weitere', ofen=True), 'nur für Räume an einem Heizkreis'),
                          (row('sensor.kueche', 'heizkreis_1', frost=True), 'nur für weitere Räume'),
                          (row('sensor.kueche', 'heizkreis_2', frost=True), 'nur für weitere Räume')):
            with self.subTest(row=bad):
                self.refused(PickerWorld(), [bad], text=text)

    def test_more_than_24(self):
        states = [{'entity_id': f'sensor.t{i}', 'state': '20', 'attributes': {'device_class': 'temperature'}}
                  for i in range(30)]
        world = PickerWorld(states=states)
        self.refused(world, [row(f'sensor.t{i}', 'weitere') for i in range(25)], text='Höchstens 24')
        status, _document = world.save([row(f'sensor.t{i}', 'weitere') for i in range(24)])
        self.assertEqual(status, 200)

    def test_wrong_types_and_shapes(self):
        good = row('sensor.kueche', 'heizkreis_1')
        cases = [[{**good, 'kuehler': 'true'}], [{**good, 'frost': 0}], [{**good, 'gruppe': 'heizkreis_3'}],
                 [{**good, 'gruppe': None}], [{**good, 'entity_id': 'light.kueche'}], [{**good, 'entity_id': 5}],
                 [{**good, 'extra': 1}], [{k: v for k, v in good.items() if k != 'ofen'}], ['sensor.kueche'], [None]]
        for rows in cases:
            with self.subTest(rows=rows):
                self.refused(PickerWorld(), rows)
        world = PickerWorld()
        for kwargs in ({'modus': 'aus'}, {'art1': 'radiator'}, {'art2': None}, {'revision': 'x' * 64},
                       {'extra': True}):
            with self.subTest(kwargs=kwargs):
                self.refused(world, [good], **kwargs)
        token = world.token()
        for raw in (b'', b'[]', b'{', b'{"revision":1,"revision":2}', json.dumps({'sensoren': 'x'}).encode(),
                    b'\xff\xfe', b'{"x": NaN}'):
            with self.subTest(raw=raw):
                status, document = world.picker.save(ADMIN, token, raw)
                self.assertEqual(status, 400)
                self.assertIs(document['ok'], False)
        self.assertEqual(world.posted, [])

    def test_changed_meanwhile_is_refused(self):
        world = PickerWorld()
        token = world.token()
        stale = rp.revision(world.stored)
        world.stored['heizkreis_1_sensoren'] = ['sensor.kueche']
        self.refused(world, [row('sensor.bad', 'heizkreis_1')], token=token, revision=stale, status=409,
                     text='inzwischen geändert')


class AdminGateTests(unittest.TestCase):
    def assert_refused(self, world, user, text, token=None):
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')], user=user,
                                      token=world.picker.tokens.issue(user) if token is None else token)
        self.assertEqual(status, 403, document)
        self.assertIn(text, document['message'])
        self.assertNotIn('info', world.calls)
        self.assertEqual(world.posted, [])
        self.assertIsNone(world.monitor.pending)

    def test_non_admin_unknown_user_and_lookup_failure_are_refused(self):
        for user in (USER, SYSTEM, INACTIVE):
            with self.subTest(user=user):
                self.assert_refused(PickerWorld(), user, 'Nur Administratoren')
        self.assert_refused(PickerWorld(), 'f' * 32, 'nicht bekannt')
        world = PickerWorld()
        world.users_fail = True
        self.assert_refused(world, ADMIN, 'nicht geprüft werden')
        world = PickerWorld(users=[{'id': ADMIN, 'is_active': True, 'group_ids': 'system-admin'}])
        self.assert_refused(world, ADMIN, 'Nur Administratoren')
        world = PickerWorld(users=['broken', {'id': 7}])
        self.assert_refused(world, ADMIN, 'nicht bekannt')

    def test_token_mismatch_other_user_and_expiry(self):
        world = PickerWorld()
        self.assert_refused(world, ADMIN, 'abgelaufen', token='')
        self.assert_refused(world, ADMIN, 'abgelaufen', token='1.' + '0' * 64)
        self.assert_refused(world, ADMIN, 'abgelaufen', token=world.picker.tokens.issue(OWNER))
        token = world.picker.tokens.issue(ADMIN)
        world.clock.advance(rp.TOKEN_TTL_S + 1)
        self.assert_refused(world, ADMIN, 'abgelaufen', token=token)
        other = rp.PageTokens()
        self.assert_refused(world, ADMIN, 'abgelaufen', token=other.issue(ADMIN))


class SaveTests(unittest.TestCase):
    def test_supervisor_error_is_a_german_message_and_nothing_is_applied(self):
        world = PickerWorld()
        world.save_status = 400
        before = world.monitor.options
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')])
        self.assertEqual(status, 502)
        self.assertEqual(document, {'ok': False, 'message': 'Home Assistant hat die Einstellungen nicht gespeichert '
                                                            '(Fehler 400). Es wurde nichts geändert.'})
        self.assertIsNone(world.monitor.pending)
        world.cycle()
        self.assertIs(world.monitor.options, before)

    def test_timeout_and_unconfirmed_saves_are_not_assumed_saved(self):
        world = PickerWorld()
        world.save_timeout = True
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')])
        self.assertEqual(status, 504)
        self.assertIn('ist unklar', document['message'])
        self.assertIsNone(world.monitor.pending)
        world = PickerWorld()
        world.readback_differs = True
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')])
        self.assertEqual(status, 502)
        self.assertIn('Prüfung ist aber fehlgeschlagen', document['message'])
        self.assertIsNone(world.monitor.pending)
        world = PickerWorld()
        world.info_fails = True
        status, document = world.picker.save(ADMIN, world.picker.tokens.issue(ADMIN),
                                             world.body([row('sensor.kueche', 'heizkreis_1')], revision='0' * 64))
        self.assertEqual(status, 503)
        self.assertEqual(world.posted, [])

    def test_saved_selection_reloads_the_running_monitor(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.wohnzimmer']})
        world.run(200)
        old_engine = world.monitor.engine
        self.assertEqual(world.monitor.options.entities, ('sensor.wohnzimmer',))
        status, document = world.save([row('sensor.kueche', 'heizkreis_1', kuehler=True),
                                       row('sensor.keller', 'weitere', frost=True)], modus='melden')
        self.assertEqual(status, 200, document)
        self.assertIn('übernimmt die neue Auswahl', document['message'])   # the worker had not run yet
        self.assertEqual(document['revision'], rp.revision(world.stored))
        self.assertIs(world.monitor.engine, old_engine)
        world.calls.clear()
        world.cycle()
        options = world.monitor.options
        self.assertEqual((options.hc1, options.haus, options.cooler, options.frost, options.mode),
                         (('sensor.kueche',), ('sensor.keller',), {'sensor.kueche'}, {'sensor.keller'}, 'melden'))
        self.assertIsNot(world.monitor.engine, old_engine)
        self.assertEqual(world.apis[-1].entities, ('sensor.kueche', 'sensor.keller'))
        self.assertIn(('state', 'sensor.kueche'), world.calls)
        self.assertNotIn(('state', 'sensor.wohnzimmer'), world.calls)
        world.run(200)
        report = world.reports()[-1]
        self.assertEqual((report['mode'], report['sensors']['configured']), ('melden', 2))
        self.assertEqual(world.monitor.status()['configured'], 2)
        self.assertIn('Room page: saved.', world.picker_logs)
        self.assertNotIn('sensor.', ' '.join(world.picker_logs + world.logs))

    def test_save_waits_for_the_running_worker(self):
        world = PickerWorld()
        world.picker.apply_wait = 3
        stop = threading.Event()
        monitor = world.monitor

        def work():
            while not stop.is_set():
                monitor._apply_pending()
                stop.wait(.01)
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        self.addCleanup(stop.set)
        status, document = world.save([row('sensor.bad', 'heizkreis_2')])
        self.assertEqual(status, 200, document)
        self.assertIn('ab sofort', document['message'])
        self.assertEqual(monitor.options.hc2, ('sensor.bad',))

    def test_stale_sample_of_the_old_selection_is_discarded(self):
        world = PickerWorld({'heizkreis_1_sensoren': ['sensor.wohnzimmer']})
        world.run(5)
        original = transport.FakeApi.state

        def racing(api, entity):
            # The new selection arrives while the old one is being sampled.
            if world.monitor.pending is None and not hasattr(world, 'raced'):
                world.raced = True
                world.monitor.request_reconfigure(r.parse_house_options({'haus_sensoren': ['sensor.keller']}))
                world.monitor._apply_pending()
            return original(api, entity)
        engine_before = world.monitor.engine
        with patch.object(transport.FakeApi, 'state', racing):
            world.monitor.next_sample = world.clock.mono
            world.cycle()
        self.assertIsNot(world.monitor.engine, engine_before)
        self.assertFalse(world.monitor.sampled)          # the old sample did not reach the new engine
        world.cycle()
        self.assertTrue(world.monitor.sampled)

    def test_invalid_running_options_become_valid_after_saving(self):
        world = PickerWorld()
        world.monitor.request_reconfigure(r.parse_house_options({'heizkreis_1_sensoren': 'sensor.kaputt'}))
        world.cycle()
        self.assertIsNone(world.monitor.engine)
        self.assertIn('House monitor: config_invalid.', world.logs)
        world.save([row('sensor.kueche', 'heizkreis_1')])
        world.cycle()
        self.assertIsNotNone(world.monitor.engine)

    def test_one_save_at_a_time(self):
        world = PickerWorld()
        world.picker.save_lock.acquire()
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')])
        world.picker.save_lock.release()
        self.assertEqual(status, 409)
        self.assertEqual(world.posted, [])


class HttpTests(unittest.TestCase):
    """The real handler: ingress gate, same-origin checks, body bounds."""

    def setUp(self):
        self.world = PickerWorld()
        settings = ingress_proxy.Settings('unused.example', 443, 'unused.example', 'i' * 43)
        self.server = run_app.MonitorProxyServer(('127.0.0.1', 0), settings, run_app.disabled_status,
                                                 self.world.picker.page, self.world.picker.save)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        peer = patch.object(ingress_proxy, 'SUPERVISOR_IP', '127.0.0.1')
        peer.start()

        def cleanup():
            self.server.shutdown(); self.server.server_close(); thread.join(); peer.stop()
        self.addCleanup(cleanup)

    def send(self, method='POST', body=b'', headers=None, path='/api/local-monitor/raumsensoren', user=ADMIN,
             identity=True):
        client = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        sent = {}
        if identity:
            sent.update({'X-Remote-User-Id': user, 'X-Ingress-Path': PREFIX})
        sent.update(headers or {})
        client.putrequest(method, path, skip_accept_encoding=True)
        for name, value in sent.items():
            client.putheader(name, value)
        if 'Content-Length' not in sent and method == 'POST':
            client.putheader('Content-Length', str(len(body)))
        client.endheaders(body if method == 'POST' else None)
        response = client.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        client.close()
        return result

    def browser(self, token=None, **overrides):
        headers = {'Content-Type': 'application/json', 'Origin': 'http://ha.example:8123', 'Referer': PAGE,
                   'Sec-Fetch-Site': 'same-origin', rp.TOKEN_HEADER: token or self.world.token()}
        headers.update(overrides)
        return {name: value for name, value in headers.items() if value is not None}

    def body(self):
        return self.world.body([row('sensor.kueche', 'heizkreis_1')])

    def test_page_through_ingress(self):
        status, body, headers = self.send('GET')
        self.assertEqual(status, 200)
        nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", headers['Content-Security-Policy']).group(1)
        page = body.decode()
        self.assertIn(f'<script nonce="{nonce}">', page)
        self.assertIn(f'<style nonce="{nonce}">', page)
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertIn('data-token="', page)
        self.assertNotIn('data-token="', self.send('GET', user=USER)[1].decode())

    def test_browser_save_succeeds(self):
        status, body, headers = self.send(body=self.body(), headers=self.browser())
        document = json.loads(body)
        self.assertEqual(status, 200, document)
        self.assertIs(document['ok'], True)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        self.assertEqual(self.world.posted[-1]['heizkreis_1_sensoren'], ['sensor.kueche'])

    def assert_refused(self, status, body=None, headers=None, identity=True, user=ADMIN):
        got, raw, _headers = self.send(body=self.body() if body is None else body,
                                       headers=self.browser() if headers is None else headers,
                                       identity=identity, user=user)
        self.assertEqual(got, status, raw)
        self.assertEqual(self.world.posted, [])
        self.assertIsNone(self.world.monitor.pending)
        return raw

    def test_cross_site_requests_are_refused(self):
        for overrides in ({'Origin': None}, {'Referer': None}, {'Origin': 'https://evil.example'},
                          {'Origin': 'null'}, {'Referer': 'http://evil.example' + PREFIX + '/x'},
                          {'Referer': 'http://ha.example:8123/api/hassio_ingress/other_app_path_12345/x'},
                          {'Referer': 'http://ha.example:8123' + PREFIX},
                          {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                          {'Origin': 'http://ha.example:8123/path'}):
            with self.subTest(overrides=overrides):
                raw = self.assert_refused(403, headers=self.browser(**overrides))
                self.assertIn('nicht von dieser Seite', json.loads(raw)['message'])
        # Without Sec-Fetch-Site (older browsers) Origin and Referer still decide.
        status, _body, _headers = self.send(body=self.body(), headers=self.browser(**{'Sec-Fetch-Site': None}))
        self.assertEqual(status, 200)

    def test_content_type_body_bounds_and_methods(self):
        for content_type in (None, 'text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data',
                             'application/jsonx'):
            with self.subTest(content_type=content_type):
                self.assert_refused(415, headers=self.browser(**{'Content-Type': content_type}))
        self.assertEqual(self.send(body=self.body(), headers=self.browser(**{'Content-Type': 'application/json; charset=utf-8'}))[0], 200)
        self.world.posted.clear()
        self.world.stored = stored_options()
        self.world.monitor.pending = None
        self.assert_refused(413, body=b'', headers=self.browser(**{'Content-Length': str(rp.SAVE_BODY_CAP + 1)}))
        self.assert_refused(400, body=b'', headers=self.browser(**{'Transfer-Encoding': 'chunked'}))
        self.assert_refused(411, body=b'', headers=self.browser(**{'Content-Length': 'x'}))
        for method in ('PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            self.assertEqual(self.send(method, body=b'')[0], 405)
        self.assertEqual(self.send(path='/api/local-monitor/raumsensoren?x=1', body=self.body(),
                                   headers=self.browser())[0], 400)
        self.assertEqual(self.world.posted, [])

    def test_ingress_identity_admin_and_token_are_required(self):
        self.assert_refused(400, identity=False)
        self.assert_refused(403, user=USER)
        self.assert_refused(403, headers=self.browser(**{rp.TOKEN_HEADER: None}))
        self.assert_refused(403, headers=self.browser(token=self.world.picker.tokens.issue(OWNER)))
        with patch.object(ingress_proxy, 'SUPERVISOR_IP', '172.30.32.2'):
            self.assert_refused(403)

    def test_status_endpoint_is_unchanged(self):
        status, body, headers = self.send('GET', path='/api/local-monitor/status')
        self.assertEqual((status, json.loads(body)), (200, run_app.disabled_status()))
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Content-Security-Policy', headers)
        self.assertEqual(self.send('POST', path='/api/local-monitor/status', body=b'{}',
                                   headers=self.browser())[0], 405)
        self.assertEqual(self.world.posted, [])

    def test_save_failure_inside_the_picker_never_breaks_ingress(self):
        self.server.house_save = lambda *_args: (_ for _ in ()).throw(RuntimeError('SECRET'))
        status, raw, _headers = self.send(body=self.body(), headers=self.browser())
        self.assertEqual(status, 500)
        self.assertNotIn(b'SECRET', raw)
        self.assertEqual(self.send('GET', path='/api/local-monitor/status')[0], 200)

    def test_non_supervisor_peer_cannot_save_with_forged_ingress_admin_headers(self):
        with patch.object(ingress_proxy, 'SUPERVISOR_IP', '127.0.0.2'):
            status, _body, _headers = self.send(body=self.body(), headers=self.browser())
        self.assertEqual(status, 403)
        self.assertEqual(self.world.posted, [])

    def test_nonadmin_cannot_promote_itself_with_admin_headers(self):
        headers = self.browser()
        headers['X-Remote-User-Is-Admin'] = 'true'
        headers['X-Role'] = 'admin'
        status, _body, _headers = self.send(body=self.body(), headers=headers, user=USER)
        self.assertEqual(status, 403)
        self.assertEqual(self.world.posted, [])


class PickerApiTests(unittest.TestCase):
    def test_allowlist_is_exact(self):
        api = rp.PickerApi('token')
        self.assertTrue(api.allowed('GET', rp.INFO_PATH, None))
        self.assertTrue(api.allowed('POST', rp.OPTIONS_PATH, {'options': {}}))
        self.assertTrue(api.allowed('POST', rp.TEMPLATE_PATH, {'template': rp.AREA_TEMPLATE}))
        for method, path, body in [
                ('POST', rp.TEMPLATE_PATH, {'template': '{{ states | list }}'}),
                ('POST', rp.TEMPLATE_PATH, {'template': rp.AREA_TEMPLATE, 'variables': {}}),
                ('POST', rp.OPTIONS_PATH, {'options': {}, 'boot': 'manual'}), ('POST', rp.OPTIONS_PATH, {'options': None}),
                ('POST', '/addons/self/restart', None), ('POST', '/addons/self/restart', {}),
                ('POST', '/addons/self/security', {}), ('POST', '/addons/self/update', {}),
                ('GET', '/addons/self/options/config', None), ('GET', '/addons', None), ('GET', '/info', None),
                ('GET', rp.INFO_PATH, {}), ('DELETE', rp.INFO_PATH, None), ('GET', hm.STATES_PATH, None),
                ('POST', hm.PUBLISH_PATH, {'topic': 'hargassner/x/house/report', 'payload': '', 'qos': 0, 'retain': False}),
                ('POST', '/core/api/services/homeassistant/restart', {})]:
            with self.subTest(method=method, path=path):
                self.assertFalse(api.allowed(method, path, body))
        with self.assertRaises(hm.ApiError):
            api.request('POST', '/addons/self/restart', {})

    def test_area_template_is_read_only(self):
        for word in ('service', 'call', 'set_', 'turn_', 'persistent_notification', 'mqtt', 'restart'):
            self.assertNotIn(word, rp.AREA_TEMPLATE)


class Supervisor(ThreadingHTTPServer):
    daemon_threads = True


class SupervisorWireTests(unittest.TestCase):
    """PickerApi against a loopback stand-in for the Supervisor and Core."""

    def setUp(self):
        test = self
        self.requests = []
        self.replies = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def handle_one(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else None
                test.requests.append((self.command, self.path, dict(self.headers), body))
                status, raw = test.replies.get((self.command, self.path), (404, b'{}'))
                self.send_response(status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            do_GET = do_POST = handle_one
        self.server = Supervisor(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join()))
        self.api = rp.PickerApi('synthetic-token', host='127.0.0.1', port=self.server.server_address[1])

    def test_info_save_and_areas(self):
        options = stored_options(heizkreis_1_sensoren=['sensor.a'])
        self.replies[('GET', rp.INFO_PATH)] = (200, json.dumps({'result': 'ok', 'data': {'options': options,
                                                                                         'name': 'x'}}).encode())
        self.assertEqual(self.api.stored_options(), options)
        self.replies[('POST', rp.OPTIONS_PATH)] = (200, b'{"result":"ok","data":{}}')
        self.api.save_options(options)
        method, path, headers, body = self.requests[-1]
        self.assertEqual((method, path, headers['Authorization'], headers['Content-Type']),
                         ('POST', rp.OPTIONS_PATH, 'Bearer synthetic-token', 'application/json'))
        self.assertEqual(json.loads(body), {'options': options})
        self.replies[('POST', rp.OPTIONS_PATH)] = (400, b'{"result":"error","message":"bad"}')
        with self.assertRaises(rp.SaveRefused) as caught:
            self.api.save_options(options)
        self.assertEqual(caught.exception.status, 400)
        self.replies[('POST', rp.TEMPLATE_PATH)] = (200, json.dumps(
            [['sensor.a', 'Wohnzimmer'], ['light.x', 'Flur'], ['sensor.b', ''], 'x', ['sensor.c', 'Bad', 1]]).encode())
        self.assertEqual(self.api.areas(), {'sensor.a': 'Wohnzimmer'})
        self.assertEqual(json.loads(self.requests[-1][3]), {'template': rp.AREA_TEMPLATE})

    def test_bad_answers_raise(self):
        for reply in ((200, b'{"result":"error"}'), (200, b'{"result":"ok","data":{"options":[]}}'),
                      (403, b'{}'), (200, b'not json'), (200, b'{"result":"ok","result":"ok"}')):
            with self.subTest(reply=reply):
                self.replies[('GET', rp.INFO_PATH)] = reply
                with self.assertRaises(hm.ApiError):
                    self.api.stored_options()
        for reply in ((401, b'[]'), (200, b'{}'), (200, b'nope')):
            with self.subTest(reply=reply):
                self.replies[('POST', rp.TEMPLATE_PATH)] = reply
                with self.assertRaises(hm.ApiError):
                    self.api.areas()


class UsersWebSocketTests(unittest.TestCase):
    def run_users(self, script):
        server = wstest.Server(script)
        try:
            return ws.list_users('synthetic-token', host='127.0.0.1', port=server.port,
                                 connect=lambda host, port, path: ws.WebSocket.connect(host, port, path, timeout=3))
        finally:
            server.close()   # joins the peer script before the test looks at it

    def test_auth_one_command_and_close(self):
        sent = []

        def script(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            sent.append(peer.receive_json())
            peer.send_json({'type': 'auth_ok'})
            sent.append(peer.receive_json())
            peer.send_json({'id': 1, 'type': 'result', 'success': True, 'result': USERS})
            sent.append(peer.receive()[0])
        self.assertEqual(self.run_users(script), USERS)
        self.assertEqual(sent, [{'type': 'auth', 'access_token': 'synthetic-token'},
                                {'id': 1, 'type': 'config/auth/list'}, 0x8])

    def test_failures_raise_fixed_categories(self):
        def refused(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_ok'})
            peer.receive_json()
            peer.send_json({'id': 1, 'type': 'result', 'success': False, 'error': {'code': 'unauthorized'}})

        def invalid(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_invalid'})

        def wrong(peer):
            peer.handshake()
            peer.send_json({'type': 'auth_required'})
            peer.receive_json()
            peer.send_json({'type': 'auth_ok'})
            peer.receive_json()
            peer.send_json({'id': 1, 'type': 'result', 'success': True, 'result': {'not': 'a list'}})
        for script, category in ((refused, 'refused'), (invalid, 'auth_failed'), (wrong, 'protocol')):
            with self.subTest(category=category):
                with self.assertRaises(ws.WebSocketError) as caught:
                    self.run_users(script)
                self.assertEqual(caught.exception.args, (category,))


class SameOriginTests(unittest.TestCase):
    def headers(self, **values):
        message = http.client.HTTPMessage()
        for name, value in values.items():
            message[name.replace('_', '-')] = value
        return message

    def test_rules(self):
        good = dict(Origin='https://ha.example', Referer='https://ha.example' + PREFIX + '/api/local-monitor/raumsensoren')
        self.assertTrue(rp.same_origin(self.headers(**good), PREFIX))
        self.assertTrue(rp.same_origin(self.headers(**good, Sec_Fetch_Site='same-origin'), PREFIX))
        for bad in (dict(good, Origin='http://ha.example'), dict(good, Referer='https://ha.example/'),
                    dict(good, Referer='https://ha.example' + PREFIX + 'x/'), dict(good, Origin='https://ha.example '),
                    dict(good, Sec_Fetch_Site='none'), {'Origin': good['Origin']}, {'Referer': good['Referer']},
                    dict(good, Origin='ftp://ha.example')):
            with self.subTest(bad=bad):
                self.assertFalse(rp.same_origin(self.headers(**bad), PREFIX))
        doubled = self.headers(**good)
        doubled['Origin'] = 'https://ha.example'
        self.assertFalse(rp.same_origin(doubled, PREFIX))


class TokenTests(unittest.TestCase):
    def test_bound_to_user_and_time(self):
        now = [100.0]
        tokens = rp.PageTokens(b'k' * 32, monotonic=lambda: now[0])
        token = tokens.issue(ADMIN)
        self.assertTrue(tokens.valid(ADMIN, token))
        self.assertFalse(tokens.valid(USER, token))
        self.assertFalse(tokens.valid(ADMIN, token[:-1] + ('0' if token[-1] != '0' else '1')))
        self.assertFalse(tokens.valid(ADMIN, None))
        now[0] += rp.TOKEN_TTL_S + 1
        self.assertFalse(tokens.valid(ADMIN, token))



class RoomPageAddress(unittest.TestCase):
    def api(self, document):
        class Api:
            def __init__(self, token): pass
            def _json(self, method, path, cap=None): return 200, document
        Api.ingress_path = rp.PickerApi.ingress_path
        return Api

    def test_the_log_names_the_page_address_once_known(self):
        lines = []
        info = {'result': 'ok', 'data': {'ingress_url': '/api/hassio_ingress/AbCdEf123456_-xyz/'}}
        path = rp.announce_room_page(lambda: 'token', self.api(info), lines.append)
        self.assertEqual(path, '/api/hassio_ingress/AbCdEf123456_-xyz/')
        self.assertEqual(len(lines), 1)
        self.assertIn('/api/hassio_ingress/AbCdEf123456_-xyz/api/local-monitor/raumsensoren', lines[0])
        self.assertIn('Panel der App einmal öffnen', lines[0])

    def test_nothing_is_logged_for_an_unknown_or_odd_path_or_a_failure(self):
        for info in ({'result': 'error'}, {'result': 'ok', 'data': {}}, {'result': 'ok', 'data': {'ingress_url': 'http://x/'}},
                     {'result': 'ok', 'data': {'ingress_url': '/api/hassio_ingress/a b/'}}, []):
            lines = []
            self.assertIsNone(rp.announce_room_page(lambda: 'token', self.api(info), lines.append))
            self.assertEqual(lines, [])
        def broken():
            raise RuntimeError('no token')
        lines = []
        self.assertIsNone(rp.announce_room_page(broken, self.api({}), lines.append))
        self.assertEqual(lines, [])


if __name__ == '__main__':
    unittest.main()

class RoomPickerSecurityRegressions(unittest.TestCase):
    def test_demoted_admin_cannot_save_with_a_still_valid_page_token(self):
        world = PickerWorld()
        token = world.token()
        world.users = [{**entry, 'is_owner': False, 'group_ids': ['system-users']}
                       if entry['id'] == ADMIN else dict(entry) for entry in USERS]
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')], token=token)
        self.assertEqual(status, 403, document)
        self.assertEqual(world.posted, [])
        self.assertIsNone(world.monitor.pending)

    def test_cached_admin_does_not_authorize_save_when_core_is_unreachable(self):
        world = PickerWorld()
        token = world.token()
        world.users_fail = True
        status, document = world.save([row('sensor.kueche', 'heizkreis_1')], token=token)
        self.assertEqual(status, 403, document)
        self.assertEqual(world.posted, [])
        self.assertIsNone(world.monitor.pending)

    def test_unhashable_group_is_a_validation_refusal_not_an_exception(self):
        for group in ([], {}):
            world = PickerWorld()
            with self.subTest(group=group):
                status, document = world.save([row('sensor.kueche', group)])
                self.assertEqual(status, 400, document)
                self.assertEqual(world.posted, [])

    def test_malformed_entity_ids_never_reach_supervisor_options(self):
        for entity in ('sensor.kueche\n', 'sensor.kueche/../../options', 'light.kueche',
                       'sensor.küche', 'sensor.kueche?query=1', 'sensor.' + 'x' * 300):
            world = PickerWorld()
            with self.subTest(entity=entity):
                status, document = world.save([row(entity, 'heizkreis_1')])
                self.assertEqual(status, 400, document)
                self.assertEqual(world.posted, [])

    def test_radiator_and_floor_selections_remain_in_their_own_circuits(self):
        world = PickerWorld()
        status, document = world.save([row('sensor.kueche', 'heizkreis_1'), row('sensor.bad', 'heizkreis_2')],
                                      art1='heizkoerper', art2='fussboden')
        self.assertEqual(status, 200, document)
        self.assertEqual(world.posted[-1]['heizkreis_1_sensoren'], ['sensor.kueche'])
        self.assertEqual(world.posted[-1]['heizkreis_2_sensoren'], ['sensor.bad'])
        self.assertEqual(r.parse_house_options(world.posted[-1]).option_types, ('radiator', 'floor'))
