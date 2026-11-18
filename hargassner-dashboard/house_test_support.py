"""Synthetic harness for the house-rule tests. No network, no real HA."""
from datetime import datetime, timezone
import json

import house_rules as r

START_MONO = 10_000.0
START_WALL = 2_000_000_000.0


def circuit(**values):
    base = {'mode': 'auto', 'phase': 'day', 'target': 21.0, 'day_target': 21.0, 'setback_target': 18.0,
            'status': 1, 'pump': True, 'flow': 45.0, 'flow_target': 45.0, 'type': 'radiator',
            'type_source': 'controller'}
    base.update(values)
    if base['type'] is None:
        base['type_source'] = None
    return base


def context_document(sequence=1, hc1=None, hc2=None, **top):
    document = {'schema': 'harg-house-context/1', 'context': f'{0xABCDEF0123456789:016x}:{sequence}',
                'online': True, 'maintenance': False, 'outdoor': 2.0, 'outdoor_avg': 3.0, 'report_ack': None,
                'circuits': {'hc1': hc1 or circuit(), 'hc2': hc2 or circuit(type='floor', flow=32.0)}}
    document.update(top)
    return document


def stamp(wall):
    return datetime.fromtimestamp(wall, timezone.utc).isoformat()


class Harness:
    """Drives one HouseEngine minute by minute.

    values: entity -> number, 'unavailable', 'missing' or None (not read).
    reporting: entity -> 'periodic' (new last_reported every minute) or a
    number of minutes between reports (on-change sensors report on a change
    or after that many minutes).
    """

    def __init__(self, options, *, local_minute=12 * 60, hc1=None, hc2=None, values=None, **top):
        self.options = r.parse_house_options(options)
        assert self.options.valid
        self.engine = r.HouseEngine(self.options)
        self.now, self.wall = START_MONO, START_WALL
        self.local = local_minute
        self.ha = 'running'
        self.sequence = 0
        self.context = context_document(hc1=hc1, hc2=hc2, **top)
        self.context_state = 'ok'
        self.values = dict(values or {})
        self.names = {}
        self.attributes = {}
        self.reporting = {}
        self.last_sent = {}
        self.findings = []
        self.steps = 0
        self.stopped = False   # the app is not running: time and HA go on, nothing is evaluated

    def set_circuit(self, name, **values):
        self.context['circuits'][name].update(values)

    def sample(self, entity):
        value = self.values.get(entity)
        if value is None:
            return None
        if value == 'missing':
            return 'missing'
        every = self.reporting.get(entity, 'periodic')
        previous = self.last_sent.get(entity)
        if every == 'periodic' or previous is None or previous[1] != value or self.wall - previous[0] >= every * 60:
            self.last_sent[entity] = (self.wall, value)
        sent_wall, sent_value = self.last_sent[entity]
        attributes = {'unit_of_measurement': '°C', 'device_class': 'temperature',
                      'friendly_name': self.names.get(entity, entity.split('.', 1)[1].replace('_', ' ').title())}
        attributes.update(self.attributes.get(entity, {}))
        state = 'unavailable' if sent_value == 'unavailable' else (
            sent_value if isinstance(sent_value, str) else f'{sent_value:.2f}')
        return {'entity_id': entity, 'state': state, 'attributes': attributes,
                'last_updated': stamp(sent_wall), 'last_reported': stamp(sent_wall)}

    def step(self):
        self.now += 60
        self.wall += 60
        self.local = (self.local + 1) % 1440
        self.steps += 1
        context = None
        state = self.context_state
        if self.context_state == 'ok' and self.context is not None:
            self.sequence += 1
            self.context['context'] = f'{0xABCDEF0123456789:016x}:{self.sequence}'
            status, context = r.validate_context(json.dumps(self.context))
            assert status == 'ok', status
        samples = {}
        for entity in self.options.entities:
            sample = self.sample(entity)
            if sample is not None:
                samples[entity] = sample
        if self.stopped:
            self.findings = []
            return self.findings
        self.findings = self.engine.step(self.now, wall=self.wall, ha=self.ha, samples=samples, context=context,
                                         context_state=state, local_minutes=self.local)
        return self.findings

    def run(self, minutes, until=None):
        for _ in range(minutes):
            self.step()
            if until is not None and until(self):
                return True
        return False

    def codes(self):
        return {row['code'] for row in self.findings}

    def rows(self, code):
        return [row for row in self.findings if row['code'] == code]

    def has(self, code, label=None, circuit=None):
        return any(row['code'] == code and (label is None or row['label'] == label)
                   and (circuit is None or row['circuit'] == circuit) for row in self.findings)

    def first(self, code, limit, label=None):
        """Minutes until `code` appears (or None within `limit`)."""
        for minute in range(1, limit + 1):
            self.step()
            if self.has(code, label):
                return minute
        return None
