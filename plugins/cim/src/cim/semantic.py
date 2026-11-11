"""Offline GreenDIGIT draft profile and deliberately narrow AUDITOR import."""
from copy import deepcopy
from datetime import datetime, timedelta
from importlib.resources import files
import json
import math


class CIMError(ValueError):
    """Invalid or unsupported semantic input."""


def catalogue():
    return json.loads(files('cim').joinpath('data/catalogue-v1.json').read_text())


def require(condition, message):
    if not condition:
        raise CIMError(message)


def text(value, name):
    require(isinstance(value, str) and bool(value.strip()), f'{name}: expected a non-empty string, not a list or missing value')
    return value


def number(value):
    require(type(value) in (int, float) and value >= 0 and value <= 1.7976931348623157e308 and math.isfinite(value),
            'value must be a finite, non-negative number')
    return value


def period(value, monthly=False):
    require(isinstance(value, dict) and set(value) == {'start', 'end'}, 'period requires start and end')
    try:
        start, end = (datetime.fromisoformat(text(value[k], k).replace('Z', '+00:00')) for k in ('start', 'end'))
    except (ValueError, TypeError) as exc:
        raise CIMError('invalid ISO period') from exc
    require(all(d.tzinfo is not None and d.utcoffset() == timedelta(0) for d in (start, end)), 'period must be explicit UTC')
    require(start < end, 'period end must follow start')
    if monthly:
        require(start.day == 1 and (start.hour, start.minute, start.second, start.microsecond) == (0, 0, 0, 0)
                and start.year < 9999, 'export requires one complete UTC calendar month, end exclusive')
        next_month = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
        require(end == next_month, 'export requires one complete UTC calendar month, end exclusive')
    return start, end


def definition(metric_id):
    text(metric_id, 'metric_id')
    cat = catalogue()
    require(metric_id in cat['metrics'], f'unknown metric: {metric_id}')
    return cat['metrics'][metric_id]


def validate(observation):
    require(isinstance(observation, dict), 'observation must be an object')
    allowed = {'observation_id', 'source_record_id', 'metric_id', 'value', 'unit', 'site', 'producer',
               'accounting_instance', 'instance_type', 'period', 'boundary', 'method', 'scope', 'vo',
               'benchmark', 'entry_tags', 'catalogue_version', 'mapping_version', 'provenance', 'original'}
    require(set(observation) <= allowed, 'unsupported canonical fields')
    for key in ('observation_id', 'source_record_id', 'metric_id', 'site', 'producer', 'accounting_instance',
                'instance_type', 'boundary', 'method', 'catalogue_version', 'mapping_version', 'scope'):
        text(observation.get(key), key)
    metric = definition(observation['metric_id'])
    require(observation['catalogue_version'] == catalogue()['version'], 'unsupported catalogue version')
    require(observation.get('unit') == metric['unit'], 'incompatible canonical unit')
    require(observation['boundary'] == metric['boundary'], 'metric boundary mismatch')
    require(observation['scope'] in metric['allowed_context']['scope'], 'unsupported observation scope')
    number(observation.get('value'))
    period(observation.get('period'))
    for key in ('vo', 'benchmark'):
        if key in observation:
            text(observation[key], key)
    tags = observation.get('entry_tags', {})
    require(isinstance(tags, dict), 'entry_tags must be an object')
    for key, value in tags.items():
        text(key, 'tag name'); text(value, 'tag value')
    require('method' not in tags or tags['method'] == observation['method'], 'conflicting method tag')
    provenance = observation.get('provenance')
    require(isinstance(provenance, dict), 'source provenance required')
    for key in ('source', 'field'):
        text(provenance.get(key), 'provenance.' + key)
    original = observation.get('original')
    require(isinstance(original, dict), 'original value/unit required')
    number(original.get('value'))
    unit = original.get('unit')
    require(unit == metric['unit'] or (unit == 'kWh' and metric['unit'] == 'Wh'), 'incompatible original unit')
    expected = original['value'] * (1000 if unit == 'kWh' else 1)
    require(math.isfinite(expected) and observation['value'] == expected, 'original and canonical values disagree')
    return observation


