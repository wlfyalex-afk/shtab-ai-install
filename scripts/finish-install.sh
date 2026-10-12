#!/bin/bash
# Finish native Linux setup after background provisioning, including a resumed run.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустите с sudo.'; exit 1; }
cd /opt/shtab-ai
# Discard only queued terminal keystrokes, never piped input.
clear_pending_input() {
    python3 -c 'import sys,termios; sys.stdin.isatty() and termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)' || true
}
trap 'clear_pending_input; echo "Настройка не завершена. Данные сохранены. Повтор: sudo /opt/shtab-ai/shtabctl finish" >&2' ERR
if [[ ${1:-} == --wait ]]; then
    python3 scripts/install-progress.py --timeout 10800
fi
status=$(cat /var/lib/shtab-ai/status 2>/dev/null || true)
[[ $status == READY_FOR_ADMIN ]] || {
    echo 'Компоненты ещё не готовы. Продолжение: sudo /opt/shtab-ai/shtabctl resume'
    echo 'Журнал: sudo /opt/shtab-ai/shtabctl logs'
    exit 1
}
# READY is written just before provisioning exits; wait for its lock to release.
exec 9>/var/lib/shtab-ai/install.lock
flock -w 30 9 || { echo 'Установка или обслуживание ещё работает. Повторите завершение позже.'; exit 1; }
echo 'Штаб.AI — завершаем настройку: HTTPS → сертификат → администратор → ярлык.'
bash scripts/check-https.sh
certificate=/usr/local/share/ca-certificates/shtab-ai.crt
[[ ! -e $certificate ]] || cmp -s "$certificate" shtab-ai-root.crt || {
    echo 'Другой сертификат с таким именем уже существует; остановлено без перезаписи.'
    exit 1
}
echo 'Добавляем корневой сертификат в доверенные сертификаты Linux.'
install -m 0644 shtab-ai-root.crt "$certificate"
update-ca-certificates
user_count=$(bash scripts/dc.sh exec -T db psql -U shtab_ai -d shtab_ai -Atc 'SELECT count(*) FROM secretary_users')
[[ $user_count =~ ^[0-9]+$ ]] || { echo 'Не удалось проверить наличие администратора.'; exit 1; }
if [[ $user_count == 0 ]]; then
    [[ -t 0 && -t 1 ]] || {
        echo 'Для создания администратора нужен интерактивный терминал.'
        echo 'Запустите: sudo /opt/shtab-ai/shtabctl finish'
        exit 1
    }
    clear_pending_input
    echo 'Создаём первого администратора. Сейчас появятся вопросы; пароль при вводе не отображается.'
    bash scripts/dc.sh exec web python bootstrap.py
else
    echo 'Администратор уже создан; пользователи сохранены.'
fi
python3 scripts/create-desktop-shortcut.py /opt/shtab-ai "${SUDO_USER:-}"
host=$(sed -n 's/^SHTAB_HTTPS_HOST=//p' .env | tail -1)
mode=$(cat acceleration)
access=$(cat access-mode 2>/dev/null || echo local)
echo "Настройка завершена. Откройте в браузере: https://$host/login. Режим: $mode."
if [[ $access == lan ]]; then
    echo 'На других ПК добавьте в доверенные публичный сертификат shtab-ai-root.crt из домашней папки пользователя.'
    echo 'Доступ с другого ПК и доверие его браузера проверяются отдельно.'
fi
clear_pending_input
