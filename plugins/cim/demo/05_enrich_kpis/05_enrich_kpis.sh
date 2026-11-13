#!/usr/bin/env bash
set -euo pipefail
step_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python -m cim enrich "$step_dir/energy-inputs.json" --requests "$step_dir/requests.json" --output "$step_dir/enriched.json"
python "$step_dir/check_kpis.py"
