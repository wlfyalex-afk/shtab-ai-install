#!/bin/bash
# Always include the selected acceleration settings, including maintenance commands.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
args=(-f "$root/compose.yaml")
if [[ -f $root/storage.json ]]; then python3 "$root/scripts/configure-storage.py" verify; fi
[[ ! -f $root/compose.storage.yaml ]] || args+=(-f "$root/compose.storage.yaml")
[[ ! -f $root/compose.cache.yaml ]] || args+=(-f "$root/compose.cache.yaml")
[[ ! -f $root/compose.gpu.yaml ]] || args+=(-f "$root/compose.gpu.yaml")
exec docker compose "${args[@]}" "$@"
