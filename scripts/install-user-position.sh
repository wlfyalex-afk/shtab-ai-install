#!/bin/bash
# Update the user position forms on an existing 021 VM.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустите через sudo.'; exit 1; }
src=$(cd "$(dirname "$0")/.." && pwd)
dst=/opt/shtab-ai-021
[[ -f $dst/compose.yaml && -f $dst/app/user_admin010.py ]] || { echo 'Установка 021 не найдена.'; exit 1; }
[[ $src != "$dst" ]] || { echo 'Запустите скрипт из нового распакованного пакета.'; exit 1; }
exec 9>/var/lib/shtab-ai-021/install.lock
flock -n 9 || { echo 'Другая операция обслуживания ещё работает.'; exit 1; }
cd "$dst"
active=$(systemctl show shtab-ai-install.service -p ActiveState --value)
[[ $active != active && $active != activating ]] || { echo 'Дождитесь завершения установщика.'; exit 1; }
image=$(docker compose -f compose.yaml config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["web"]["image"])')
old_image=$(docker image inspect "$image" --format '{{.Id}}')
backup="/var/backups/shtab-ai-021/users-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$backup/templates"
cp "$dst/app/user_admin010.py" "$backup/"
cp "$dst/app/templates/users010.html" "$backup/templates/"
chmod 0600 "$backup/user_admin010.py" "$backup/templates/users010.html"
rollback() {
    trap - ERR
    echo 'Обновление не завершено. Возвращаем прежние файлы и образ web.' >&2
    cp "$backup/user_admin010.py" "$dst/app/user_admin010.py"
    cp "$backup/templates/users010.html" "$dst/app/templates/users010.html"
    docker image tag "$old_image" "$image" || true
    docker compose -f compose.yaml up -d --no-deps --wait --wait-timeout 120 web || true
    echo "Сохранённые файлы: $backup" >&2
    exit 1
}
trap rollback ERR
install -m 0644 "$src/app/user_admin010.py" "$dst/app/user_admin010.py"
install -m 0644 "$src/app/templates/users010.html" "$dst/app/templates/users010.html"
echo 'Пересборка web; база данных и записи сохраняются.'
docker compose -f compose.yaml build web
docker compose -f compose.yaml up -d --no-deps --wait --wait-timeout 120 web
docker compose -f compose.yaml exec -T web python manage.py check
trap - ERR
echo 'Готово. Обновите страницу «Пользователи» (Ctrl+F5).'
echo "Прежние файлы сохранены: $backup"
