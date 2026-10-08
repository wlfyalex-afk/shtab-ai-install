#!/bin/bash
# Driver installation/reboot stays outside a background provisioning job.
set -euo pipefail
mode=${1:-cpu}
case "$mode" in
cpu) exit 0 ;;
nvidia)
    export PATH="/usr/lib/wsl/lib:$PATH"
    command -v nvidia-smi >/dev/null && nvidia-smi -L || {
        echo 'NVIDIA недоступна. Установите рекомендованный драйвер Ubuntu и перезагрузитесь, затем повторите установку; либо выберите CPU.'
        exit 1
    }
    if ! docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"'; then
        # Restarting Docker must not interrupt unrelated applications.
        [[ -z $(docker ps -q) ]] || {
            echo 'Docker уже обслуживает контейнеры. Настройте NVIDIA Container Toolkit отдельно, либо выберите CPU.'; exit 1;
        }
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y gnupg
        work=$(mktemp -d)
        trap 'rm -rf "$work"' EXIT
        curl -fsSL --retry 3 https://nvidia.github.io/libnvidia-container/gpgkey -o "$work/key"
        gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg "$work/key"
        curl -fsSL --retry 3 https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list -o "$work/repo"
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' "$work/repo" > /etc/apt/sources.list.d/nvidia-container-toolkit.list
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit
        nvidia-ctk runtime configure --runtime=docker
        systemctl restart docker
    fi
    docker run --rm --gpus all ubuntu:24.04 nvidia-smi
    ;;
amd)
    [[ -c /dev/kfd && -d /dev/dri ]] || {
        echo 'AMD ROCm недоступна: нужны поддерживаемая карта, драйвер и устройства /dev/kfd и /dev/dri. Выберите CPU или подготовьте драйвер и повторите установку.'; exit 1;
    }
    ;;
*) echo 'Unknown acceleration mode'; exit 2 ;;
esac
