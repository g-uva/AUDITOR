"""Fixed JSON-LD subsets; no RDF expansion, remote contexts or inference."""
from copy import deepcopy
from .semantic import require, validate

GD = 'urn:greendigit:cim:draft:'
SAREF = 'https://saref.etsi.org/core/'
SCHEMA = 'https://schema.org/'
CONTEXT = {'gd': GD, 'saref': SAREF, 'schema': SCHEMA,
           'gd:metadata': {'@id': GD + 'metadata', '@type': '@json'}}
# Local unit identifiers avoid implying a third-party unit catalogue endorsement.
UNITS = {unit: GD + 'unit:' + unit for unit in ('Wh', 'L', 'kgCO2e')}


def to_representation(observation, representation):
    validate(observation)
    require(representation in ('saref', 'schemaorg'), 'unsupported representation')
    o = deepcopy(observation)
    result = {'@context': CONTEXT.copy(), '@id': o.pop('observation_id')}
    metric, value, unit = (o.pop(key) for key in ('metric_id', 'value', 'unit'))
    result['gd:metadata'] = o
    if representation == 'saref':
        result.update({'@type': 'saref:PropertyValue', 'saref:isValueOfProperty': {'@id': metric},
                       'saref:hasValue': value, 'saref:isMeasuredIn': {'@id': UNITS[unit]}})
    else:
        result.update({'@type': 'schema:PropertyValue', 'schema:propertyID': metric,
                       'schema:value': value, 'schema:unitText': unit})
    return result


def from_representation(document, representation):
    require(representation in ('saref', 'schemaorg'), 'unsupported representation')
    require(isinstance(document, dict) and document.get('@context') == CONTEXT, 'only the documented fixed JSON-LD context is supported')
    if representation == 'saref':
        keys = {'saref:isValueOfProperty', 'saref:hasValue', 'saref:isMeasuredIn'}
        require(document.get('@type') == 'saref:PropertyValue', 'expected SAREF 4.1.1 PropertyValue')
        metric = document.get('saref:isValueOfProperty')
        unit = document.get('saref:isMeasuredIn')
        require(isinstance(metric, dict) and set(metric) == {'@id'}, 'property reference required')
        require(isinstance(unit, dict) and set(unit) == {'@id'} and unit['@id'] in UNITS.values(), 'unsupported unit reference')
        metric, value, unit = metric['@id'], document.get('saref:hasValue'), next(k for k, v in UNITS.items() if v == unit['@id'])
    else:
        keys = {'schema:propertyID', 'schema:value', 'schema:unitText'}
        require(document.get('@type') == 'schema:PropertyValue', 'expected Schema.org PropertyValue')
        metric, value, unit = (document.get(key) for key in ('schema:propertyID', 'schema:value', 'schema:unitText'))
    require(set(document) == keys | {'@context', '@id', '@type', 'gd:metadata'}, 'unsupported or missing representation fields')
    metadata = document.get('gd:metadata')
    require(isinstance(metadata, dict) and not set(metadata) & {'observation_id', 'metric_id', 'value', 'unit'}, 'invalid metadata')
    return validate(dict(deepcopy(metadata), observation_id=document.get('@id'), metric_id=metric, value=value, unit=unit))
