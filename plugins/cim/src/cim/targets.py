"""Isolated experimental target serialisation and explicit delivery."""
from copy import deepcopy
from hashlib import sha256
import math
from .semantic import CIMError, period, require, text, validate_batch

TARGET = 'pear-draft-2026-10-27'
WORKLOAD = 'urn:greendigit:cim:draft:workload-energy'


def export_draft(observations, target=TARGET):
    require(target == TARGET, f'unsupported target: {target}')
    validate_batch(observations)
    records, sidecars, identities = [], {}, set()
    for o in observations:
        require(o['metric_id'] == WORKLOAD and o['unit'] == 'Wh', 'target supports only provisional workload energy in Wh')
        require(o['scope'] == 'monthly-summary', 'export requires manually prepared monthly-summary scope')
        start, _ = period(o['period'], monthly=True)
        vo = text(o.get('vo'), 'vo required by target')
        require(o['provenance']['source'] == 'synthetic-prototype', 'raw source group is reserved for the declared synthetic-prototype source')
        require(not o.get('benchmark') and set(o.get('entry_tags', {})) <= {'method'}, 'target cannot represent benchmark or additional entry tags')
        key = {'month': start.strftime('%Y-%m'), 'vo': vo, 'site': o['site'],
               'accounting_instance': o['accounting_instance'], 'instance_type': o['instance_type']}
        identity = tuple(key.values())
        require(identity not in identities, 'duplicate monthly summary key; caller must resolve summaries without re-aggregation')
        identities.add(identity)
        # Local envelope only: not an official accounting schema-version field.
        record_id = 'cim-' + sha256(o['observation_id'].encode()).hexdigest()
        payload = {'key': key, 'values': {'raw': [{'metric': 'energy_consumed', 'value': o['value'],
                   'unit': 'Wh', 'tags': {'method': o['method']}}]}}
        records.append({'record_id': record_id, 'accounting': payload})
        sidecars[record_id] = deepcopy(o)
    return {'target': target, 'records': records, 'sidecar': sidecars}


class DeliveryError(CIMError):
    def __init__(self, delivered, failed_record_id, cause):
        self.delivered = delivered
        self.failed_record_id = failed_record_id
        super().__init__(f'delivery failed for {failed_record_id}; acknowledged IDs: {delivered}; receiver is not idempotent: {cause}')


def send_batch(observations, pear_url, *, target=TARGET, timeout=10):
    """Preflight the entire batch before the first HTTP request; stop on failure."""
    from . import send_record
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0, 'timeout must be finite and positive')
    text(pear_url, 'pear_url')
    draft = export_draft(observations, target)
    delivered = []
    for record in draft['records']:
        try:
            send_record(record, pear_url, timeout=timeout)
        except Exception as exc:
            raise DeliveryError(delivered, record['record_id'], exc) from exc
        delivered.append(record['record_id'])
    return {'acknowledged': delivered, 'sidecar': draft['sidecar']}
