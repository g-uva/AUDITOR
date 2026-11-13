"""Offline stand-in for POST /get-metrics. All KPI calculations live here."""
from copy import deepcopy
from hashlib import sha256
from importlib.resources import files
import json
from typing import Protocol

from .semantic import number, period, require, text

VERSION = 'eimps-mock-v1'
PREFIX = 'urn:greendigit:cim:draft:'
METRICS = {'pue': (PREFIX + 'pue', '1', 'facility-to-it', 'facility/it'),
           'ci': (PREFIX + 'carbon-intensity', 'gCO2e/kWh', 'location-electricity', 'electricity'),
           'cfp': (PREFIX + 'operational-co2e', 'gCO2e', 'facility-operations', None)}
FORMULAS = {'pue': 'facility-energy/it-energy:v1', 'ci': 'synthetic-location-time-lookup:v1',
            'cfp': 'electricity-energy*pue-if-it*ci:v1'}


def fixture():
    return json.loads(files('cim').joinpath('data/eimps-mock-v1.json').read_text())


def identity(value):
    return 'eimps-' + sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class MetricsClient(Protocol):
    def get_metrics(self, request: dict) -> dict: ...


class Registration(Protocol):
    """Replace with registration/authentication integration; this is not authorisation."""
    def validate_context(self, site_id: str | None, location_id: str | None) -> dict | None: ...


class MockRegistration:
    def __init__(self, data=None):
        self.data = deepcopy(fixture() if data is None else data)

    def validate_context(self, site_id, location_id):
        site = None
        if site_id is not None:
            require(site_id in self.data['sites'], f'unknown registered site: {site_id}')
            site = self.data['sites'][site_id]
        if location_id is not None:
            require(location_id in self.data['locations'], f'unsupported location: {location_id}')
            require(site is None or location_id in site['locations'], 'inconsistent site/location combination')
        return deepcopy(site)


def validate_request(request, registration):
    require(isinstance(request, dict) and set(request) == {'schema_version', 'type', 'details'}, 'invalid get-metrics request envelope')
    require(request['schema_version'] == VERSION, 'unsupported EIMPS schema version')
    kind = request['type']
    require(isinstance(kind, str) and kind in METRICS, 'unsupported KPI type')
    d = request['details']
    required = {'pue': {'site_id', 'period'}, 'ci': {'location_id', 'period'},
                'cfp': {'site_id', 'location_id', 'period', 'energy'}}[kind]
    require(isinstance(d, dict) and set(d) == required, f'{kind} requires exactly {sorted(required)}')
    period(d['period'])
    for key in required & {'site_id', 'location_id'}:
        text(d[key], key)
    registration.validate_context(d.get('site_id'), d.get('location_id'))
    if kind == 'cfp':
        e = d['energy']
        require(isinstance(e, dict) and set(e) == {'value', 'unit', 'basis', 'source_observation_id'}, 'explicit energy value, unit, basis and observation identity required')
        number(e['value']); text(e['source_observation_id'], 'energy observation identity')
        require(e['unit'] in ('Wh', 'kWh'), 'energy must be Wh or kWh')
        require(e['basis'] in ('it', 'facility'), 'ambiguous workload-energy attribution; explicit IT or facility energy required')
    return d


def kwh(energy):
    require(energy.get('unit') in ('Wh', 'kWh'), 'incompatible energy unit')
    return number(number(energy.get('value')) / (1000 if energy['unit'] == 'Wh' else 1))


class MockEIMPSClient:
    def __init__(self, data=None, registration=None):
        self.data = deepcopy(fixture() if data is None else data)
        self.registration = registration if registration is not None else MockRegistration(self.data)

    def get_metrics(self, request):
        request = deepcopy(request)
        d = validate_request(request, self.registration)
        kind = request['type']
        metric, unit, boundary, basis = METRICS[kind]
        result = dict(schema_version=VERSION, type=kind, metric_id=metric, unit=unit,
                      site_id=d.get('site_id'), location_id=d.get('location_id'), period=deepcopy(d['period']),
                      boundary=boundary, basis=basis, method='synthetic-eimps', formula_version=FORMULAS[kind],
                      accounting_mode='location-based', synthetic=True, input_references=[], factor_references=[], dependencies=[])
        if kind == 'pue':
            matches = [x for x in self.data['pue_inputs'] if x['site_id'] == d['site_id'] and period(x['period']) == period(d['period'])]
            require(len(matches) == 1, 'unavailable or ambiguous facility/IT inputs for site and period')
            inputs = matches[0]
            facility, it = inputs.get('facility_energy'), inputs.get('it_energy')
            require(isinstance(facility, dict) and isinstance(it, dict), 'missing facility/IT energy inputs')
            text(inputs.get('boundary_id'), 'PUE boundary identity')
            for energy, expected in ((facility, 'facility'), (it, 'it')):
                require(energy.get('basis') == expected and energy.get('boundary_id') == inputs['boundary_id'], 'incompatible PUE energy boundaries')
                require(period(energy.get('period')) == period(d['period']), 'incompatible PUE input periods')
                text(energy.get('source_observation_id'), 'PUE input reference')
            denominator = kwh(it)
            require(denominator > 0, 'zero IT-energy denominator')
            result['value'] = number(kwh(facility) / denominator)
            result['input_references'] = deepcopy([facility, it])
        elif kind == 'ci':
            matches = [x for x in self.data['ci_factors'] if x['location_id'] == d['location_id'] and period(x['period']) == period(d['period'])]
            require(len(matches) == 1, 'unavailable or ambiguous CI factor for location and period')
            factor = matches[0]
            require(factor.get('synthetic') is True and factor.get('unit') == unit and factor.get('accounting_mode') == 'location-based', 'incompatible CI factor')
            text(factor.get('factor_id'), 'CI factor identity')
            result['value'] = number(factor.get('value'))
            result['factor_references'] = [deepcopy(factor)]
        else:
            def dependency(t, details):
                return self.get_metrics({'schema_version': VERSION, 'type': t, 'details': details})
            # Return both dependencies for review, even when facility energy bypasses PUE.
            pue = dependency('pue', {'site_id': d['site_id'], 'period': d['period']})
            ci = dependency('ci', {'location_id': d['location_id'], 'period': d['period']})
            energy = d['energy']
            result['basis'] = energy['basis']
            result['value'] = number(kwh(energy) * (pue['value'] if energy['basis'] == 'it' else 1) * ci['value'])
            result['dependencies'] = [pue, ci]
            result['input_references'] = [deepcopy(energy)]
            result['factor_references'] = [{'result_id': r['result_id'], 'used': r['type'] == 'ci' or energy['basis'] == 'it'} for r in (pue, ci)]
        result['result_id'] = identity(result)
        return result
