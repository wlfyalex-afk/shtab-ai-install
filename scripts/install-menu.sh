#!/bin/bash
# Install only the administration scripts in an existing VM.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
src=$(cd "$(dirname "$0")/.." && pwd)
dst=/opt/shtab-ai
[[ -f $dst/compose.yaml ]] || { echo 'Existing installation not found'; exit 1; }
[[ $src != "$dst" ]] || { echo 'Run from the newly extracted package'; exit 1; }
mkdir -p /var/lib/shtab-ai
exec 9>/var/lib/shtab-ai/install.lock
flock -n 9 || { echo 'Another installer/maintenance operation is running'; exit 1; }
backup="/var/backups/shtab-ai/menu-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$backup"
cp "$dst/shtabctl" "$backup/"
for name in admin.py menu.sh check-https.sh monitor-install.sh resume-install.sh finish-install.sh install-progress.py model_progress.py install_operation.py; do
    if [[ -f $dst/scripts/$name ]]; then cp "$dst/scripts/$name" "$backup/"; fi
    install -m 0755 "$src/scripts/$name" "$dst/scripts/$name"
done
if [[ -f $dst/uninstall.sh ]]; then cp "$dst/uninstall.sh" "$backup/"; fi
install -m 0755 "$src/uninstall.sh" "$dst/uninstall.sh"
install -m 0755 "$src/shtabctl" "$dst/shtabctl"
chmod 600 "$backup/"*
echo 'Меню установлено; контейнеры и данные не изменены.'
echo 'Запуск: sudo /opt/shtab-ai/shtabctl menu'
