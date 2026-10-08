#!/bin/bash
# Always include the selected acceleration settings, including maintenance commands.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
args=(-f "$root/compose.yaml")
[[ ! -f $root/compose.gpu.yaml ]] || args+=(-f "$root/compose.gpu.yaml")
exec docker compose "${args[@]}" "$@"
