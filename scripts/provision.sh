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
if ! bash scripts/prepare-gpu.sh "$(cat acceleration 2>/dev/null || echo cpu)"; then
    echo 'Подготовка GPU не пройдена. Автоматически настраиваем процессор.'
    python3 scripts/configure-acceleration.py /opt/shtab-ai-021 cpu
fi
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
BUILDKIT_PROGRESS=plain dc build web
stage INITIALIZING_DATABASE
dc run --rm --no-deps init-volumes
dc up -d --wait --wait-timeout 240 db
external=$(sed -n 's/^SHTAB_OLLAMA_ENDPOINT=//p' .env)
if [[ -z $external ]]; then dc up -d --wait --wait-timeout 240 ollama; fi
mkdir -p download-progress
chown 10001:10001 download-progress
chmod 755 download-progress
ln -sfn /opt/shtab-ai-021/download-progress/qwen-progress.json "$state/qwen-progress.json"
ln -sfn /opt/shtab-ai-021/download-progress/whisper-progress.json "$state/whisper-progress.json"
rm -f download-progress/qwen-progress.json download-progress/whisper-progress.json
stage DOWNLOADING_QWEN
if [[ -n $external ]]; then
    SHTAB_PROGRESS_FILE="$PWD/download-progress/qwen-progress.json" python3 scripts/ollama-api.py pull
    python3 scripts/ollama-api.py check "$(cat windows-acceleration 2>/dev/null || echo cpu)"
else
    dc --profile setup run --rm qwen-download
fi
if [[ -z $external ]]; then
    # Run the host checker against the private Docker-published API through a container.
    set +e
    dc run --rm -v "$PWD/download-progress:/gpu-result" -e SHTAB_GPU_RESULT_ROOT=/gpu-result web python /installer/ollama-api.py check "$(cat acceleration)"
    result=$?
    set -e
    if [[ $result == 20 ]]; then
        echo 'GPU для Qwen не подтверждена. Переключаем Ollama на CPU.'
        python3 scripts/configure-acceleration.py /opt/shtab-ai-021 cpu
        dc build web
        dc up -d --wait --wait-timeout 240 ollama
        dc run --rm -v "$PWD/download-progress:/gpu-result" -e SHTAB_GPU_RESULT_ROOT=/gpu-result web python /installer/ollama-api.py check cpu
    elif [[ $result != 0 ]]; then exit "$result"; fi
fi
stage DOWNLOADING_WHISPER
if ! dc --profile setup run --rm asr-download; then
    if [[ $(cat acceleration) != nvidia ]]; then exit 1; fi
    echo 'Запуск контейнера Whisper на GPU не прошёл. Проверяем CPU.'
    python3 scripts/apply-asr-cpu.py /opt/shtab-ai-021
    dc --profile setup run --rm asr-download
fi
if python3 -c 'import json; from pathlib import Path; p=Path("download-progress/asr-compute.json"); raise SystemExit(0 if p.exists() and json.loads(p.read_text())["device"] == "cpu" else 1)'; then
    python3 scripts/apply-asr-cpu.py /opt/shtab-ai-021
fi
stage CHECKING_DATABASE_AND_MODELS
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

