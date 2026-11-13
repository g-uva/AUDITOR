# CIM: local metric harmonisation MVP

CIM is a Python library with a thin CLI. It imports explicitly mapped AUDITOR
records into a **local, versioned GreenDIGIT draft profile**, validates observations,
and exports one experimental PEAR draft. It has no runtime dependencies outside
the Python 3.11+ standard library. It runs inside the exporter or as a one-shot
command; it needs no service or setup wizard.

The existing `cim.send_record(record, pear_url, timeout=10)` transport remains
available and sends its argument unchanged to `/records`. A receiver receipt
confirms JSON storage only, not accounting-schema conformance.

## Installation

From `~/ensure-forks`:

```bash
python3 -m venv /tmp/cim-venv
/tmp/cim-venv/bin/pip install -e ./AUDITOR/plugins/cim
source /tmp/cim-venv/bin/activate
cd AUDITOR/plugins/cim
```

For embedded use, install that same local package into the exporter's environment:
`/path/to/exporter/venv/bin/pip install -e ~/ensure-forks/AUDITOR/plugins/cim`.
The sibling `pear-prototype/validation/requirements.txt` already installs it as an
exporter dependency. The distribution name remains `auditor-cim-mock` for existing
installations; version 0.2.0 provides the `cim` import and executable. No Rust server
or APEL exporter dependency is changed.

## Offline demonstration

For numbered scripts and inspectable outputs at every stage, use the
[step-by-step demo](demo/README.md), including both SAREF and Schema.org round trips.

Run from this package directory after installation:

```bash
cim validate fixtures/monthly-auditor.json --input-format auditor \
  --mapping src/cim/data/auditor-mapping-v1.json --context fixtures/context.json
cim convert fixtures/grid-auditor.json --input-format auditor \
  --mapping src/cim/data/auditor-mapping-v1.json --context fixtures/context.json \
  --output /tmp/cim-canonical.json
cim convert /tmp/cim-canonical.json --output-format pear-draft-2026-10-27 \
  --output /tmp/cim-draft.json --sidecar /tmp/cim-provenance.json
cim convert /tmp/cim-canonical.json --output-format saref --output /tmp/cim-saref.json
cim convert /tmp/cim-saref.json --input-format saref --output /tmp/cim-roundtrip.json
cim convert /tmp/cim-canonical.json --output-format schemaorg --output /tmp/cim-schemaorg.json
```

Validation and conversion never perform HTTP. The synthetic AUDITOR fixture is
already a manually prepared October 2026 summary: 12.5 kWh becomes 12,500 Wh.
The grid batch adds SiteB at 7,000 Wh, keeping the same central producer and
separate site/accounting-instance identities. These records illustrate dictionaries
retrieved from AUDITOR; this demonstration does not submit them to AUDITOR.
`fixtures/canonical.json`, `saref.json`, `schemaorg.json` and
`pear-draft-golden.json` show the expected results.

A separate, explicit send (only against a deliberately started test receiver):

```bash
cim send /tmp/cim-canonical.json --pear-url http://127.0.0.1:18081 \
  --sidecar /tmp/cim-send-provenance.json
```

Start that isolated receiver in a separate terminal from `~/ensure-forks/pear-prototype`:
`python3 -m pear_receiver --port 18081 --output /tmp/cim-test-received.jsonl`.
Choose an unused port. CIM never starts or stops services. The CLI writes the
sidecar before sending. Delivery errors identify the failed ID and acknowledged
IDs; there is no retry, checkpoint, transaction or rollback. The receiver appends
duplicates even for identical record IDs, so a retry can double-count.

## Library and explicit mapping

```python
import json
from pathlib import Path
from cim import import_batch, validate_batch, export_draft, send_batch

mapping = json.loads(Path('src/cim/data/auditor-mapping-v1.json').read_text())
context = json.loads(Path('fixtures/context.json').read_text())
# retrieved_records is a list of dictionaries fetched by the exporter from AUDITOR.
result = import_batch(retrieved_records, mapping, context)
validate_batch(result['observations'])
draft = export_draft(result['observations'])
Path('/tmp/provenance.json').write_text(json.dumps(draft['sidecar'], indent=2))
# Explicit opt-in only: send_batch(result['observations'], test_receiver_url)
```

