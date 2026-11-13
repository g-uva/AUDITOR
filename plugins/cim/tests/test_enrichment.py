"""Contract, arithmetic, provenance and orchestration acceptance checks."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cim import (CIMError, MockEIMPSClient, MockRegistration, enrich, export_draft,
                 from_representation, send_batch, to_representation)
from cim.cli import main
from cim.eimps import fixture, identity
from cim.enrichment import prepare_request

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'demo/05_enrich_kpis'


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.observations = json.loads((DEMO / 'energy-inputs.json').read_text())['observations']
        self.plans = json.loads((DEMO / 'requests.json').read_text())
        self.client = MockEIMPSClient()
        self.request = prepare_request(self.observations[0], self.plans[2])

    def test_two_sites_independent_together_and_repeat(self):
        original = deepcopy(self.observations)
        with patch('urllib.request.urlopen', side_effect=AssertionError('offline')):
            result = enrich(self.observations, self.plans)
            self.assertEqual(enrich(result, self.plans), result)
        self.assertEqual(self.observations, original)
        self.assertEqual(result[:4], original)
        self.assertEqual(len({o['observation_id'] for o in result}), 12)
        for plan, observation in zip(self.plans, result[4:]):
            self.assertEqual(enrich(original, [plan])[-1], observation)
            expected = {'SiteA': {'pue': 1.5, 'ci': 200, 'cfp': 30000},
                        'SiteB': {'pue': 1.25, 'ci': 400, 'cfp': 100000}}
            self.assertEqual(observation['provenance']['response']['value'], expected[observation['site']][plan['type']])
            if plan['type'] == 'cfp':
                self.assertEqual(observation['unit'], 'kgCO2e')
                self.assertEqual(observation['value'], expected[observation['site']]['cfp'] / 1000)
            for representation in ('saref', 'schemaorg'):
                self.assertEqual(from_representation(to_representation(observation, representation), representation), observation)

    def test_energy_unit_conversion_and_facility_bypass(self):
        request = deepcopy(self.request)
        request['details']['energy'].update(value=100, unit='kWh')
        self.assertEqual(self.client.get_metrics(request)['value'], 30000)
        request['details']['energy'].update(value=150, basis='facility')
        response = self.client.get_metrics(request)
        self.assertEqual(response['value'], 30000)
        self.assertEqual([x['used'] for x in response['factor_references']], [False, True])
        data = fixture()
        data['pue_inputs'][0]['facility_energy'].update(value=150000, unit='Wh')
        self.assertEqual(MockEIMPSClient(data).get_metrics(self.request)['value'], 30000)

    def test_dependency_provenance(self):
        response = self.client.get_metrics(self.request)
        pue, ci = response['dependencies']
        self.assertEqual([r['basis'] for r in pue['input_references']], ['facility', 'it'])
        self.assertEqual([r['value'] for r in pue['input_references']], [150, 100])
        self.assertEqual(ci['factor_references'][0]['value'], 200)
        self.assertTrue(ci['factor_references'][0]['synthetic'])
        self.assertEqual(response['input_references'], [self.request['details']['energy']])
        self.assertEqual([r['result_id'] for r in response['factor_references']], [pue['result_id'], ci['result_id']])

    def test_registration_and_context_errors(self):
        for key, value, error in [('site_id', 'missing', 'unknown registered site'),
                                  ('location_id', 'missing', 'unsupported location'),
                                  ('location_id', 'mock-region-b', 'inconsistent site/location')]:
            request = deepcopy(self.request); request['details'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(CIMError, error):
                self.client.get_metrics(request)
        plans = [dict(self.plans[1], location_id='mock-region-b')]
        with self.assertRaisesRegex(CIMError, 'inconsistent'):
            enrich(self.observations, plans)
        self.assertEqual(MockRegistration().validate_context('SiteA', 'mock-region-a')['canonical_name'], 'SiteA')

    def test_missing_invalid_or_ambiguous_inputs(self):
        for key in ('site_id', 'period', 'location_id', 'energy'):
            request = deepcopy(self.request); del request['details'][key]
            with self.subTest(key=key), self.assertRaises(CIMError): self.client.get_metrics(request)
        for key, value in [('basis', 'workload'), ('unit', 'W'), ('source_observation_id', ''),
                           ('value', -1), ('value', True), ('value', float('inf'))]:
            request = deepcopy(self.request); request['details']['energy'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(CIMError): self.client.get_metrics(request)
        workload = json.loads((ROOT / 'fixtures/canonical.json').read_text())['observations']
        with self.assertRaisesRegex(CIMError, 'ambiguous'):
            enrich(workload, [dict(self.plans[2], source_observation_id=workload[0]['observation_id'])])
        with self.assertRaisesRegex(CIMError, 'missing source'):
            enrich(self.observations, [dict(self.plans[2], source_observation_id='absent')])

    def test_missing_factors_and_invalid_pue_inputs(self):
        for mutation, error in [
            (lambda d: d.update(ci_factors=[]), 'unavailable.*CI'),
            (lambda d: d.update(pue_inputs=[]), 'unavailable.*facility/IT'),
            (lambda d: d['pue_inputs'][0].pop('it_energy'), 'missing facility/IT'),
            (lambda d: d['pue_inputs'][0]['it_energy'].update(value=0), 'zero IT'),
            (lambda d: d['pue_inputs'][0]['it_energy'].update(boundary_id='different'), 'boundaries'),
            (lambda d: d['pue_inputs'][0]['it_energy']['period'].update(end='2026-10-02T00:00:00Z'), 'periods'),
            (lambda d: d['ci_factors'][0].update(unit='kgCO2e/MWh'), 'incompatible CI')]:
            data = fixture(); mutation(data)
            with self.subTest(error=error), self.assertRaisesRegex(CIMError, error):
                MockEIMPSClient(data).get_metrics(self.request)
        request = deepcopy(self.request); request['details']['period']['end'] = '2026-10-02T00:00:00Z'
        with self.assertRaisesRegex(CIMError, 'unavailable'): self.client.get_metrics(request)

    def test_response_mismatches_even_with_valid_result_hash(self):
        def rehash(r):
            r['result_id'] = identity({k: v for k, v in r.items() if k != 'result_id'})
        changes = [lambda r: r.update(site_id='SiteB'), lambda r: r.update(unit='kgCO2e'),
                   lambda r: r.update(metric_id='wrong'), lambda r: r.update(synthetic=False),
                   lambda r: r.update(basis='facility'), lambda r: r.update(formula_version='unknown'),
                   lambda r: r['period'].update(end='2026-10-02T00:00:00Z'),
                   lambda r: r.update(input_references=[]), lambda r: r.update(factor_references=[]),
                   lambda r: r.update(dependencies=[]),
                   lambda r: r['dependencies'][0].update(site_id='SiteB'),
                   lambda r: r['dependencies'][1]['factor_references'][0].update(location_id='mock-region-b')]
        for change in changes:
            response = self.client.get_metrics(self.request); change(response)
            for dep in response['dependencies']: rehash(dep)
            rehash(response)
            class BadClient:
                def get_metrics(self, request): return response
            with self.subTest(change=change), self.assertRaises(CIMError):
                enrich(self.observations, [self.plans[2]], client=BadClient())
        response = self.client.get_metrics(self.request); response['value'] = 123
        with self.assertRaisesRegex(CIMError, 'identity'):
            from cim.enrichment import validate_response
            validate_response(self.request, response)

    def test_enrichment_cannot_silently_submit_kpis(self):
        derived = enrich(self.observations, self.plans)[4:]
        for o in derived:
            with self.assertRaisesRegex(CIMError, 'target supports only'):
                export_draft([o])
        with patch('cim.send_record') as send, self.assertRaises(CIMError):
            send_batch(derived, 'http://unused')
        send.assert_not_called()

    def test_replaceable_client_and_registration(self):
        response = self.client.get_metrics(self.request)
        calls = []
        class Client:
            def get_metrics(self, request):
                calls.append(deepcopy(request))
                return json.loads(json.dumps(response))
        class Registry:
            def validate_context(self, site_id, location_id):
                self.context = (site_id, location_id)
        registry = Registry()
        result = enrich(self.observations, [self.plans[2]], client=Client(), registration=registry)
        self.assertEqual(calls, [self.request])
        self.assertEqual(registry.context, ('SiteA', 'mock-region-a'))
        self.assertEqual(result[-1]['value'], 30)

    def test_cli(self):
        with TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            output = Path(tmp) / 'result.json'
            self.assertEqual(main(['enrich', str(DEMO / 'energy-inputs.json'), '--requests', str(DEMO / 'requests.json'), '--output', str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())['observations'], enrich(self.observations, self.plans))
            self.assertEqual(main(['enrich', str(DEMO / 'energy-inputs.json')]), 1)


if __name__ == '__main__':
    unittest.main()
