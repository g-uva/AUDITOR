"""CIM request preparation, response validation and immutable KPI attachment."""
from copy import deepcopy

from .eimps import FORMULAS, METRICS, VERSION, MockEIMPSClient, MockRegistration, identity
from .semantic import catalogue, number, period, require, text, validate_batch


def prepare_request(observation, plan):
    require(isinstance(plan, dict) and set(plan) <= {'type', 'source_observation_id', 'location_id'}, 'invalid enrichment plan')
    kind = plan.get('type')
    require(isinstance(kind, str) and kind in METRICS, 'unsupported KPI type')
    require(plan.get('source_observation_id') == observation['observation_id'], 'plan observation identity mismatch')
    d = {'period': deepcopy(observation['period'])}
    if kind != 'ci':
        d['site_id'] = observation['site']
    if kind != 'pue':
        d['location_id'] = text(plan.get('location_id'), 'location_id')
    else:
        require('location_id' not in plan, 'PUE uses site and period, not location')
    if kind == 'cfp':
        bases = {'urn:greendigit:cim:draft:it-energy': 'it',
                 'urn:greendigit:cim:draft:total-facility-energy': 'facility'}
        require(observation['metric_id'] in bases and observation['provenance']['source'] != 'synthetic-eimps',
                'CFP requires an explicit measured IT or facility energy observation; workload attribution is ambiguous')
        d['energy'] = {'value': observation['value'], 'unit': observation['unit'],
                       'basis': bases[observation['metric_id']], 'source_observation_id': observation['observation_id']}
    return {'schema_version': VERSION, 'type': kind, 'details': d}


def validate_response(request, response):
    """Validate the contract and dependency lineage, without calculating KPIs."""
    require(isinstance(response, dict), 'EIMPS response must be an object')
    kind, d = request['type'], request['details']
    metric, unit, boundary, basis = METRICS[kind]
    expected = {'schema_version': VERSION, 'type': kind, 'metric_id': metric, 'unit': unit,
                'boundary': boundary, 'basis': d['energy']['basis'] if kind == 'cfp' else basis,
                'site_id': d.get('site_id'), 'location_id': d.get('location_id'),
                'method': 'synthetic-eimps', 'formula_version': FORMULAS[kind],
                'accounting_mode': 'location-based', 'synthetic': True}
    for key, value in expected.items():
        require(key in response and type(response[key]) is type(value) and response[key] == value, f'EIMPS response mismatch: {key}')
    require(period(response.get('period')) == period(d['period']), 'EIMPS response period mismatch')
    number(response.get('value'))
    for key in ('input_references', 'factor_references', 'dependencies'):
        require(isinstance(response.get(key), list), f'EIMPS response requires {key}')
    payload = {k: v for k, v in response.items() if k != 'result_id'}
    require(response.get('result_id') == identity(payload), 'EIMPS result identity mismatch')
    inputs, factors, deps = (response[k] for k in ('input_references', 'factor_references', 'dependencies'))
    if kind == 'pue':
        require(len(inputs) == 2 and not factors and not deps, 'PUE requires facility and IT input provenance')
        for ref, basis in zip(inputs, ('facility', 'it')):
            require(isinstance(ref, dict) and ref.get('basis') == basis and ref.get('unit') in ('Wh', 'kWh'), 'invalid PUE input provenance')
            number(ref.get('value')); text(ref.get('source_observation_id'), 'PUE input identity')
            text(ref.get('boundary_id'), 'PUE boundary identity')
            require(period(ref.get('period')) == period(d['period']), 'PUE input period mismatch')
        require(inputs[0]['boundary_id'] == inputs[1]['boundary_id'] and inputs[1]['value'] > 0, 'incompatible PUE boundaries or zero denominator')
    elif kind == 'ci':
        require(not inputs and not deps and len(factors) == 1, 'CI factor provenance required')
        factor = factors[0]
        require(isinstance(factor, dict), 'invalid CI factor')
        text(factor.get('factor_id'), 'CI factor identity'); number(factor.get('value'))
        require(factor.get('unit') == unit and factor.get('location_id') == d['location_id']
                and factor.get('accounting_mode') == response['accounting_mode'] and factor.get('synthetic') is True
                and period(factor.get('period')) == period(d['period']), 'CI factor context mismatch')
    else:
        require(inputs == [d['energy']], 'CFP energy provenance mismatch')
        require(len(deps) == 2, 'CFP requires PUE and CI dependency results')
        for dep, t, context in zip(deps, ('pue', 'ci'), ({'site_id': d['site_id']}, {'location_id': d['location_id']})):
            validate_response({'schema_version': VERSION, 'type': t, 'details': dict(context, period=d['period'])}, dep)
        require(factors == [{'result_id': r['result_id'], 'used': r['type'] == 'ci' or d['energy']['basis'] == 'it'} for r in deps], 'CFP dependency reference mismatch')
    return response


def enrich(observations, plans, *, client=None, registration=None):
    """Return original observations plus distinct derived KPIs; never mutate inputs.

    Each plan selects type, source_observation_id and (for CI/CFP) location_id.
    The selected observation supplies site, period and canonical attachment context.
    """
    validate_batch(observations)
    require(isinstance(plans, list) and plans, 'non-empty enrichment plan list required')
    client = MockEIMPSClient() if client is None else client
    registration = registration if registration is not None else getattr(client, 'registration', MockRegistration())
    output = deepcopy(observations)
    by_id = {o['observation_id']: o for o in observations}
    attached = dict(by_id)
    for plan in plans:
        require(isinstance(plan, dict), 'enrichment plan must be an object')
        oid = text(plan.get('source_observation_id'), 'source_observation_id')
        require(oid in by_id, f'missing source observation: {oid}')
        source = by_id[oid]
        request = prepare_request(source, plan)
        # CI needs no site in the service contract. Check optional site/location
        # association through the provider's replaceable registration boundary.
        registration.validate_context(source['site'], plan.get('location_id'))
        response = validate_response(request, client.get_metrics(deepcopy(request)))
        result = deepcopy(source)
        # Result identity includes attachment context: never merge sites or producers.
        context = {k: source[k] for k in ('site', 'producer', 'accounting_instance', 'instance_type', 'scope')}
        result_id = identity({'response': response, 'context': context, 'source_observation_id': oid})
        value = response['value'] / 1000 if plan['type'] == 'cfp' else response['value']
        result.update(observation_id='cim-kpi-' + result_id, source_record_id=response['result_id'],
                      metric_id=response['metric_id'], value=value,
                      unit='kgCO2e' if plan['type'] == 'cfp' else response['unit'], boundary=response['boundary'],
                      method=response['formula_version'], mapping_version=VERSION, catalogue_version=catalogue()['version'],
                      original={'value': value, 'unit': 'kgCO2e' if plan['type'] == 'cfp' else response['unit']},
                      provenance={'source': 'synthetic-eimps', 'field': plan['type'], 'derived': True,
                                  'synthetic': True, 'request': deepcopy(request), 'response': deepcopy(response),
                                  'source_observation_id': oid})
        result.pop('entry_tags', None)
        result.pop('benchmark', None)
        if result['observation_id'] in attached:
            require(attached[result['observation_id']] == result, 'conflicting derived observation identity')
        else:
            output.append(result)
            attached[result['observation_id']] = result
    return validate_batch(output)
