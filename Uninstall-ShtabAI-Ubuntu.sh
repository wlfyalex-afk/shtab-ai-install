#!/bin/bash
# Fully delete only this application's resources; shared Docker/drivers stay installed.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then exec sudo bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"; fi
root=/opt/shtab-ai-021
self=$(readlink -f "${BASH_SOURCE[0]}")
# The manager may run this file from the mount being removed. Release its open script descriptor first.
if [[ $self == "$root/"* ]]; then
    temporary=$(mktemp /var/tmp/shtab-remove.XXXXXX.sh)
    cp "$self" "$temporary"
    cd /
    exec bash "$temporary" "$@"
fi
[[ $self != /var/tmp/shtab-remove.*.sh ]] || trap 'rm -f "$self"' EXIT
cd /
if [[ ! -d $root ]]; then echo 'Штаб.AI не установлен.'; exit 0; fi
[[ ! -L $root && ( -f $root/installation-created || -f $root/storage.json ) ]] || { echo 'Не удалось подтвердить принадлежность каталога. Ничего не удалено.'; exit 1; }
selected=$root
mount_unit=''
if [[ -f $root/storage.json ]]; then
    python3 "$root/scripts/configure-storage.py" verify
    selected=$(python3 -c 'import json; print(json.load(open("/opt/shtab-ai-021/storage.json"))["root"])')
    mount_unit=$(python3 -c 'import json; print(json.load(open("/opt/shtab-ai-021/storage.json"))["mount_unit"])')
fi
if find "$root" -type l -print -quit | read -r _; then echo 'В каталоге есть символические ссылки. Автоматическое удаление остановлено.'; exit 1; fi
volumes=()
if command -v docker >/dev/null && ! docker info >/dev/null 2>&1; then
    echo 'Docker недоступен. Запустите Docker и повторите удаление: контейнеры и данные пока сохранены.'; exit 1
fi
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    for key in db_data app_data asr_models ollama_models; do
        name="shtab-ai-021_$key"
        if docker volume inspect "$name" >/dev/null 2>&1; then
            label=$(docker volume inspect "$name" --format '{{ index .Labels "com.docker.compose.project" }}:{{ index .Labels "com.docker.compose.volume" }}')
            [[ $label == "shtab-ai-021:$key" ]] || { echo "Неожиданный владелец тома: $name"; exit 1; }
            volumes+=("$name")
        fi
    done
    if [[ -f $root/compose.yaml ]]; then bash "$root/scripts/dc.sh" config --quiet; fi
fi
echo 'Будут удалены Штаб.AI, пользователи, записи, результаты и данные внутри папки приложения. Резервная копия не создаётся.
Постоянный кэш моделей вне приложения и ранее созданные резервные копии сохраняются.'
read -r -p 'Для полного удаления введите DELETE-SHTAB-021: ' answer
[[ $answer == DELETE-SHTAB-021 ]] || { echo 'Отменено.'; exit 0; }
systemctl stop shtab-ai-install.service 2>/dev/null || true
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    if [[ -f $root/compose.yaml ]]; then bash "$root/scripts/dc.sh" down --remove-orphans; fi
    if [[ ${#volumes[@]} -gt 0 ]]; then docker volume rm "${volumes[@]}"; fi
    # Remove only the application image when no remaining container references it.
    for image in shtab-ai-021-app:0.21.0-rc3 shtab-ai-021-app:0.21.0-rc3-nvidia; do
        if docker image inspect "$image" >/dev/null 2>&1 && [[ -z $(docker ps -aq --filter "ancestor=$image") ]]; then
            docker image rm "$image" || true
        fi
    done
fi
if [[ -f $root/lan-address && -f $root/lan-subnet ]]; then bash "$root/scripts/network-firewall.sh" remove; fi
for service in shtab-ai-network.service shtab-ai-install.service; do
    systemctl disable --now "$service" 2>/dev/null || true
    rm -f "/etc/systemd/system/$service"
done
systemctl daemon-reload
systemctl reset-failed shtab-ai-network.service shtab-ai-install.service 2>/dev/null || true
python3 - "$root" <<'PY'
import json
from pathlib import Path
import sys
root=Path(sys.argv[1]); metadata=root/'desktop-shortcut.json'
if metadata.exists():
    record=json.loads(metadata.read_text()); path=Path(record['path'])
    if path.name=='ShtabAI.desktop' and path.is_file() and not path.is_symlink():
        text=path.read_text()
        if '\nURL='+record['url']+'\n' in text: path.unlink()
    if record.get('manager'):
        manager=Path(record['manager'])
        if manager.name=='ShtabAI-Manager.desktop' and manager.is_file() and not manager.is_symlink():
            if '\nExec=sudo /opt/shtab-ai-021/shtabctl menu\n' in manager.read_text(): manager.unlink()
PY
certificate=/usr/local/share/ca-certificates/shtab-ai-021.crt
if [[ -f $certificate && -f $root/shtab-ai-root.crt ]] && cmp -s "$certificate" "$root/shtab-ai-root.crt"; then
    rm -f "$certificate"
    update-ca-certificates
fi
if [[ -n $mount_unit ]]; then
    expected_unit=$(systemd-escape -p --suffix=mount "$root")
    [[ $mount_unit == "$expected_unit" ]] || { echo 'Неожиданная mount unit; удаление остановлено.'; exit 1; }
    systemctl disable --now "$mount_unit"
    rm -f "/etc/systemd/system/$mount_unit"
    systemctl daemon-reload
    rmdir "$root"
    [[ -f $selected/storage.json && -d $selected/storage ]] || { echo 'Нельзя подтвердить каталог данных после отключения mount.'; exit 1; }
    rm -rf -- "$selected"
else
    rm -rf -- /opt/shtab-ai-021
fi
rm -rf -- /var/lib/shtab-ai-021
echo 'Штаб.AI полностью удалён. Постоянный кэш моделей вне приложения, резервные копии, Docker, драйверы и другие приложения сохранены.'