def resolve(binding, record):
    if isinstance(binding, dict):
        require(len(binding) == 1, 'context binding needs exactly one selector')
        if 'meta' in binding:
            text(binding['meta'], 'metadata selector')
            require(isinstance(record.get('meta'), dict), 'AUDITOR metadata must be an object')
            values = record['meta'].get(binding['meta'])
            require(isinstance(values, list) and len(values) == 1, f'ambiguous or missing metadata: {binding["meta"]}')
            return values[0]
        if 'field' in binding:
            return record.get(text(binding['field'], 'field selector'))
        raise CIMError('unknown context selector')
    return deepcopy(binding)


def import_auditor(record, mapping, context):
    """Return observations and explicit ignore report; no unit/name guessing."""
    require(isinstance(record, dict) and isinstance(mapping, dict) and isinstance(context, dict), 'record, mapping and context must be objects')
    rid = text(record.get('record_id'), 'record_id')
    version = text(mapping.get('version'), 'mapping version')
    source = text(mapping.get('source'), 'mapping source')
    rules = mapping.get('fields')
    require(isinstance(rules, dict), 'mapping fields required')
    allowed_context = {'site', 'producer', 'accounting_instance', 'instance_type', 'period', 'boundary',
                       'method', 'scope', 'vo', 'benchmark', 'entry_tags'}
    require(set(context) <= allowed_context, 'unsupported context fields')
    require(isinstance(context.get('period'), dict), 'context period bindings required')
    ctx = {key: resolve(value, record) for key, value in context.items() if key not in ('period', 'entry_tags')}
    if 'entry_tags' in context:
        require(isinstance(context['entry_tags'], dict), 'context entry_tags must be an object')
        ctx['entry_tags'] = {key: resolve(value, record) for key, value in context['entry_tags'].items()}
    ctx['period'] = {key: resolve(value, record) for key, value in context.get('period', {}).items()}
    # A caller may declare scope, but may not widen a job interval into a month.
    if 'start_time' in record or 'stop_time' in record:
        source_period = {'start': record.get('start_time'), 'end': record.get('stop_time')}
        require(period(ctx['period']) == period(source_period), 'context period must match source record interval; cannot relabel jobs as monthly totals')
    components = record.get('components')
    require(isinstance(components, list) and components, 'non-empty AUDITOR components required')
    observations, ignored, seen = [], [], set()
    for component in components:
        require(isinstance(component, dict), 'component must be an object')
        field = text(component.get('name'), 'component name')
        require(field not in seen, f'duplicate component: {field}')
        seen.add(field)
        require(field in rules, f'unknown mapping for component: {field}')
        rule = rules[field]
        require(isinstance(rule, dict), 'mapping rule must be an object')
        if rule.get('ignore') is True:
            ignored.append({'source_record_id': rid, 'field': field, 'reason': text(rule.get('reason'), 'ignore reason')})
            continue
        metric_id = text(rule.get('metric_id'), 'mapped metric_id')
        metric = definition(metric_id)
        unit = rule.get('unit')
        require(unit == metric['unit'] or (unit == 'kWh' and metric['unit'] == 'Wh'), f'incompatible declared unit: {unit}')
        require('unit' not in component or component['unit'] == unit, 'component unit conflicts with declared mapping')
        value = number(component.get('amount'))
        observation = dict(ctx, observation_id=f'{rid}#{field}', source_record_id=rid,
                           metric_id=metric_id, value=value * (1000 if unit == 'kWh' else 1), unit=metric['unit'],
                           original={'value': value, 'unit': unit}, catalogue_version=catalogue()['version'],
                           mapping_version=version, provenance={'source': source, 'field': field, 'record': deepcopy(record)})
        observations.append(validate(observation))
    require(observations, 'record has no mapped observations')
    return {'observations': observations, 'ignored_fields': ignored}


def import_batch(records, mapping, context):
    require(isinstance(records, list) and records, 'non-empty record batch required')
    result = {'observations': [], 'ignored_fields': []}
    for record in records:
        imported = import_auditor(record, mapping, context)
        for key in result:
            result[key].extend(imported[key])
    validate_batch(result['observations'])
    return result


def validate_batch(observations):
    require(isinstance(observations, list) and observations, 'non-empty observation batch required')
    ids = set()
    for observation in observations:
        validate(observation)
        oid = observation['observation_id']
        require(oid not in ids, f'duplicate observation identity: {oid}')
        ids.add(oid)
    return observations
