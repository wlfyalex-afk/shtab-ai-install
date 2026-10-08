#!/bin/bash
# One entry point for Ubuntu 24.04 LTS x64, CPU / NVIDIA / AMD.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then exec sudo bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"; fi
mode=${1:-ask}
access=${2:-ask}
revision=${3:-main}
install_dir=${4:-}
case "$mode" in ask|cpu|nvidia|amd) ;; *) echo 'Acceleration: ask/cpu/nvidia/amd'; exit 2;; esac
case "$access" in ask|local|lan) ;; *) echo 'Access: ask/local/lan'; exit 2;; esac
[[ $revision == main || $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid revision'; exit 2; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || { echo 'Поддерживается Ubuntu 24.04 LTS x64.'; exit 1; }
[[ ! -e /opt/shtab-ai-021 ]] || { echo 'Каталог прежней установки существует. Сначала запустите скрипт удаления.'; exit 1; }
umask 077
work=$(mktemp -d /var/tmp/shtab-bootstrap.XXXXXX)
trap 'rm -rf "$work"' EXIT
if [[ -z $install_dir ]]; then
    lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINTS
    read -r -p 'Новая папка установки [/opt/shtab-ai-021], например /mnt/data/shtab-ai-021: ' install_dir
    install_dir=${install_dir:-/opt/shtab-ai-021}
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
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl unzip python3 openssl
if [[ $revision == main ]]; then
    curl --fail --location --retry 3 https://api.github.com/repos/wlfyalex-afk/shtab-ai-install/commits/main -o "$work/head.json"
    revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$work/head.json")
fi
[[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Cannot resolve revision'; exit 1; }
echo "Загрузка Штаб.AI: $revision"
curl --fail --location --retry 3 --connect-timeout 20 --proto '=https' --proto-redir '=https' --progress-bar \
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
unzip -q "$work/source.zip" -d "$work/source"
mapfile -t roots < <(find "$work/source" -mindepth 1 -maxdepth 1 -type d)
[[ ${#roots[@]} == 1 ]] || { echo 'Unexpected archive layout'; exit 1; }
cd "${roots[0]}"
sha256sum --quiet -c SHA256SUMS
python3 scripts/configure-storage.py prepare "$install_dir"
SHTAB_ACCESS="$access" SHTAB_LAN_ADDRESS="$host" SHTAB_LAN_SUBNET="$subnet" bash install.sh "$host" "$mode"
deadline=$((SECONDS+10800))
while true; do
    status=$(cat /var/lib/shtab-ai-021/status 2>/dev/null || echo STARTING)
    /opt/shtab-ai-021/shtabctl progress --once
    [[ $status != FAILED* ]] || { journalctl -u shtab-ai-install -n 80 --no-pager; exit 1; }
    [[ $status != READY_FOR_ADMIN ]] || break
    (( SECONDS < deadline )) || { echo 'Ожидание истекло; проверьте журнал установки.'; exit 1; }
    sleep 10
done
/opt/shtab-ai-021/shtabctl certificate
certificate=/usr/local/share/ca-certificates/shtab-ai-021.crt
[[ ! -e $certificate ]] || { echo 'Сертификат с таким именем уже существует; остановлено без перезаписи.'; exit 1; }
install -m 0644 /opt/shtab-ai-021/shtab-ai-root.crt "$certificate"
update-ca-certificates
/opt/shtab-ai-021/shtabctl bootstrap
python3 /opt/shtab-ai-021/scripts/create-desktop-shortcut.py /opt/shtab-ai-021 "${SUDO_USER:-}"
url="https://$host/login"
curl --fail --silent --show-error --noproxy '*' --cacert "$certificate" "$url" -o /dev/null
echo "Готово: $url. Режим: $mode."
if [[ $access == lan ]]; then
    echo 'На других ПК добавьте в доверенные публичный сертификат /opt/shtab-ai-021/shtab-ai-root.crt.'
    echo 'Проверка входа и загрузки с другого ПК необходима: локальный тест не проверяет чужие браузеры и файерволлы.'
fi
