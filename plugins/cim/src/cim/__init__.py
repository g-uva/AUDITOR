"""Mock CIM boundary. Replace send_record when the real CIM is available."""

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
    if receipt.get("record_id") != record["record_id"] or receipt.get("status") != "received":
        raise ValueError("PEAR did not acknowledge the submitted record")
    return receipt
