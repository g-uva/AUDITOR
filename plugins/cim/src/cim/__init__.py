"""CIM local library: explicit harmonisation plus the established transport."""

import json
from urllib.request import Request, urlopen


def send_record(record: dict, pear_url: str, *, timeout: float = 10) -> dict:
    """Pass an AUDITOR record unchanged to PEAR; propagate delivery failures."""
    request = Request(
        pear_url.rstrip("/") + "/records",
        data=json.dumps(record).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        receipt = json.load(response)
    if not isinstance(receipt, dict) or receipt.get("record_id") != record["record_id"] or receipt.get("status") != "received":
        raise ValueError("PEAR did not acknowledge the submitted record")
    return receipt


from .semantic import CIMError, catalogue, import_auditor, import_batch, validate, validate_batch
from .representations import from_representation, to_representation
from .targets import DeliveryError, export_draft, send_batch
