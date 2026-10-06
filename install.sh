#!/bin/bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run: sudo bash install.sh'; exit 1; }
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Requires Ubuntu 24.04 LTS'; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'Requires amd64'; exit 1; }
[[ $(nproc) -ge 4 ]] || { echo 'Requires at least 4 vCPU'; exit 1; }
mem=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
[[ $mem -ge 10000000 ]] || { echo 'Requires at least 10 GB RAM; VM target 12 GB'; exit 1; }
free=$(df -Pk /opt | awk 'NR==2 {print $4}')
[[ $free -ge 31457280 ]] || { echo 'Requires at least 30 GiB free in /opt'; exit 1; }
install_state=$(systemctl show shtab-ai-install.service -p ActiveState --value 2>/dev/null || true)
if [[ $install_state == active || $install_state == activating ]]; then
    echo 'Installation already running; sudo journalctl -fu shtab-ai-install'; exit 0
fi
src=$(cd "$(dirname "$0")" && pwd)
[[ -s "$src/app/static/bim-dv.jpg" ]] || { echo 'Missing logo: app/static/bim-dv.jpg'; exit 1; }
for font in DejaVuSans.ttf DejaVuSans-Bold.ttf; do
    [[ -s "$src/app/vendor0184/$font" ]] || { echo "Missing PDF font: $font"; exit 1; }
done
dst=/opt/shtab-ai-021
if [[ ! -f $dst/.env ]]; then
    if ss -H -ltn 'sport = :18092' | read -r _; then
        echo 'TCP 18092 is occupied'; exit 1
    fi
fi
if [[ ! -d $dst/tls-data ]]; then
    for port in 80 443; do
        if ss -H -ltn "sport = :$port" | read -r _; then
            echo "TCP $port is occupied; HTTPS needs ports 80 and 443"; exit 1
        fi
    done
fi
mkdir -p "$dst" /var/lib/shtab-ai-021
if [[ $src != "$dst" ]]; then
    cp -a "$src/app" "$src/database" "$src/scripts" "$src/compose.yaml" "$src/shtabctl" "$src/uninstall.sh" "$src/https" "$dst/"
fi
install -d -m 0700 "$dst/secrets"
python3 - "$dst" <<'PY'
from pathlib import Path
import secrets
import sys
root = Path(sys.argv[1])
paths = [root/'secrets'/n for n in ('db_password', 'flask_secret')]
exists = [p.exists() for p in paths]
if any(exists) and not all(exists):
    raise SystemExit('Incomplete secrets: stop and restore missing file; passwords are not regenerated')
if not any(exists):
    if (root/'installation-created').exists():
        raise SystemExit('Existing installation lost secrets; restore them before continuing')
    for p in paths:
        p.write_text(secrets.token_hex(32))
        p.chmod(0o444)
(root/'installation-created').touch()
PY
if [[ ! -f $dst/.env ]]; then
    vm_ip=$(hostname -I | awk '{print $1}')
    [[ -n $vm_ip ]] || { echo 'No VM IP'; exit 1; }
    cat > "$dst/.env" <<EOF
SHTAB_BIND_IP=0.0.0.0
SHTAB_HTTP_PORT=18092
SHTAB_TRUSTED_HOSTS=127.0.0.1,localhost,$vm_ip,$(hostname)
EOF
    chmod 600 "$dst/.env"
fi
python3 "$dst/scripts/configure-https.py" "$dst" "${1:-}"
chmod 755 "$dst/shtabctl" "$dst/scripts/provision.sh"
cat > /etc/systemd/system/shtab-ai-install.service <<EOF
[Unit]
Description=Shtab.AI 021 provisioning
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$dst
ExecStart=/bin/bash $dst/scripts/provision.sh
TimeoutStartSec=infinity
StandardOutput=journal
StandardError=journal
EOF
systemctl daemon-reload
if [[ $(systemctl show shtab-ai-install.service -p ActiveState --value) == failed ]]; then
    systemctl reset-failed shtab-ai-install.service
fi
systemctl start --no-block shtab-ai-install.service
echo 'Background installation started.'
echo 'Follow: sudo journalctl -fu shtab-ai-install'
echo 'Status: sudo /opt/shtab-ai-021/shtabctl status'
echo 'Progress: sudo /opt/shtab-ai-021/shtabctl progress'
