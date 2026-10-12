#!/bin/bash
# Standalone Linux installer: Ubuntu 24.04 / Astra 1.8 x64; CPU / prepared GPU.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then exec sudo bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"; fi
mode=${1:-ask}
access=${2:-ask}
revision=${3:-main}
install_dir=${4:-}
case "$mode" in ask|cpu|nvidia|amd) ;; *) echo 'Acceleration: ask/cpu/nvidia/amd'; exit 2;; esac
case "$access" in ask|local|lan) ;; *) echo 'Access: ask/local/lan'; exit 2;; esac
[[ $revision == main || $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid revision'; exit 2; }
#!/bin/bash
# Shared host checks; no changes to Astra security policy or vendor drivers.
shtab_detect_linux() {
    local ID='' VERSION_ID='' PRETTY_NAME='' arch=${2:-$(uname -m)}
    source "${1:-/etc/os-release}"
    [[ $arch == x86_64 ]] || { echo 'Нужна архитектура Linux x86_64.' >&2; return 1; }
    case "$ID:$VERSION_ID" in
      ubuntu:24.04) SHTAB_LINUX_PROFILE=ubuntu ;;
      astra:1.8|astra:1.8.*|astra:1.8_x86-64) SHTAB_LINUX_PROFILE=astra ;;
      *) echo "Система ${PRETTY_NAME:-$ID $VERSION_ID} пока не поддерживается. Доступны Ubuntu 24.04 и Astra Linux 1.8 x64." >&2; return 1 ;;
    esac
    SHTAB_LINUX_NAME=${PRETTY_NAME:-$ID $VERSION_ID}
    SHTAB_LINUX_VERSION=$VERSION_ID
}

shtab_select_linux() {
    local requested=${1:-auto}
    case $requested in auto|ubuntu|astra) ;; *) echo 'Выбор системы: auto, ubuntu или astra.' >&2; return 2 ;; esac
    [[ $requested == auto || $requested == "$SHTAB_LINUX_PROFILE" ]] || {
        echo "Выбрана $requested, но обнаружена $SHTAB_LINUX_NAME. Установка остановлена до изменений." >&2
        return 1
    }
}

shtab_install_docker() {
    shtab_detect_linux
    if command -v docker >/dev/null && docker compose version >/dev/null 2>&1 && docker buildx version >/dev/null 2>&1; then
        echo 'Используем установленный Docker с Compose и Buildx.'
    elif [[ $SHTAB_LINUX_PROFILE == astra ]] || [[ $(dpkg-query -W -f='${db:Status-Status}' docker.io 2>/dev/null || true) == installed ]]; then
        echo "Docker: пакеты из репозитория $SHTAB_LINUX_NAME."
        local package candidate
        for package in docker.io docker-compose-v2 docker-buildx; do
            candidate=$(LC_ALL=C apt-cache policy "$package" | awk '/Candidate:/ {print $2; exit}')
            [[ -n $candidate && $candidate != '(none)' ]] || {
                echo "В подключённых репозиториях нет $package. Подключите репозиторий вашей версии ОС и повторите установку." >&2
                return 1
            }
        done
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 docker-buildx
    else
        # This branch is exclusively Ubuntu 24.04, checked above.
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL --retry 5 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    systemctl enable --now docker
    docker info >/dev/null
    docker compose version
    docker buildx version
}

distribution=${5:-ask}
shtab_detect_linux
if [[ $distribution == ask ]]; then
    echo "Обнаружена система: $SHTAB_LINUX_NAME"
    echo '1 — обнаруженная система; 2 — Ubuntu 24.04; 3 — Astra Linux 1.8.'
    read -r -p 'Система [1]: ' choice
    case ${choice:-1} in 1) distribution=auto;; 2) distribution=ubuntu;; 3) distribution=astra;; *) exit 2;; esac
fi
shtab_select_linux "$distribution"
echo "Вариант установки: $SHTAB_LINUX_NAME"
# Detect numbered layouts before creating any new resources; keep them intact.
for previous in /opt/shtab-ai-[0-9][0-9][0-9]; do
    if [[ -d $previous ]]; then
        echo 'Обнаружена прежняя установка Штаб.AI. Сохраните нужные данные и выполните команду удаления из инструкции; кэш и резервные копии сохранятся.'
        exit 1
    fi
