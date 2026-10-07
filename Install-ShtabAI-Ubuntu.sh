#!/bin/bash
# Download a pinned Shtab.AI revision and start installation on a clean Ubuntu VM.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"
fi
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || {
    echo 'Поддерживается Ubuntu 24.04 LTS amd64.'; exit 1;
}
revision=70c33aa9a211da84f2d88490b8baf1ead58bad29
[[ ! -e /opt/shtab-ai-021/installation-created ]] || {
    echo 'Установка уже существует. Этот скрипт предназначен для чистой VM.'; exit 1;
}
umask 077
work=$(mktemp -d /var/tmp/shtab-bootstrap.XXXXXX)
trap 'rm -rf "$work"' EXIT
echo 'Подготовка средств загрузки; Ubuntu целиком не обновляется.'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl unzip python3 openssl
echo "Загрузка открытой тестовой версии Штаб.AI: $revision"
curl --fail --location --retry 3 --connect-timeout 20 \
    --proto '=https' --proto-redir '=https' --progress-bar \
    "https://github.com/wlfyalex-afk/shtab-ai-install/archive/$revision.zip" -o "$work/source.zip"
unzip -q "$work/source.zip" -d "$work/source"
mapfile -t roots < <(find "$work/source" -mindepth 1 -maxdepth 1 -type d)
[[ ${#roots[@]} == 1 ]] || { echo 'Неожиданная структура архива.'; exit 1; }
cd "${roots[0]}"
sha256sum --quiet -c SHA256SUMS
bash install.sh
echo 'Установка запущена. Ожидание готовности (до двух часов).'
deadline=$((SECONDS + 7200))
while true; do
    status=$(cat /var/lib/shtab-ai-021/status 2>/dev/null || echo STARTING)
    echo "Штаб.AI: $status"
    [[ $status == READY_FOR_ADMIN ]] && break
    [[ $status != FAILED* ]] || { echo 'Ошибка установки. Последние записи журнала:'; journalctl -u shtab-ai-install -n 100 --no-pager; exit 1; }
    (( SECONDS < deadline )) || { echo 'Время ожидания истекло; установка продолжает работать. Последние записи журнала:'; journalctl -u shtab-ai-install -n 100 --no-pager; exit 1; }
    sleep 10
done
/opt/shtab-ai-021/shtabctl certificate
install -m 0644 /opt/shtab-ai-021/shtab-ai-root.crt /usr/local/share/ca-certificates/shtab-ai.crt
update-ca-certificates
/opt/shtab-ai-021/shtabctl bootstrap
echo 'Установка завершена. Адрес HTTPS:'
grep '^SHTAB_HTTPS_HOST=' /opt/shtab-ai-021/.env | cut -d= -f2-
echo 'Доверие сертификату настроено на этой Ubuntu. Браузеру на другом компьютере потребуется сертификат этой установки.'

