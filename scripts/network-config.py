#!/usr/bin/env python3
"""Validate and persist LAN/VPN source networks; never guess a VPN range."""
import ipaddress
import os
from pathlib import Path
import re
import sys


def networks(value):
    result = []
    for item in re.split(r'[,\s]+', value.strip()):
        if not item:
            continue
        if '/' not in item:
            raise ValueError('Укажите IPv4-подсеть с маской, например 10.20.30.0/24.')
        network = ipaddress.ip_network(item, strict=False)
        if network.version != 4 or network.prefixlen == 0 or network.network_address.is_multicast or network.network_address.is_unspecified:
            raise ValueError('Нужна конкретная IPv4-подсеть; 0.0.0.0/0 не разрешается.')
        normalized = str(network)
        if normalized not in result:
            result.append(normalized)
    return result


def allowed(root):
    lan = networks((root / 'lan-subnet').read_text())
    if len(lan) != 1:
        raise ValueError('Не удалось определить офисную подсеть.')
    host = ipaddress.IPv4Address((root / 'lan-address').read_text().strip())
    if host not in ipaddress.ip_network(lan[0]):
        raise ValueError('Адрес сервера не входит в офисную подсеть.')
    path = root / 'allowed-subnets'
    extra = networks(path.read_text()) if path.exists() else []
    return list(dict.fromkeys(lan + extra))


def configure(root, extra='', replace=False):
    # Validate the entire new configuration before changing anything.
    requested = networks(extra)
    current = allowed(root)
    lan = networks((root / 'lan-subnet').read_text())
    values = list(dict.fromkeys(lan + (requested if replace else current + requested)))
    temporary = root / 'allowed-subnets.tmp'
    temporary.write_text('\n'.join(values) + '\n')
    temporary.chmod(0o600)
    os.replace(temporary, root / 'allowed-subnets')
    return values


def main():
    action = sys.argv[1]
    if action == 'validate':
        print(','.join(networks(' '.join(sys.argv[2:]))))
        return
    root = Path(sys.argv[2])
    if action == 'list':
        values = allowed(root)
    elif action in ('init', 'set-vpn'):
        values = configure(root, ' '.join(sys.argv[3:]), replace=action == 'set-vpn')
    else:
        raise ValueError('Команды: validate, list, init, set-vpn.')
    print('\n'.join(values))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, IndexError) as exc:
        raise SystemExit(str(exc))
