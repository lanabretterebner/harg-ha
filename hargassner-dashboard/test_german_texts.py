"""German user texts of the app: every notification, verdict and page sentence
renders as plain German (owner complaint 2026-11-23: object-verb-subject order,
"Störung Kessel, der Kessel meldet Störung", telegram style).

A text that may be spoken or mailed (HA notification, Betriebsüberwachung
verdict) starts with a complete sentence that says what and where: it has a
finite verb, no inner full stop, no abbreviation, and is not an instruction;
instructions follow as further sentences. No text repeats a level word, no
notification sentence names a component twice, and no raw id, English word or
doubled word reaches a user. Room and sensor findings are sent to the VM as
codes and numbers only (house_rules.py); the VM writes their German text.
"""
import ast
from pathlib import Path
import re
import unittest

import health_alerts as health
import house_monitor
import house_rules as rules
import room_picker
from operation_watchdog import LEASE, NOTIFY_TEXT, Verdict

HERE = Path(__file__).resolve().parent

# Finite verb forms the app's sentences use. A new sentence needs its verb
# here (or a rewrite), which is the point.
VERBS = frozenset('''
ist sind war waren wird werden wurde wurden hat haben gibt läuft laufen kommt
erreicht liefert meldet melden stimmen sendet wartet nimmt heizt steigt liegt
zählt sinkt bleibt bleiben droht zeigt hält erlaubt kann können darf muss braucht
antwortet gilt gelten verletzt verwendet übernimmt beginnen konnte lässt startet passt
'''.split())
# Infinitives that make an instruction ("Sensor prüfen."): fine as a follow-up
# sentence, never as the first one.
INSTRUCTIONS = ('prüfen', 'wechseln', 'aktualisieren', 'entlüften', 'starten', 'laden', 'versuchen')
LEVELS = ('Störung', 'Hinweis', 'Unbekannt', 'Normal')
COMPONENTS = ('Kessel', 'Puffer', 'Warmwasser', 'Steuerung', 'Vorlauf', 'Pelletvorrat', 'Heizkreis 1',
              'Heizkreis 2', 'Raumsensor', 'Zertifikat', 'VM', 'Home Assistant', 'Heizungsdienst')
