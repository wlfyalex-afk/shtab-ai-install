#!/bin/bash
set -euo pipefail
cd /opt/shtab-ai-021
state=/var/lib/shtab-ai-021
mkdir -p "$state"
exec 9>"$state/install.lock"
flock -n 9 || { echo 'Installation already running'; exit 1; }
trap 'printf "FAILED line=%s\n" "$LINENO" > "$state/status"' ERR
stage() { printf '%s\n' "$1" | tee "$state/status"; }
dc() { bash scripts/dc.sh "$@"; }

stage INSTALLING_DEPENDENCIES
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl openssl python3 unzip
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL --retry 5 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
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
bash scripts/prepare-gpu.sh "$(cat acceleration 2>/dev/null || echo cpu)"
if [[ $(cat access-mode 2>/dev/null || true) == lan ]]; then
    bash scripts/network-firewall.sh apply
    cat > /etc/systemd/system/shtab-ai-network.service <<EOF
[Unit]
Description=Shtab.AI LAN firewall
After=docker.service
Requires=docker.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash /opt/shtab-ai-021/scripts/network-firewall.sh apply
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now shtab-ai-network.service
fi
dc config --quiet
stage BUILDING_APP
dc build web
stage INITIALIZING_DATABASE
dc run --rm --no-deps init-volumes
dc up -d --wait --wait-timeout 240 db
external=$(sed -n 's/^SHTAB_OLLAMA_ENDPOINT=//p' .env)
if [[ -z $external ]]; then dc up -d --wait --wait-timeout 240 ollama; fi
stage DOWNLOADING_QWEN
if [[ -n $external ]]; then
    python3 scripts/ollama-api.py pull
    python3 scripts/ollama-api.py check "$(cat windows-acceleration 2>/dev/null || echo cpu)"
elif ! dc exec -T ollama ollama show qwen3:4b >/dev/null 2>&1; then
    dc exec -T ollama ollama pull qwen3:4b
fi
stage DOWNLOADING_WHISPER
dc --profile setup run --rm asr-download
stage CHECKING_DATABASE_AND_MODELS
if [[ -z $external ]]; then
    dc run --rm web python /installer/ollama-api.py check "$(cat acceleration)"
fi
dc run --rm web python manage.py check
dc run --rm meeting-worker python meeting_worker.py check --load-model
dc run --rm llm-worker python llm_worker013.py check
dc run --rm brief-worker python brief_worker017.py check
dc run --rm web python check0183.py
dc run --rm web python check01845.py
if [[ -n $external ]]; then
    python3 scripts/ollama-api.py list > "$state/ollama-models.txt"
else
    dc exec -T ollama ollama list > "$state/ollama-models.txt"
fi
dc images --format json > "$state/images.json"
stage STARTING_SERVICES
dc up -d --wait --wait-timeout 240 web meeting-worker llm-worker brief-worker proxy
bash scripts/check-https.sh
stage READY_FOR_ADMIN
echo 'Components ready. Create administrator: sudo /opt/shtab-ai-021/shtabctl bootstrap'
