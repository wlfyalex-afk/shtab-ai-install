#!/bin/bash
set -euo pipefail
cd /opt/shtab-ai-021
dc() { bash scripts/dc.sh "$@"; }
echo '=== VM memory ==='
free -h
echo '=== Containers ==='
dc ps -a
echo '=== Resource usage ==='
docker stats --no-stream $(dc ps -q) || true
echo '=== Recognition worker log ==='
dc logs --since 30m --tail 150 meeting-worker || true
echo '=== Recognition memory limit and OOM counters ==='
dc exec -T meeting-worker sh -c 'cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/memory.events' || true
echo '=== Recent recognition and conversion logs ==='
dc exec -T meeting-worker sh -c 'find /srv/shtab-ai/meeting-imports -type f \( -name asr.log -o -name conversion.log \) -mmin -60 -print -exec tail -n 50 {} \;' || true
echo '=== LLM and brief worker logs ==='
dc logs --since 30m --tail 60 llm-worker brief-worker ollama || true

echo '=== HTTPS proxy ==='
dc logs --since 30m --tail 60 proxy || true
