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
