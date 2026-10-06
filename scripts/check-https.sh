#!/bin/bash
set -euo pipefail
cd /opt/shtab-ai-021
host=$(sed -n 's/^SHTAB_HTTPS_HOST=//p' .env)
bind=$(sed -n 's/^SHTAB_HTTPS_BIND_IP=//p' .env)
[[ -n $host ]] || { echo 'HTTPS не настроен: в .env нет SHTAB_HTTPS_HOST.'; exit 1; }
# Use the actual published interface; wildcard binds are reachable via loopback.
target=${bind:-127.0.0.1}
[[ $target != 0.0.0.0 ]] || target=127.0.0.1
ca=tls-data/caddy/pki/authorities/local/root.crt
echo "Проверка HTTPS: https://$host/login"
echo "Проверяем на этой VM: $target:443; сертификат и страницу входа."
dc() { docker compose -f compose.yaml "$@"; }
if ! running=$(dc ps --services --status running); then
    echo 'Не удалось получить статус сервисов Docker. Проверьте пункт 1.'; exit 1
fi
if ! printf '%s\n' "$running" | grep -qx proxy; then
    echo 'HTTPS недоступен: сервис proxy не запущен.'
    echo 'Во время бэкапа/восстановления это ожидаемо. Дождитесь завершения обслуживания.'
    echo 'Если обслуживание завершено: пункт 1 — статус, пункт 9 — запуск сервисов.'
    exit 1
fi
err=$(mktemp)
trap 'rm -f "$err"' EXIT
last_code=0
# Bounded startup allowance; no repeated raw curl errors.
for attempt in {1..5}; do
    printf 'Попытка %d/5: ' "$attempt"
    if [[ ! -s $ca ]]; then
        echo 'ожидаем выпуска корневого сертификата сервисом proxy.'
        last_code=60
    elif curl --silent --show-error --fail --connect-timeout 2 --max-time 5 \
        --noproxy '*' --cacert "$ca" --connect-to "$host:443:$target:443" \
        "https://$host/login" -o /dev/null 2>"$err"; then
        echo 'страница входа доступна, сертификат проверен.'
        install -m 0644 "$ca" shtab-ai-root.crt
        openssl x509 -in shtab-ai-root.crt -noout -fingerprint -sha256
        echo 'HTTPS исправен. Корневой сертификат: /opt/shtab-ai-021/shtab-ai-root.crt'
        echo 'Это локальная проверка; доступ с другого ПК и доверие его браузера проверяются отдельно.'
        exit 0
    else
        last_code=$?
        echo "пока недоступно (код $last_code)."
    fi
    if [[ $attempt -lt 5 ]]; then sleep 2; fi
done
case $last_code in
    7) echo "Нет TCP-соединения с $target:443. Проверьте proxy и привязку порта 443." ;;
    28) echo 'Истекло время ожидания соединения или ответа HTTPS.' ;;
    60|77) echo 'Не удалось проверить доверие к сертификату. Проверьте корневой сертификат и имя HTTPS.' ;;
    22) echo 'HTTPS ответил ошибкой HTTP. Проверьте web и страницу входа.' ;;
    *) echo "Проверка HTTPS завершилась ошибкой (код $last_code)." ;;
esac
[[ ! -s $err ]] || cat "$err"
echo 'Статус: пункт 1. Журнал proxy/web:'
echo 'sudo docker compose -f /opt/shtab-ai-021/compose.yaml logs --tail 50 proxy web'
exit 1
