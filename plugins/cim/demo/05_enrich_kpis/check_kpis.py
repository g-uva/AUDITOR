"""Verify combined/independent KPIs and unchanged inputs, entirely offline."""
import json
from pathlib import Path
from cim import enrich, CIMError, export_draft

ROOT = Path(__file__).resolve().parent
inputs = json.loads((ROOT / 'energy-inputs.json').read_text())['observations']
plans = json.loads((ROOT / 'requests.json').read_text())
actual = json.loads((ROOT / 'enriched.json').read_text())['observations']
assert actual[:len(inputs)] == inputs
assert enrich(actual, plans) == actual
for plan, derived in zip(plans, actual[len(inputs):]):
    assert enrich(inputs, [plan])[-1] == derived
    response = derived['provenance']['response']
    expected = {'SiteA': {'pue': 1.5, 'ci': 200, 'cfp': 30000},
                'SiteB': {'pue': 1.25, 'ci': 400, 'cfp': 100000}}
    assert response['value'] == expected[derived['site']][plan['type']]
try:
    export_draft(actual)
except CIMError:
    pass
else:
    raise AssertionError('KPI observations must not be exported to PEAR')
print('KPI check: independent/combined, two sites, IT/facility CFP, repeat enrichment and PEAR rejection passed')