ENGLISH = frozenset('''
the and is are not of to for with from by available unavailable invalid required request response
failed error health check warning report missing stale please
'''.split())
ABBREVIATIONS = re.compile(r'\b(?:z\. ?B|u\. ?a|ca|Nr|bzw|ggf|usw|evtl|Std|inkl|max|min|vgl)\.')
RAW_ID = re.compile(r'\b[a-z0-9]+_[a-z0-9_]+\b')
DOUBLED = re.compile(r'\b(\w+)\s+\1\b', re.IGNORECASE)
SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=["A-ZÄÖÜ0-9])')
CODES = (set(health.REASONS) | set(health.KINDS) | set(rules.CODES) | set(rules.REASONS)
         | set(rules.WHYS) | {'fold', 'fresh', 'stale', 'maintenance'})
# Labelled data lines of the room-learning evidence ("Quelle: Angabe"): a list
# of sources, read as a table row, not a sentence.
DATA_LINE = re.compile(r'^(?:Raumtemperatur|Heizkreise): ')


def sentences(text):
    return SENTENCE_END.split(text.strip())


def words(text):
    return re.findall(r'[A-Za-zÄÖÜäöüß]+', text)


class Checker:
    """Collects every rule a text breaks, so one failure lists all of them."""

    def __init__(self, case):
        self.case, self.problems = case, []

    def fail(self, where, text, why):
        self.problems.append(f'{where}: {why}: {text!r}')

    def common(self, where, text):
        if ABBREVIATIONS.search(text):
            self.fail(where, text, 'abbreviation with a full stop')
        # Ids are lower case; German nouns (Frost) are not.
        if RAW_ID.search(text) or any(re.search(r'\b' + re.escape(code) + r'\b', text) for code in CODES):
            self.fail(where, text, 'raw id')
        if ENGLISH & {word.lower() for word in words(text)}:
            self.fail(where, text, 'English word')
        if DOUBLED.search(text):
            self.fail(where, text, 'doubled word')
        if re.search('[‐-―]', text):
            self.fail(where, text, 'typographic dash instead of an ordinary hyphen')
        for level in LEVELS:
            if text.count(level) > 1:
                self.fail(where, text, f'level word {level} repeated')

    def sentence(self, where, text, components=True):
        """A text that starts with a complete German sentence."""
        self.common(where, text)
        first = sentences(text)[0]
        if not first.endswith(('.', '!', '?')):
            self.fail(where, text, 'first sentence has no end')
        if first.count('.') != 1 and not first.endswith('?'):
            self.fail(where, text, 'inner full stop in the first sentence')
        if not VERBS & {word.lower() for word in words(first)}:
            self.fail(where, text, 'no finite verb in the first sentence')
        if words(first) and words(first)[-1] in INSTRUCTIONS:
            self.fail(where, text, 'the first sentence is an instruction')
        if components:
            for name in COMPONENTS:
                if len(re.findall(r'\b' + re.escape(name) + r'\b', first)) > 1:
                    self.fail(where, text, f'component {name} named twice in the first sentence')
        for later in sentences(text)[1:]:
            if not later.rstrip(')').endswith(('.', '!', '?', '...')):
                self.fail(where, text, 'a sentence has no end')

    def heading(self, where, text):
        self.common(where, text)

    def done(self):
        self.case.assertEqual(self.problems, [], '\n' + '\n'.join(self.problems))


def notification_texts():
    """(where, title, message) of every HA notification health_alerts creates."""
    rendered = []
    for component in health.KINDS:
        for reason in sorted(health.REASONS):
            for status in ('fault', 'warning', 'unknown'):
                event = {'component': component, 'status': status, 'reason': reason}
                action, payload = health.ha_notification(event)
                if action == 'create':
                    rendered.append((f'{component}/{reason}/{status}', payload['title'], payload['message']))
    return rendered


def verdict_texts():
    """The Betriebsüberwachung texts of the app: waiting, maintenance, failed and the notify row."""
    clock = {'tick': 0.0, 'wall': 1000.0}
    verdict = Verdict(lambda: clock['tick'], lambda: clock['wall'])
    waiting = verdict.document()['summary']
    clock['tick'] += LEASE
    failed = verdict.document()['summary']
    verdict.maintenance_at = clock['tick']
    clock['tick'] += 60
    maintenance = verdict.document()['summary']
    return {'waiting': waiting, 'failed': failed, 'maintenance': maintenance, 'notify': NOTIFY_TEXT}


def status_page(**overrides):
    status = {'mode': 'melden', 'valid': True, 'configured': 4, 'reason': None, 'last_report_wall': 1.8e9,
              'publish': 'ok', 'base_pinned': True, 'ack': 'accepted', 'ack_other_instance': False,
              'basis_unknown': {'hc1': {'24h': 0, '7d': 12}},
              'circuits': {'hc1': {'type': 'radiator', 'type_source': 'controller', 'judged': 'yes', 'why': None},
                           'hc2': {'type': None, 'type_source': None, 'judged': 'unknown', 'why': 'ha'}}}
    status.update(overrides)
    return house_monitor.status_html(status)


def list_items(page):
    return [re.sub(r'<[^>]+>', '', item) for item in re.findall(r'<li>(.*?)</li>', page)]


def source_texts(name, calls=None):
    """Sentence-like strings of a module, f-string fields rendered as "3".

    calls: only first arguments of calls with these names; otherwise every
    string that starts like a sentence (capital or a field) and ends with a
    full stop, outside docstrings and log lines."""
    tree = ast.parse((HERE / name).read_text())
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(part) for part in node.values)
        if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', None)) in ('print', 'log', 'emit'):
            skip.update(id(child) for child in ast.walk(node))

    def render(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return ''.join(part.value if isinstance(part, ast.Constant) else '3' for part in node.values)
        return None
    found = []
    for node in ast.walk(tree):
        if calls is not None:
            if (isinstance(node, ast.Call) and node.args
                    and getattr(node.func, 'id', getattr(node.func, 'attr', None)) in calls):
                # The message is the first string argument (a category may follow).
                texts = [render(argument) for argument in node.args if render(argument) is not None]
                if texts:
                    found.append((f'{name}:{node.lineno}', texts[0]))
            continue
        if id(node) in skip:
            continue
        text = render(node)
        if (text and ' ' in text and re.match(r'["A-ZÄÖÜ3]', text) and text.rstrip().endswith('.')
                and re.search('[a-zäöüß]{3}', text) and not DATA_LINE.match(text)):
            found.append((f'{name}:{node.lineno}', text.strip()))
    return found


class GermanTextTests(unittest.TestCase):
    def test_every_health_notification_starts_with_a_sentence(self):
        rendered = notification_texts()
        # Every reason that can raise a problem has its own copy; the three
        # components and the unknown suffix are all reached.
        self.assertGreaterEqual(len(rendered), 40)
        self.assertEqual({where.split('/')[0] for where, _t, _m in rendered}, set(health.KINDS))
        check = Checker(self)
        for where, title, message in rendered:
            check.heading(where + ' title', title)
            self.assertTrue(title.startswith('Heizung: '), title)
            check.sentence(where, message)
            self.assertNotIn('Prüfe ', message)   # one form of address: infinitive instructions
        check.done()

    def test_unknown_suffix_keeps_cause_and_reassurance_apart(self):
        _action, payload = health.ha_notification({'component': 'heartbeat', 'status': 'unknown',
                                                   'reason': 'report_missing'})
        self.assertEqual(payload['message'],
                         'Der lokale Heizungsdienst liefert keine auswertbare Statusmeldung. Den Dashboard-Dienst '
                         'und seine Protokolle auf der VM prüfen. Der Zustand der Heizung ist so lange nicht '
                         'sicher bekannt. Ein Ausfall der Heizung ist damit nicht belegt.')

    def test_betriebsueberwachung_texts_are_sentences(self):
        texts = verdict_texts()
        self.assertEqual(len(set(texts.values())), 4)
        check = Checker(self)
        for where, text in texts.items():
            check.sentence('verdict/' + where, text)
        check.done()
        self.assertTrue(texts['failed'].startswith('Die VM ist nicht erreichbar'))
        self.assertIn('gewartet', texts['maintenance'])
        self.assertTrue(texts['waiting'].startswith('Die App wartet'))

    def test_room_status_page_sentences(self):
        pages = [status_page()]
        pages += [status_page(reason=reason) for reason in house_monitor.REASON_TEXT]
        pages += [status_page(ack=ack) for ack in house_monitor.ACK_TEXT]
        pages += [status_page(base_pinned=False, ack=None), status_page(ack=None),
                  status_page(ack=None, ack_other_instance=True), status_page(configured=0)]
        items = {item for page in pages for item in list_items(page)}
        check = Checker(self)
        for item in sorted(items):
            label, _sep, value = item.partition(': ')
            if label in ('Modus', 'Überwachte Sensoren', 'Letzte Meldung an die VM'):
                check.heading('status item', item)
            elif label == 'Zustand':
                check.sentence('status reason', value)
            else:
                check.sentence('status item', item)
        for row in re.findall(r'<td>(.*?)</td>', ''.join(pages)):
            check.heading('status cell', row)
        check.done()
        self.assertGreaterEqual(len(items), 14)

    def test_room_learning_and_page_texts(self):
        texts = source_texts('room_learning.py')
        texts += source_texts('room_picker.py', calls=('refuse', 'SelectionError'))
        texts += [('room_picker.py viewer', text) for text in (*room_picker.VIEWER_TEXT.values(),
                                                               *room_picker.REFUSED_TEXT.values())]
        self.assertGreaterEqual(len(texts), 50)
        check = Checker(self)
        for where, text in texts:
            check.sentence(where, text, components=False)
        check.done()

    def test_ingress_errors_are_german(self):
        texts = source_texts('ingress_proxy.py', calls=('Rejected', 'error'))
        texts += source_texts('run_app.py', calls=('Rejected', 'refuse'))
        self.assertGreaterEqual(len(texts), 20)
        check = Checker(self)
        for where, text in texts:
            check.sentence(where, text)
        check.done()

    def test_room_findings_leave_the_app_as_codes_and_numbers(self):
        """The VM renders the German finding text; the app sends no text of its own."""
        self.assertEqual({check for check, _level in rules.CODES.values()}, {'Räume', 'Raumsensoren'})
        self.assertEqual({level for _check, level in rules.CODES.values()}, {'Hinweis', 'Störung'})
        findings = [{'code': code, 'level': level, 'label': 'Bad', 'circuit': 'hc1', 'held_s': 3600,
                     'room': 18.5, 'target': 21.0, 'count': 3} for code, (_check, level) in rules.CODES.items()]
        report, _payload = rules.build_report(instance='a' * 32, seq=1, context_id='b' * 32, mode='melden',
                                              monitor='ok', reason=None,
                                              sensors={'configured': 1, 'usable': 1, 'unavailable': 0},
                                              circuits={}, findings=findings, context_major=2)
        for row in report['findings']:
            self.assertEqual(set(row), {'code', 'level', 'label', 'circuit', 'held_s', 'room', 'target', 'count',
                                        'allowance'})
            for key in ('label', 'level', 'code', 'circuit'):
                self.assertNotRegex(row[key], r'\s(ist|sind|wird|prüfen)\b')
        self.assertEqual(rules.make_labels(['sensor.a'], {}), {'sensor.a': 'Sensor 1'})

    def test_checker_catches_the_reported_classes(self):
        """The rules find what the owner complained about."""
        bad = {
            'Störung Kessel, der Kessel meldet Störung.': 'level word Störung repeated',
            'Batterie schwach. Batterie wechseln.': 'no finite verb in the first sentence',
            'Fenster und Ventil prüfen. Der Raum ist kalt.': 'the first sentence is an instruction',
            'Der Raum ist z. B. kalt.': 'abbreviation with a full stop',
            'Der Code room_cold ist aktiv.': 'raw id',
            'The sensor is not available.': 'English word',
            'Der Raum ist ist kalt.': 'doubled word',
            'Der Kessel ist kalt, der Kessel heizt nicht.': 'component Kessel named twice in the first sentence',
        }
        for text, why in bad.items():
            check = Checker(self)
            check.sentence('example', text)
            self.assertTrue(any(why in problem for problem in check.problems), (text, check.problems))
        check = Checker(self)
        check.sentence('example', 'Im Raum Bad ist es seit längerem zu kalt (18,5 °C statt 21,0 °C). '
                                  'Fenster und Ventil prüfen.')
        check.done()


if __name__ == '__main__':
    unittest.main()
