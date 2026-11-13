# Internal EIMPS mock contract: eimps-mock-v1

Everything runs locally with Python's standard library. There is no HTTP server,
network request, service deployment, login, token or federation registration.
The dictionary interface models a future JSON `POST /get-metrics` contract.

CIM prepares a request from explicit canonical observation context, validates the
response and appends derived canonical observations. `MockEIMPSClient` owns the
registration check, factor lookups, dependency resolution and calculations.
No formula execution, evaluation of expressions or calculations from user text
are supported. CFP means **operational electricity-related emissions**, excluding
embodied and lifecycle emissions.

## Requests

The envelope has exactly `schema_version`, `type` and `details`:

```json
{
  "schema_version": "eimps-mock-v1",
  "type": "cfp",
  "details": {
    "site_id": "SiteA",
    "period": {"start": "2026-10-01T00:00:00Z", "end": "2026-11-01T00:00:00Z"},
    "location_id": "mock-region-a",
    "energy": {
      "value": 100, "unit": "kWh", "basis": "it",
      "source_observation_id": "example-energy-observation"
    }
  }
}
```

| Type | Required details | Calculation in mock provider |
| --- | --- | --- |
| `pue` | `site_id`, `period` | facility energy / IT energy |
| `ci` | `location_id`, `period` | synthetic location/time factor lookup |
| `cfp` | `site_id`, `location_id`, `period`, `energy` | IT kWh × PUE × CI; facility kWh × CI |

Periods must increase, be explicit UTC and match fixture intervals exactly as
instants. They are end exclusive; there is no interpolation, period aggregation
or fallback. Wh and kWh are supported energy units. `energy.basis` is exactly
`it` or `facility`; explicit value, unit and observation identity are mandatory.
PUE and CI alone do not determine CFP. CIM rejects workload-energy observations
because their overhead attribution cannot be inferred from the existing profile.

`src/cim/data/eimps-mock-v1.json` explicitly represents the data EIMPS is assumed
to possess: paired facility/IT energy inputs with observation references, units,
periods, bases and a common site boundary identifier. Site and time locate those
inputs; they are not themselves the calculation inputs. CI factors explicitly
carry location, time, unit, synthetic flag and `location-based` accounting mode.
These are illustrative data, not real regional emission factors.

The registry maps synthetic IDs to canonical site names (currently identical)
and supported locations. SiteA supports mock-region-a; SiteB supports
mock-region-b. Unknown sites, unsupported locations and inconsistent combinations
fail. CI's service request is location-only; CIM checks the selected attachment
site/location through the same registration interface.

## Responses and attachment

A response contains `schema_version`, `result_id`, `type`, canonical `metric_id`,
`value`, `unit`, `site_id`, `location_id`, `period`, `boundary`, `basis`, `method`,
`formula_version`, `accounting_mode`, `synthetic`, `input_references`,
`factor_references` and `dependencies`. Site/location are null where the metric
does not require them. All responses use `synthetic: true` and
`method: synthetic-eimps`. An actual complete example is retained in each derived
observation in step 05's `enriched.json`.

| Type | Response unit | Boundary | Formula version |
| --- | --- | --- | --- |
| `pue` | `1` | `facility-to-it` | `facility-energy/it-energy:v1` |
| `ci` | `gCO2e/kWh` | `location-electricity` | `synthetic-location-time-lookup:v1` |
| `cfp` | `gCO2e` | `facility-operations` | `electricity-energy*pue-if-it*ci:v1` |

PUE input references include both energy records. CI factor references include
the complete synthetic factor. CFP includes the supplied energy reference and
complete PUE and CI dependency results, each with its own provenance and identity.
Its factor references identify dependency results and whether each was used.
For facility energy PUE is returned for inspection but marked `used: false` and
never multiplied. This minimal profile requires both dependencies to be available
for all CFP responses, including facility energy; it has no partial results.

CIM validates identity, metric, units, site/location, periods, boundary/basis,
formula version, synthetic flag and required input/factor/dependency lineage
before attachment. A deterministic SHA-256 result identity covers response
content; this is an integrity check, not an authentication signature. CIM's
observation identity also includes the attachment context and source observation
identity, so sites, producers and selected inputs stay separate. Identical repeated
enrichment leaves the list unchanged; conflicting existing identities fail.
No mutable cache is used.

Measured observations are copied intact. Derived observations carry
`provenance.derived: true`, `provenance.synthetic: true`, the prepared request,
validated response and attachment observation reference. PUE/CI retain response
units. CFP is converted from gCO2e to the existing canonical kgCO2e unit, retaining
the original response and all dependencies in provenance. The canonical `original`
pair records the converted value/unit to satisfy the existing canonical invariant.
Existing JSON-LD adapters round-trip these observations. PEAR export/send still
accepts only provisional workload energy and rejects enriched batches before any
submission; callers must select supported inputs explicitly.

Missing required inputs, invalid units or basis, unknown registration/context,
unavailable factors, zero IT-energy denominators, incompatible input periods or
boundaries, non-finite/negative numbers and mismatched responses raise `CIMError`.
There are no invented zero emissions, default factors or guessed attribution.

## Replacement boundary

`MetricsClient` exposes `get_metrics(request: dict) -> dict`; inject an alternative
with `enrich(observations, plans, client=client, registration=registration)`.
`Registration` exposes `validate_context(site_id, location_id)`; the mock provider
accepts it too. It checks registered site/location context, returning the site
record when present. Mock registration **is not authenticated authorisation**.

A future implementation can use CIM-Auth and EGI Check-in for identity and
registration claims behind this boundary. Real EIMPS must validate registration
and calculate metrics from its authoritative data. A production contract/version
and corresponding response validation profile must be agreed before accepting
non-synthetic results. No real authentication, network integration, user-defined
formulae, production caching or retry behavior is implemented here.
