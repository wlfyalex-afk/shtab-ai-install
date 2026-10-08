#!/bin/bash
# Scope Docker's forwarding exception to this installation and its LAN subnet.
set -euo pipefail
root=/opt/shtab-ai-021
action=${1:-apply}
host=$(cat "$root/lan-address")
subnet=$(cat "$root/lan-subnet")
chain=SHTAB021-NET
for port in 80 443; do
    rule=(-m conntrack --ctorigdst "$host" --ctorigdstport "$port" -m comment --comment ShtabAI-021 -j "$chain")
    if [[ $action == remove ]]; then
        while iptables -w -C DOCKER-USER "${rule[@]}" 2>/dev/null; do iptables -w -D DOCKER-USER "${rule[@]}"; done
    fi
done
if [[ $action == remove ]]; then
    if iptables -w -S "$chain" >/dev/null 2>&1; then iptables -w -F "$chain"; iptables -w -X "$chain"; fi
    exit 0
fi
iptables -w -S DOCKER-USER >/dev/null || { echo 'Docker DOCKER-USER chain unavailable; network setup stopped.'; exit 1; }
iptables -w -N "$chain" 2>/dev/null || true
iptables -w -F "$chain"
iptables -w -A "$chain" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -w -A "$chain" -s "$subnet" -j ACCEPT
iptables -w -A "$chain" -j DROP
for port in 80 443; do
    rule=(-m conntrack --ctorigdst "$host" --ctorigdstport "$port" -m comment --comment ShtabAI-021 -j "$chain")
    iptables -w -C DOCKER-USER "${rule[@]}" 2>/dev/null || iptables -w -I DOCKER-USER 1 "${rule[@]}"
done