done
resuming=false
if [[ -e /opt/shtab-ai ]]; then
    resuming=true
    [[ -f /opt/shtab-ai/installation-created && -f /opt/shtab-ai/.env ]] || { echo 'Неполная конфигурация прежней установки. Данные сохранены.'; exit 1; }
    echo 'Найдена установка Штаб.AI. Продолжаем без удаления.'
fi
umask 077
work=$(mktemp -d /var/tmp/shtab-bootstrap.XXXXXX)
trap 'rm -rf "$work"' EXIT
run_step() {
    python3 - "$@" <<'PY'
import subprocess, sys, time
title, *args = sys.argv[1:]
print('Штаб.AI: ' + title, flush=True)
started = time.monotonic()
process = subprocess.Popen(args)
try:
    while True:
        try:
            result = process.wait(timeout=30)
            break
        except subprocess.TimeoutExpired:
            print(f'Штаб.AI: {title} | прошло {int(time.monotonic()-started)} с; операция ещё не завершена.', flush=True)
except BaseException:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    raise
if result:
    raise SystemExit(result)
print(f'Штаб.AI: {title} — завершено за {int(time.monotonic()-started)} с.', flush=True)
PY
}
if ! $resuming; then
if [[ -z $install_dir ]]; then
    lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINTS
    read -r -p 'Новая папка установки [/opt/shtab-ai], например /mnt/data/shtab-ai: ' install_dir
    install_dir=${install_dir:-/opt/shtab-ai}
fi
if [[ $mode == ask ]]; then
    echo '1 — CPU Intel/AMD. 2 — NVIDIA (Whisper + Ollama). 3 — AMD (Ollama; Whisper на CPU).'
    read -r -p 'Режим [1]: ' choice
    case ${choice:-1} in 1) mode=cpu;; 2) mode=nvidia;; 3) mode=amd;; *) exit 2;; esac
fi
if [[ $mode == nvidia ]]; then
    export PATH="/usr/lib/wsl/lib:$PATH"
    command -v nvidia-smi >/dev/null && nvidia-smi -L || { echo 'Установите драйвер NVIDIA, перезагрузитесь и повторите установку; либо выберите CPU.'; exit 1; }
elif [[ $mode == amd ]]; then
    [[ -c /dev/kfd && -d /dev/dri ]] || { echo 'Для AMD нужны поддерживаемая карта и драйвер ROCm. Выберите CPU либо подготовьте драйвер.'; exit 1; }
fi
if [[ $access == ask ]]; then
    read -r -p 'Доступ: 1 — только этот ПК, 2 — локальная сеть [2]: ' choice
    case ${choice:-2} in 1) access=local;; 2) access=lan;; *) exit 2;; esac
fi
host=localhost
subnet=''
vpn_subnets=${SHTAB_VPN_SUBNETS:-}
if [[ $access == lan ]]; then
    echo 'Сетевые интерфейсы:'
    ip -br -4 address show scope global
    read -r -p 'IPv4 адрес этого ПК для доступа по сети: ' host
    ip -j -4 address > "$work/addresses.json"
    subnet=$(python3 - "$host" "$work/addresses.json" <<'PY'
import ipaddress,json,sys
address=ipaddress.IPv4Address(sys.argv[1])
for interface in json.load(open(sys.argv[2])):
    for item in interface.get('addr_info',[]):
        if item.get('local')==str(address):
            print(ipaddress.IPv4Network(f'{address}/{item["prefixlen"]}',strict=False));raise SystemExit
raise SystemExit('Адрес не принадлежит этому компьютеру')
PY
    )
    if [[ -z $vpn_subnets ]]; then
        echo 'Для доступа через WireGuard можно разрешить его IPv4-подсеть.'
        echo 'Пример: 10.20.30.0/24; несколько подсетей — через запятую.'
        read -r -p 'Подсети VPN (Enter — настроить позже): ' vpn_subnets
    fi
    vpn_subnets=$(python3 - "$vpn_subnets" <<'PYVPN'
import ipaddress,re,sys
values=[]
for item in re.split(r'[,\s]+',sys.argv[1].strip()):
    if not item: continue
    if '/' not in item: raise SystemExit('Укажите IPv4-подсеть с маской.')
    net=ipaddress.ip_network(item,strict=False)
    if net.version!=4 or net.prefixlen==0 or net.network_address.is_multicast or net.network_address.is_unspecified:
        raise SystemExit('Нужна конкретная IPv4-подсеть; 0.0.0.0/0 не разрешается.')
    if str(net) not in values: values.append(str(net))
print(','.join(values))
PYVPN
    )
