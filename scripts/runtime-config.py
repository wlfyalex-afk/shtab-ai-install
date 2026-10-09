#!/usr/bin/env python3
"""Reconcile runtime addresses only after provisioning; keep the existing CA."""
import ipaddress
import os
from pathlib import Path
import sys


def reconcile(root, endpoint, host, status):
    if status != 'READY_FOR_ADMIN' or not (root / '.env').is_file():
        return 'DEFERRED'
    # Arguments come from the selected Windows adapter and WSL NAT route.
    address = endpoint.removeprefix('http://').removesuffix(':11435')
    ipaddress.IPv4Address(address)
    if endpoint != f'http://{address}:11435':
        raise ValueError('Invalid Ollama endpoint')
    host = host or 'localhost'
    if host != 'localhost':
        ip = ipaddress.IPv4Address(host)
        if ip.is_unspecified or ip.is_multicast or ip.is_loopback:
            raise ValueError('Invalid LAN address')
    env = root / '.env'
    lines = env.read_text().splitlines()
    values = dict(line.split('=', 1) for line in lines if '=' in line and not line.startswith('#'))
    old = values.get('SHTAB_WINDOWS_LAN_HOST', values.get('SHTAB_HTTPS_HOST', ''))
    trusted = [x for x in values.get('SHTAB_TRUSTED_HOSTS', '').split(',') if x and x != old]
    changes = {
        'SHTAB_OLLAMA_ENDPOINT': endpoint,
        'SHTAB_WINDOWS_LAN_HOST': host,
        'SHTAB_HTTPS_HOST': host,
        'SHTAB_HTTPS_SITES': 'https://' + host + (', https://localhost' if host != 'localhost' else ''),
        'SHTAB_TRUSTED_HOSTS': ','.join(dict.fromkeys(trusted + ['127.0.0.1', 'localhost', host])),
    }
    # Leave a retry marker if compose fails after updating the environment.
    pending = root / '.runtime-config-pending'
    if all(values.get(k) == v for k, v in changes.items()):
        return 'CHANGED' if pending.exists() else 'SAME'
    pending.touch(mode=0o600)
    output = [line for line in lines if line.split('=', 1)[0] not in changes]
    output += [f'{k}={v}' for k, v in changes.items()]
    temp = root / '.env.runtime.tmp'
    temp.write_text('\n'.join(output) + '\n')
    temp.chmod(0o600)
    os.replace(temp, env)
    return 'CHANGED'


if __name__ == '__main__':
    root = Path('/opt/shtab-ai-021')
    state = Path('/var/lib/shtab-ai-021/status')
    print(reconcile(root, sys.argv[1], sys.argv[2], state.read_text().strip() if state.exists() else ''))
