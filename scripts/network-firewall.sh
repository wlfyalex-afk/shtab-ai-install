#!/bin/bash
# Scope Docker forwarding to the explicitly permitted LAN and VPN subnets.
set -euo pipefail
root=/opt/shtab-ai
action=${1:-apply}
host=$(cat "$root/lan-address")
[[ $action == apply || $action == remove ]] || { echo 'Action: apply/remove'; exit 2; }
chain=SHTAB-NET
for port in 80 443; do
    rule=(-m conntrack --ctorigdst "$host" --ctorigdstport "$port" -m comment --comment ShtabAI -j "$chain")
    if [[ $action == remove ]]; then
        while iptables -w -C DOCKER-USER "${rule[@]}" 2>/dev/null; do iptables -w -D DOCKER-USER "${rule[@]}"; done
    fi
done
if [[ $action == remove ]]; then
    if iptables -w -S "$chain" >/dev/null 2>&1; then iptables -w -F "$chain"; iptables -w -X "$chain"; fi
    exit 0
fi
subnets=$(python3 "$root/scripts/network-config.py" list "$root")
iptables -w -S DOCKER-USER >/dev/null || { echo 'Docker DOCKER-USER chain unavailable; network setup stopped.'; exit 1; }
iptables -w -N "$chain" 2>/dev/null || true
iptables -w -F "$chain"
iptables -w -A "$chain" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
while IFS= read -r subnet; do
    iptables -w -A "$chain" -s "$subnet" -j ACCEPT
done <<< "$subnets"
iptables -w -A "$chain" -j DROP
for port in 80 443; do
    rule=(-m conntrack --ctorigdst "$host" --ctorigdstport "$port" -m comment --comment ShtabAI -j "$chain")
    iptables -w -C DOCKER-USER "${rule[@]}" 2>/dev/null || iptables -w -I DOCKER-USER 1 "${rule[@]}"
done