`import_auditor(record, mapping, context)` handles a single retrieved dictionary;
`import_batch` validates the complete batch and rejects duplicate observation IDs.
Both return `observations` and `ignored_fields`. `validate` validates one canonical
observation; `validate_batch`, `export_draft` and `send_batch` accept observation
lists. Errors use `CIMError` (`ValueError`); delivery errors expose `delivered` and
`failed_record_id`. CLI errors are readable on stderr with a non-zero exit code.

Definitions in `src/cim/data/catalogue-v1.json` are separate from observations.
Each has an identifier, meaning, quantity kind, unit, boundary, allowed context
and aggregation semantics. The original five local terms cover total facility energy,
IT energy, water input, operational CO2e and separately provisional workload
energy. Their URNs identify this implementation's draft, not approved ontology
terms. The chapter itself was not supplied in the workspace: definitions are
conservative seeds from the brief's named topics and require project review.

Mappings in `auditor-mapping-v1.json` explicitly declare source component names,
units, source identity and version. Unknown components/metrics fail; ignored
components require reasons and are returned/reported. `Cores` is explicitly ignored
in the synthetic example. Never map CPU time or power to energy by name similarity.
A component is not assumed to contain its unit. Only declared kWh → Wh and
identity conversions are supported; no power/time integration is implemented.

Context values are literal values or exactly one selector: `{"meta": "site_id"}`
requires a single metadata value; `{"field": "start_time"}` reads a top-level field.
Period has explicit `start` and `end` bindings, end exclusive. When source
timestamps are present, the period must match them; context cannot widen a job
interval into a month. `entry_tags` is an object of tag names to literal values or
selectors. The caller supplies
site, producer, accounting instance/type, scope, boundary and method. Producer
is independent of site. VO is optional canonically but required by this target;
benchmark and entry tags are optional and must only be supplied when relevant.
Missing values are not defaulted to invented values. The importer retains source
record/observation IDs, the full source dictionary, original value/unit and
catalogue/mapping versions. Canonical values must agree with the retained original.
All current metrics require finite non-negative values; booleans and numeric
strings are rejected. UTC periods must increase. Duplicate components and
multi-valued site bindings fail rather than splitting or guessing site identity.

The caller must truthfully declare `job` or `monthly-summary` scope. An individual
job can be imported/validated, but cannot be exported by this target. A summary
must have an explicit full UTC calendar-month period, with a first-of-month
midnight start and next-month midnight end. CIM never aggregates: prepare monthly
summaries in the exporter/caller. Duplicate summary keys are rejected, including
summaries from different producers for the same target key. Sites are never merged.

## Fixed JSON/JSON-LD subsets

These are representation adapters, not a general RDF parser. Both accept a single
object or CLI list of objects with the exact fixed JSON-LD 1.1 `@context` shown in their
fixtures. There are no remote contexts, runtime downloads or ontology server.
`@id` retains the observation ID; `gd:metadata` retains all other canonical fields
including boundary, original values, provenance and versions, as an `@json` literal. This local extension
is necessary for lossless semantic round trips; bare generic PropertyValue objects
without the explicit metadata are insufficient and rejected. Additional document
fields are rejected to avoid silent semantic loss.

