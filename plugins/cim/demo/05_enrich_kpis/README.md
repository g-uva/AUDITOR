# Step 05: offline CIM-KPI mock enrichment

After the existing four demo steps, run from the installed package environment:

```bash
bash demo/05_enrich_kpis/05_enrich_kpis.sh
```

`demo/run_all.sh` also discovers this step. `energy-inputs.json` contains explicit
synthetic canonical IT/facility observations, separate from the earlier demo's
workload inputs. `requests.json` selects PUE, CI and CFP for each site's IT input,
and CFP for each site's facility input. `enriched.json` preserves all four inputs
and appends eight distinct derived observations, including full request/response
and dependency provenance. `check_kpis.py` also runs every selection independently.

| Site | IT kWh | Facility kWh | PUE | CI gCO2e/kWh | CFP gCO2e from either energy basis |
| --- | --- | --- | --- | --- | --- |
| SiteA | 100 | 150 | 1.5 | 200 | 30,000 |
| SiteB | 200 | 250 | 1.25 | 400 | 100,000 |

CFP is attached using CIM's existing kgCO2e definition: 30 and 100 kgCO2e.
Provider responses retain gCO2e. For facility energy the returned PUE dependency
is marked unused. The check verifies unchanged inputs, independent/combined
results, site separation, equal IT/facility CFP, repeat enrichment and PEAR
rejection. No submissions occur. See the [contract](../../docs/eimps-mock.md).
