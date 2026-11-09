# Mock CIM library

Install locally beside AUDITOR's exporter with `pip install -e plugins/cim`.
This is a temporary Python library, not a separate service or a real CIM
implementation. `cim.send_record(record, pear_url)` passes an AUDITOR record
unchanged to PEAR's receive-only HTTP endpoint and returns its acknowledgement.
HTTP errors and invalid acknowledgements fail the call. There is no background
queue, retry policy, aggregation, authentication, or accounting transformation.

The runnable example and replacement boundary are documented in the sibling
`pear-prototype/validation/README.md`.
