import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cim import (CIMError, DeliveryError, import_auditor, import_batch, validate,
                 export_draft, send_batch, to_representation, from_representation)
from cim.cli import main

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_text())


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.records = read('fixtures/grid-auditor.json')
        self.mapping = read('src/cim/data/auditor-mapping-v1.json')
        self.context = read('fixtures/context.json')
        self.batch = import_batch(self.records, self.mapping, self.context)
        self.o = self.batch['observations'][0]

    def test_conversion_and_preservation(self):
        self.assertEqual(self.o['value'], 12500)
        self.assertEqual(self.o['original'], {'value': 12.5, 'unit': 'kWh'})
        self.assertEqual(self.o['producer'], 'central-grid-producer')
        self.assertEqual(self.o['provenance']['record'], self.records[0])
        self.assertEqual(len(self.batch['ignored_fields']), 2)
        self.assertNotIn('benchmark', self.o)

    def test_golden_contract_and_sites(self):
        self.assertEqual(export_draft(self.batch['observations']), read('fixtures/pear-draft-golden.json'))
        self.assertEqual([o['site'] for o in self.batch['observations']], ['SiteA', 'SiteB'])
        self.assertEqual([r['accounting']['values']['raw'][0]['value'] for r in export_draft(self.batch['observations'])['records']], [12500, 7000])

    def test_documented_representation_round_trips(self):
        for representation in ('saref', 'schemaorg'):
            fixture = read(f'fixtures/{representation}.json')
            self.assertEqual(to_representation(self.o, representation), fixture)
            self.assertEqual(from_representation(fixture, representation), self.o)
            bad = deepcopy(fixture); bad['@context'] = 'https://untrusted.example/context'
            with self.assertRaises(CIMError):
                from_representation(bad, representation)
            bad = deepcopy(fixture); bad['gd:metadata']['boundary'] = 'facility'
            with self.assertRaises(CIMError):
                from_representation(bad, representation)

    def test_unknown_mapping_and_units(self):
        for rule in ({'metric_id': 'unknown', 'unit': 'Wh'}, {'metric_id': self.o['metric_id'], 'unit': 'W'}, None):
            mapping = deepcopy(self.mapping)
            if rule is None:
                del mapping['fields']['WorkloadEnergy']
            else:
                mapping['fields']['WorkloadEnergy'] = rule
            with self.assertRaises(CIMError):
                import_batch(self.records, mapping, self.context)

    def test_invalid_numbers(self):
        for value in (float('nan'), float('inf'), -1, True, '12', None, 1e308):
            record = deepcopy(self.records[0]); record['components'][0]['amount'] = value
            with self.subTest(value=value), self.assertRaises(CIMError):
                import_auditor(record, self.mapping, self.context)

    def test_missing_and_ambiguous_context(self):
        for key in ('site', 'producer', 'accounting_instance', 'instance_type', 'method', 'boundary', 'period', 'scope'):
            ctx = deepcopy(self.context); del ctx[key]
            with self.subTest(key=key), self.assertRaises(CIMError):
                import_batch(self.records, self.mapping, ctx)
        for site in ([], ['SiteA', 'SiteB']):
            record = deepcopy(self.records[0]); record['meta']['site_id'] = site
            with self.assertRaises(CIMError):
                import_auditor(record, self.mapping, self.context)

    def test_invalid_periods(self):
        for start, end in [('bad', 'bad'), ('2026-10-01', '2026-11-01'),
                           ('2026-11-01T00:00:00Z', '2026-10-01T00:00:00Z'),
                           ('2026-10-01T01:00:00+01:00', '2026-11-01T01:00:00+01:00')]:
            o = deepcopy(self.o); o['period'] = {'start': start, 'end': end}
            with self.assertRaises(CIMError): validate(o)
        for start, end in [('2026-10-02T00:00:00Z', '2026-11-01T00:00:00Z'),
                           ('2026-10-01T00:00:00Z', '2026-12-01T00:00:00Z')]:
            o = deepcopy(self.o); o['period'] = {'start': start, 'end': end}
            validate(o)
            with self.assertRaises(CIMError): export_draft([o])

    def test_jobs_validate_but_do_not_export(self):
        o = deepcopy(self.o); o['scope'] = 'job'; o['period']['end'] = '2026-10-01T01:00:00Z'
        validate(o)
        with self.assertRaises(CIMError): export_draft([o])

    def test_no_http_until_entire_batch_valid(self):
        for key, value in [('scope', 'job'), ('site', ['SiteA', 'SiteB']), ('metric_id', 'unknown'), ('vo', None)]:
            batch = deepcopy(self.batch['observations']); batch[1][key] = value
            with patch('cim.send_record') as send, self.assertRaises(CIMError):
                send_batch(batch, 'http://unused')
            send.assert_not_called()
        with patch('cim.send_record') as send, self.assertRaises(CIMError):
            send_batch(self.batch['observations'], 'http://unused', target='official')
        send.assert_not_called()

    def test_target_restrictions(self):
        for key, value in [('benchmark','HEPSPEC06'), ('entry_tags',{'extra':'x'})]:
            o=deepcopy(self.o);o[key]=value
            with self.assertRaises(CIMError): export_draft([o])
        o=deepcopy(self.o);o['provenance']['source']='real-source'
        with self.assertRaises(CIMError): export_draft([o])
        o=deepcopy(self.o);o['observation_id']='another'
        with self.assertRaises(CIMError): export_draft([self.o,o])
        with self.assertRaises(CIMError): import_batch([self.records[0]]*2,self.mapping,self.context)

    def test_delivery_failure_reports_partial_progress(self):
        with patch('cim.send_record', side_effect=[{'status':'received'}, OSError('offline')]) as send:
            with self.assertRaises(DeliveryError) as error:
                send_batch(self.batch['observations'], 'http://unused')
            self.assertEqual(len(error.exception.delivered), 1)
            self.assertEqual(send.call_count, 2)

    def test_explicit_tags_and_source_period(self):
        context=deepcopy(self.context); context['entry_tags']={'method': context['method']}
        self.assertEqual(import_auditor(self.records[0],self.mapping,context)['observations'][0]['entry_tags']['method'], context['method'])
        context['period']={'start':'2026-09-01T00:00:00Z','end':'2026-10-01T00:00:00Z'}
        with self.assertRaisesRegex(CIMError, 'match source'):
            import_auditor(self.records[0],self.mapping,context)

    def test_all_catalogue_terms_validate_without_target_inference(self):
        from cim import catalogue
        for metric_id, metric in catalogue()['metrics'].items():
            o=deepcopy(self.o)
            o.update(metric_id=metric_id,unit=metric['unit'],boundary=metric['boundary'],value=1,
                     original={'value':1,'unit':metric['unit']})
            validate(o)
            if metric_id != self.o['metric_id']:
                with self.assertRaises(CIMError): export_draft([o])

    def test_malformed_context_and_conflicting_component_unit(self):
        for ctx in (dict(self.context, period=[]),dict(self.context,site={'meta':[]}),dict(self.context,unknown='x')):
            with self.assertRaises(CIMError): import_auditor(self.records[0],self.mapping,ctx)
        r=deepcopy(self.records[0]);r['components'][0]['unit']='W'
        with self.assertRaises(CIMError): import_auditor(r,self.mapping,self.context)

    def test_cli_offline_conversion_and_errors(self):
        with TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            output=Path(folder)/'draft.json'; sidecar=Path(folder)/'sidecar.json'
            args=['convert',str(ROOT/'fixtures/grid-auditor.json'),'--input-format','auditor','--mapping',str(ROOT/'src/cim/data/auditor-mapping-v1.json'),'--context',str(ROOT/'fixtures/context.json')]
            with patch('cim.send_record') as send:
                self.assertEqual(main(args+['--output-format','pear-draft-2026-10-27','--output',str(output),'--sidecar',str(sidecar)]),0)
                self.assertEqual(main(['validate',str(ROOT/'fixtures/canonical.json')]),0)
                self.assertEqual(main(['validate',str(output)]),1)
            send.assert_not_called()
            self.assertTrue(sidecar.exists())


if __name__ == '__main__':
    unittest.main()
