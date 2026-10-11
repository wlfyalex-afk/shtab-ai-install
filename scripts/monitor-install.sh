#!/usr/bin/env bash
# Read-only installation monitor for Ubuntu / Shtab.AI.
set -u
if [[ ${1:-} == --help ]]; then
    printf 'Запуск: bash %s\n' "${BASH_SOURCE[0]##*/}"
    printf 'Разовая сводка: bash %s --once\n' "${BASH_SOURCE[0]##*/}"
    exit 0
fi
self=$(readlink -f "${BASH_SOURCE[0]}")
if (( EUID != 0 )); then exec sudo bash "$self" "$@"; fi
root=/opt/shtab-ai
unit=shtab-ai-install.service
selected=$root
cache=/var/cache/shtab-ai
backups=/var/backups/shtab-ai
if command -v python3 >/dev/null; then
    mapfile -t paths < <(python3 - "$root" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
selected = str(root)
cache = '/var/cache/shtab-ai'
backups = '/var/backups/shtab-ai'
try:
    selected = json.loads((root/'storage.json').read_text())['root']
except (OSError, ValueError, KeyError):
    pass
try:
    backups = (root/'backup-directory').read_text().strip() or backups
except OSError:
    pass
try:
    volumes = json.loads((root/'compose.cache.yaml').read_text())['volumes']
    cache = str(Path(volumes['asr_models']['driver_opts']['device']).parent)
except (OSError, ValueError, KeyError, TypeError):
    pass
for value in (selected, cache, backups):
    print(value)
PY
    )
    if (( ${#paths[@]} == 3 )); then
        selected=${paths[0]}; cache=${paths[1]}; backups=${paths[2]}
    fi
fi

docker_available() { command -v docker >/dev/null && docker info >/dev/null 2>&1; }
sizes() {
    local label path
    while (( $# >= 2 )); do
        label=$1; path=$2; shift 2
        printf '\n%s: %s\n' "$label" "$path"
        if [[ -d $path ]]; then du -sh -- "$path"; else echo 'Папка ещё не создана.'; fi
    done
}
cache_view() {
    date '+%F %T %Z'
    sizes 'Кэш моделей' "$cache" 'Whisper' "$cache/whisper" 'Qwen / Ollama' "$cache/ollama" 'Приложение и рабочие данные' "$selected" 'Бэкапы' "$backups"
    if docker_available; then
        local docker_root
        docker_root=$(docker info --format '{{.DockerRootDir}}')
        sizes 'Общее хранилище Docker (включает другие приложения)' "$docker_root"
        [[ ! -d /var/lib/containerd ]] || sizes 'Общее хранилище containerd' /var/lib/containerd
    fi
    printf '\nРазмер на диске — не счётчик сетевой загрузки. Кэш может расти после начала этапа моделей.\n'
}
overview() {
    date '+%F %T %Z'
    printf '\nСлужба установки:\n'
    systemctl show "$unit" -p ActiveState -p SubState -p Result
    printf '\nЭтап: '
    if [[ -f /var/lib/shtab-ai/status ]]; then cat /var/lib/shtab-ai/status; else echo 'Статус пока отсутствует.'; fi
    printf '\nПриложение: %s\nКэш моделей: %s\nБэкапы: %s\n' "$selected" "$cache" "$backups"
    printf '\nКонтейнеры Штаб.AI:\n'
    if docker_available; then
        docker ps -a --filter label=com.docker.compose.project=shtab-ai --format 'table {{.Names}}\t{{.Status}}'
    else echo 'Docker пока не установлен или недоступен.'; fi
    printf '\nСвободное место:\n'
    df -h -- / "$(dirname "$selected")"
    printf '\nОперативная память:\n'
    free -h
}
process_view() {
    date '+%F %T %Z'
    printf '\nCPU в ps — средняя нагрузка за время жизни процесса; RSS — память в КиБ.\n'
    ps -eo pid,ppid,comm,pcpu,pmem,rss,etimes --sort=-pcpu |
        awk 'NR == 1 || $3 ~ /^(python|ollama|postgres|docker|containerd|buildkit|curl|wget|apt|dpkg|pip|ffmpeg|cc1|gcc|g\+\+)/'
}
live() {
    if command -v watch >/dev/null; then
        watch -n 10 -- bash "$self" "$1"
    else
        echo 'watch недоступен; показываем один снимок.'
        bash "$self" "$1"
    fi
}
pause() { read -r -p 'Enter — вернуться в меню… ' _ || true; }
case ${1:-} in
    --once|--overview) overview; exit 0;;
    --cache) cache_view; exit 0;;
    --processes) process_view; exit 0;;
    '') ;;
    *) echo 'Неизвестный параметр. Используйте --help.'; exit 2;;
esac
[[ -t 0 ]] || { overview; exit 0; }
# Ctrl+C closes the foreground viewer and returns to the menu.
trap ':' INT
while true; do
    printf '\nШтаб.AI — наблюдение за установкой\n'
    printf '1 — Сводка: этап, служба, контейнеры, память и диски\n'
    printf '2 — Индикатор прогресса установщика\n'
    printf '3 — Журнал установки в реальном времени\n'
    printf '4 — Размеры кэша, приложения и бэкапов\n'
    printf '5 — Нагрузка контейнеров: CPU, память, сеть, диск\n'
    printf '6 — Все процессы Ubuntu (top)\n'
    printf '7 — Процессы установки и моделей\n'
    printf '8 — Последние сообщения базы PostgreSQL\n'
    printf '9 — Разовая статистика диска Docker\n'
    printf '0 — Выход\nCtrl+C в просмотре — вернуться в меню.\n'
    read -r -p 'Выбор: ' choice || break
    case $choice in
        1) live --overview ;;
        2) if [[ -f $root/scripts/install-progress.py ]]; then
               python3 "$root/scripts/install-progress.py"
           else echo 'Индикатор ещё не установлен.'; pause; fi ;;
        3) journalctl -fu "$unit" -o cat ;;
        4) live --cache ;;
        5) if docker_available; then
               mapfile -t containers < <(docker ps -q --filter label=com.docker.compose.project=shtab-ai)
               if (( ${#containers[@]} )); then docker stats "${containers[@]}";
               else echo 'Пока нет работающих контейнеров Штаб.AI.'; pause; fi
           else echo 'Docker недоступен.'; pause; fi ;;
        6) top -c ;;
        7) live --processes ;;
        8) if docker_available; then
               mapfile -t databases < <(docker ps -aq --filter label=com.docker.compose.project=shtab-ai --filter label=com.docker.compose.service=db)
               if (( ${#databases[@]} )); then
                   docker inspect --format '{{.Name}}: {{.State.Status}} {{if .State.Health}}/ {{.State.Health.Status}}{{end}}' "${databases[0]}"
                   docker logs --tail 80 "${databases[0]}"
               else echo 'Контейнер базы ещё не создан.'; fi
           else echo 'Docker недоступен.'; fi
           pause ;;
        9) if docker_available; then docker system df; else echo 'Docker недоступен.'; fi; pause ;;
        0) break ;;
        *) echo 'Выберите пункт от 0 до 9.' ;;
    esac
done
echo 'Наблюдение закрыто. Состояние установки не изменено.'
