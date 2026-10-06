#!/bin/bash
# Upgrade an existing 021 VM, without running model/database provisioning.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
src=$(cd "$(dirname "$0")/.." && pwd)
dst=/opt/shtab-ai-021
[[ $src != "$dst" ]] || { echo 'Run this script from the newly extracted rc2 package'; exit 1; }
[[ -f $dst/compose.yaml && -f $dst/.env ]] || { echo 'Existing installation not found'; exit 1; }
mkdir -p /var/lib/shtab-ai-021
exec 9>/var/lib/shtab-ai-021/install.lock
flock -n 9 || { echo 'Installer running; wait'; exit 1; }
for port in 80 443; do
    if ss -H -ltn "sport = :$port" | read -r _; then
        echo "TCP $port occupied; stop and inspect its owner before upgrading"; exit 1
    fi
done
# Validate input before touching the installation.
python3 - "$src" "${1:-}" <<'PY'
import runpy,sys
m=runpy.run_path(sys.argv[1]+'/scripts/configure-https.py')
if sys.argv[2]: m['validate_host'](sys.argv[2])
PY
backup="/var/backups/shtab-ai-021/https-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$backup"
tar -czf "$backup/installation.tar.gz" -C /opt shtab-ai-021
chmod 600 "$backup/installation.tar.gz"
python3 - "$src" "$dst" <<'PY'
from pathlib import Path
import re,sys
src,dst=map(Path,sys.argv[1:])
old=(dst/'compose.yaml').read_text()
image=re.search(r'^  image: (shtab-ai-021-app:[^\s]+)$',old,re.M)
if not image: raise SystemExit('Unknown app image; upgrade aborted')
new=(src/'compose.yaml').read_text()
new=re.sub(r'^  image: shtab-ai-021-app:[^\s]+$', '  image: '+image[1],new,count=1,flags=re.M)
(dst/'compose.yaml').write_text(new)
p=dst/'app/app.py'
s=p.read_text()
old_cookie='SESSION_COOKIE_SECURE=False'
new_cookie="SESSION_COOKIE_SECURE=os.environ.get('SHTAB_SESSION_COOKIE_SECURE','0') == '1'"
if old_cookie in s: s=s.replace(old_cookie,new_cookie,1)
elif new_cookie not in s: raise SystemExit('Unknown cookie setting; restore compose.yaml from backup')
if 'from werkzeug.middleware.proxy_fix import ProxyFix' not in s:
    s=s.replace('from werkzeug.security import', 'from werkzeug.middleware.proxy_fix import ProxyFix\nfrom werkzeug.security import',1)
if 'app.wsgi_app = ProxyFix' not in s:
    marker='    app = Flask(__name__)'
    if marker not in s: raise SystemExit('Unknown Flask setup; restore from backup')
    s=s.replace(marker,marker+"\n    if os.environ.get('SHTAB_SESSION_COOKIE_SECURE','0') == '1':\n        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)",1)
p.write_text(s)
PY
cp -a "$src/https" "$dst/"
cp -a "$src/scripts/." "$dst/scripts/"
cp "$src/shtabctl" "$dst/shtabctl"
cp "$src/uninstall.sh" "$dst/uninstall.sh"
cp "$src/app/vendor0184/DejaVuSans.ttf" "$src/app/vendor0184/DejaVuSans-Bold.ttf" "$dst/app/vendor0184/"
python3 "$dst/scripts/configure-https.py" "$dst" "${1:-}"
cd "$dst"
docker compose config --quiet
docker compose build web
docker compose up -d --no-deps --wait --wait-timeout 240 web
docker compose up -d proxy
bash scripts/check-https.sh
echo "Previous installation saved (root only): $backup/installation.tar.gz"
echo 'Workers and database were not restarted. Configure Windows trust, then log in over HTTPS.'
