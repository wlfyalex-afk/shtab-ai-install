#!/bin/bash
set -euo pipefail
cd /opt/shtab-ai-021
state=/var/lib/shtab-ai-021
mkdir -p "$state"
exec 9>"$state/install.lock"
flock -n 9 || { echo 'Installation already running'; exit 1; }
trap 'printf "FAILED line=%s\n" "$LINENO" > "$state/status"' ERR
stage() { printf '%s\n' "$1" | tee "$state/status"; }
dc() { docker compose -f compose.yaml "$@"; }

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
dc config --quiet
stage BUILDING_APP
dc build web
stage INITIALIZING_DATABASE
dc run --rm --no-deps init-volumes
dc up -d --wait --wait-timeout 240 db ollama
stage DOWNLOADING_QWEN
if ! dc exec -T ollama ollama show qwen3:4b >/dev/null 2>&1; then
    dc exec -T ollama ollama pull qwen3:4b
fi
stage DOWNLOADING_WHISPER
dc --profile setup run --rm asr-download
stage CHECKING_DATABASE_AND_MODELS
dc run --rm web python manage.py check
dc run --rm meeting-worker python meeting_worker.py check --load-model
dc run --rm llm-worker python llm_worker013.py check
dc run --rm brief-worker python brief_worker017.py check
dc run --rm web python check0183.py
dc run --rm web python check01845.py
dc exec -T ollama ollama list > "$state/ollama-models.txt"
dc images --format json > "$state/images.json"
stage STARTING_SERVICES
dc up -d --wait --wait-timeout 240 web meeting-worker llm-worker brief-worker proxy
bash scripts/check-https.sh
stage READY_FOR_ADMIN
echo 'Components ready. Create administrator: sudo /opt/shtab-ai-021/shtabctl bootstrap'