fi
echo "Приложение: $install_dir"
if [[ $install_dir == /opt/shtab-ai ]]; then
    echo "Кэш моделей: ${SHTAB_MODEL_CACHE:-/var/cache/shtab-ai}"
    echo 'Резервные копии: /var/backups/shtab-ai'
else
    echo "Кэш моделей: ${SHTAB_MODEL_CACHE:-${install_dir}-cache}"
    echo "Резервные копии: ${install_dir}-backups"
fi
echo 'Образы и кэш сборки Docker: системное хранилище Docker.'
run_step 'Обновление списка системных пакетов' apt-get update
run_step 'Установка базовых компонентов' env DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl unzip python3 openssl
if [[ $revision == main ]]; then
    run_step 'Определение актуальной версии приложения' curl --fail --location --retry 3 https://api.github.com/repos/wlfyalex-afk/shtab-ai-install/commits/main -o "$work/head.json"
    revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$work/head.json")
fi
[[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Cannot resolve revision'; exit 1; }
echo "Загрузка Штаб.AI: $revision"
run_step 'Загрузка архива приложения' curl --fail --location --retry 3 --connect-timeout 20 --proto '=https' --proto-redir '=https' --progress-bar \
    "https://github.com/wlfyalex-afk/shtab-ai-install/archive/$revision.zip" -o "$work/source.zip"
python3 - "$work/source.zip" <<'PY'
from pathlib import PurePosixPath
import sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    for entry in z.infolist():
        p=PurePosixPath(entry.filename)
        if p.is_absolute() or '..' in p.parts or '\\' in entry.filename or ':' in entry.filename:
            raise SystemExit('Unsafe archive path')
PY
run_step 'Распаковка архива приложения' unzip -q "$work/source.zip" -d "$work/source"
mapfile -t roots < <(find "$work/source" -mindepth 1 -maxdepth 1 -type d)
[[ ${#roots[@]} == 1 ]] || { echo 'Unexpected archive layout'; exit 1; }
cd "${roots[0]}"
run_step 'Проверка целостности файлов приложения' sha256sum --quiet -c SHA256SUMS
python3 scripts/configure-storage.py prepare "$install_dir"
run_step 'Настройка приложения и проверка кэша моделей' env SHTAB_ACCESS="$access" SHTAB_LAN_ADDRESS="$host" SHTAB_LAN_SUBNET="$subnet" SHTAB_VPN_SUBNETS="$vpn_subnets" bash install.sh "$host" "$mode"
else
    # Use the current bootstrap's resume helper even for an older installation.
    (
#!/bin/bash
# Continue the installed version with its original secrets, volumes and cache.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустите с sudo.'; exit 1; }
cd /opt/shtab-ai
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
status=$(cat /var/lib/shtab-ai/status 2>/dev/null || true)
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
module.reconcile(Path('/opt/shtab-ai'), sys.argv[1], sys.argv[2], 'READY_FOR_ADMIN')
PY
fi
echo 'Продолжаем установку. Полученные файлы моделей и данные сохраняются.'
systemctl reset-failed "$unit"
# Avoid reading a stale FAILED status before the background process starts.
printf 'RESUMING\n' > /var/lib/shtab-ai/status
systemctl start --no-block "$unit"

    )
    host=$(sed -n 's/^SHTAB_HTTPS_HOST=//p' /opt/shtab-ai/.env | tail -1)
    mode=$(cat /opt/shtab-ai/acceleration)
    access=$(cat /opt/shtab-ai/access-mode 2>/dev/null || echo local)
fi
# Embed the current finalizer so older installations can finish without reinstalling.
finish_native() {
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

}
finish_native --wait
