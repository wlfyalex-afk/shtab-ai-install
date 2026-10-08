#!/bin/bash
# Only this project's resources; never prune Docker or uninstall Docker itself.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
mode=${1:---keep-data}
case "$mode" in
  --keep-data|--fresh|--purge-all) ;;
  *) echo 'Usage: sudo bash uninstall.sh [--keep-data|--fresh|--purge-all]'; exit 2 ;;
esac
cd /opt/shtab-ai-021
[[ -f compose.yaml ]] || { echo 'Installation not found'; exit 1; }
state=/var/lib/shtab-ai-021
mkdir -p "$state"
exec 9>"$state/install.lock"
flock -n 9 || { echo 'Installer is running; wait for completion'; exit 1; }
active=$(systemctl show shtab-ai-install.service -p ActiveState --value 2>/dev/null || true)
[[ $active != activating && $active != active ]] || { echo 'Installation service is running; wait'; exit 1; }
dc() { bash scripts/dc.sh "$@"; }
dc config --quiet
project=$(dc config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
[[ $project == shtab-ai-021 ]] || { echo 'Unexpected project; no changes'; exit 1; }
# Validate every volume before deleting any. No dynamically supplied path enters rm.
for key in db_data app_data asr_models ollama_models; do
    name="shtab-ai-021_$key"
    labels=$(docker volume inspect "$name" --format '{{ index .Labels "com.docker.compose.project" }}:{{ index .Labels "com.docker.compose.volume" }}')
    [[ $labels == "shtab-ai-021:$key" ]] || { echo "Unexpected volume ownership: $name"; exit 1; }
done
if [[ $mode != --keep-data ]]; then
    echo 'Will back up database, uploaded recordings and installation files, then delete database and app-data volumes.'
    if [[ $mode == --purge-all ]]; then
        echo 'Downloaded Qwen/Whisper model volumes will also be deleted (not backed up).'
    else
        echo 'Downloaded models will be kept for the next installation.'
    fi
    read -r -p 'Type DELETE-SHTAB-021 to continue: ' confirmation
    [[ $confirmation == DELETE-SHTAB-021 ]] || { echo 'Cancelled; no changes'; exit 0; }
fi
backup="/var/backups/shtab-ai-021/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$backup"
umask 077
if [[ $mode != --keep-data ]]; then
    # Refuse to interrupt uploads/recognition/model processing.
    python3 - <<'PYCODE'
import runpy
runpy.run_path('scripts/admin021.py')['ensure_idle']()
PYCODE
fi
# Freeze Caddy certificate storage before archiving its keys/config.
# Use Compose service inventory (compatible with pre-HTTPS installs).
if dc config --services | grep -qx proxy; then dc stop proxy; fi
# Keep code/config/secrets for recovery even in keep-data mode.
tar -czf "$backup/installation.tar.gz" -C /opt shtab-ai-021
journalctl -u shtab-ai-install --no-pager > "$backup/install.log"
if [[ $mode != --keep-data ]]; then
    # Need local helper image and live DB before stopping application writers.
    docker image inspect alpine:3.20 >/dev/null
    dc exec -T db pg_isready -U shtab_ai -d shtab_ai >/dev/null
    dc stop web meeting-worker llm-worker brief-worker
    python3 - "$backup" <<'PYCODE'
from pathlib import Path
import runpy,sys
module = runpy.run_path('scripts/admin021.py')
module['ensure_idle']()
module['make_backup'](Path(sys.argv[1]))
PYCODE
fi
# No volumes removed by Compose: specific, validated names below.
dc down --remove-orphans
systemctl disable shtab-ai-install.service 2>/dev/null || true
rm -f /etc/systemd/system/shtab-ai-install.service
systemctl daemon-reload
if [[ $mode != --keep-data ]]; then
    docker volume rm shtab-ai-021_db_data shtab-ai-021_app_data
    if [[ $mode == --purge-all ]]; then
        docker volume rm shtab-ai-021_asr_models shtab-ai-021_ollama_models
    fi
    # Keep installed sources rather than rm a directory containing the running script.
    mv /opt/shtab-ai-021 "$backup/installed-directory"
    rm -f "$state/status" "$state/ollama-models.txt" "$state/images.json"
else
    printf 'UNINSTALLED_DATA_PRESERVED\n' > "$state/status"
fi
find "$backup" -type f -exec chmod 600 {} +
echo "Completed: $mode"
echo "Backup (root only): $backup"
echo 'Docker, Ubuntu, unrelated containers and Docker image cache remain installed.'
