#!/bin/bash
# Continue the installed version with its original secrets, volumes and cache.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустите с sudo.'; exit 1; }
cd /opt/shtab-ai-021
for file in installation-created .env compose.yaml secrets/db_password secrets/flask_secret scripts/provision.sh; do
    [[ -f $file ]] || { echo "Неполная конфигурация установки: $file. Данные сохранены."; exit 1; }
done
if [[ -f storage.json ]]; then python3 scripts/configure-storage.py verify; fi
unit=shtab-ai-install.service
systemctl cat "$unit" >/dev/null
active=$(systemctl show "$unit" -p ActiveState --value)
if [[ $active == activating || $active == active ]]; then
    echo 'Установка уже идёт; подключаемся к её прогрессу.'
    exit 0
fi
status=$(cat /var/lib/shtab-ai-021/status 2>/dev/null || true)
if [[ $status == READY_FOR_ADMIN ]]; then
    echo 'Компоненты установлены; завершаем настройку.'
    exit 0
fi
if [[ -n ${1:-} ]]; then
    # WSL's NAT gateway may have changed after a reboot. Do this only while idle.
    python3 - "$1" "${2:-localhost}" <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('runtime_config', 'scripts/runtime-config.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.reconcile(Path('/opt/shtab-ai-021'), sys.argv[1], sys.argv[2], 'READY_FOR_ADMIN')
PY
fi
echo 'Продолжаем установку. Полученные файлы моделей и данные сохраняются.'
systemctl reset-failed "$unit"
# Avoid reading a stale FAILED status before the background process starts.
printf 'RESUMING\n' > /var/lib/shtab-ai-021/status
systemctl start --no-block "$unit"