Verified against [SAREF Core 4.1.1](https://saref.etsi.org/core/v4.1.1/):

| Canonical field | SAREF subset |
| --- | --- |
| Value node | `saref:PropertyValue` |
| `metric_id` | `saref:isValueOfProperty` with `@id` |
| `value` | `saref:hasValue` |
| `unit` | `saref:isMeasuredIn` with a local draft unit URN |

The namespace is `https://saref.etsi.org/core/`, with terms pinned here to 4.1.1.
In particular, this adapter does not use the older `Measurement`/`hasUnit` pattern.

Verified against [Schema.org PropertyValue](https://schema.org/PropertyValue):

| Canonical field | Schema.org subset |
| --- | --- |
| Value node | `schema:PropertyValue` |
| `metric_id` | `schema:propertyID` |
| `value` | `schema:value` |
| `unit` | `schema:unitText` |

`unitText` deliberately retains the profile's unit symbols without inventing
UN/CEFACT codes. Neither adapter asserts `owl:sameAs`, exact equivalence to generic
classes or approved GreenDIGIT ontology alignment. Metric identity remains the
local project term; the boundary is never reconstructed from a generic ontology.

## Experimental PEAR contract and gaps

Only `pear-draft-2026-10-27` is supported. Based on the five key fields described
in the brief, the **local assumed envelope** is:

```json
{
  "record_id": "cim-<sha256 of observation ID>",
  "accounting": {
    "key": {"month": "2026-10", "vo": "example-vo", "site": "SiteA",
            "accounting_instance": "ce-a.example", "instance_type": "grid"},
    "values": {"raw": [{"metric": "energy_consumed", "value": 12500,
                        "unit": "Wh", "tags": {"method": "synthetic-monthly-summary"}}]}
  }
}
```

`accounting`, `key`, `metric`, `tags`, the YYYY-MM month encoding, transport ID
construction and unit placement are local serialisation assumptions; the original
presentation was not available to confirm these spellings. The golden fixture
pins them for review, not for a claim of official PEAR conformance. `target` in the
local conversion result identifies the adapter and is not sent as a schema-version
field. The transport wrapper's `record_id` lets the unchanged receiver acknowledge
the accounting payload. No official schema-version value is invented.

The only target metric is provisional workload energy, exported as
`energy_consumed`/Wh, with `method` as an entry tag. `raw` is allowed only for the
explicit `synthetic-prototype` source. Real source group naming awaits PEAR
agreement; other sources fail. Facility metrics validate canonically but are not
silently repurposed as workload energy. Benchmarks and additional entry tags fail
export until a target mapping is agreed. KPI enrichment below adds PUE, CI and CFP locally; none has a PEAR target mapping. WUE and CUE remain unimplemented.

Producer stays in the canonical observation and provenance sidecar, outside the
five-field key. The later producer-in-key/flat-values proposal remains unresolved.
The sidecar is keyed by the transport ID and retains the entire observation.
`export_draft` returns it; library callers must persist it before `send_batch`.
The CLI requires `--sidecar` for draft conversion/send. Target serialisation lives
in `targets.py` so a future agreed contract can replace it independently.

Remaining integration gaps include approved metric/boundary definitions, agreement
on target field shapes and source names, an official schema/version, downstream
accounting validation, producer identity treatment and idempotent delivery. The
existing receiver checks only a non-empty `record_id`, stores JSON and acknowledges
receipt; it does not validate this draft or feed it into APEL/MONIT. No dashboards,
databases, polling, automatic mapping, generic aggregation or production delivery
infrastructure are added.

## Focused checks

From `~/ensure-forks/AUDITOR`, after installation:

```bash
python -m unittest discover -s plugins/cim/tests -v
python -m unittest discover -s ../pear-prototype/validation -p 'test_*.py' -v
```

The second suite needs the exporter's existing PyYAML dependency. HTTP checks bind
isolated loopback receivers on ephemeral ports and write temporary files. No live
submissions are used. The old job transport demo remains in the sibling
[validation documentation](../../../pear-prototype/validation/README.md).

## Offline CIM-KPI enrichment

`cim enrich` and `cim.enrich` add distinct derived observations without changing
measured inputs. Run the additional [step 05](demo/05_enrich_kpis/README.md), which
keeps the original workload-energy/PEAR demo intact. This step uses separate,
explicit IT and facility energy fixtures; it never relabels workload energy.

```bash
cim enrich demo/05_enrich_kpis/energy-inputs.json \
  --requests demo/05_enrich_kpis/requests.json --output /tmp/cim-enriched.json
```

The `--requests` file contains CIM selection plans, **not** raw service requests:
`[{"type": "cfp", "source_observation_id": "mock-SiteA-it-2026-10",
"location_id": "mock-region-a"}]`. The selected canonical observation supplies
site, period, energy value/unit and basis. PUE plans omit location; CI and CFP
plans require it. PUE and CI can use any valid observation as their site/period
attachment context. CFP accepts only explicit IT or facility energy metrics.

```python
from cim import enrich, MockEIMPSClient

# observations are a validated canonical list; plans select explicit observations.
enriched = enrich(observations, plans, client=MockEIMPSClient())
```

See [the mock contract](docs/eimps-mock.md) for units, response lineage, explicit
failure behavior and replacement interfaces. New local PUE and CI definitions
extend the draft catalogue; CFP reuses operational CO2e (kgCO2e). All derived
observations remain unsupported by PEAR export/send, which fails preflight.
