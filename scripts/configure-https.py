#!/usr/bin/env python3
"""Select and validate one local HTTPS address; preserve an existing CA."""
import ipaddress
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


def validate_host(value):
    if not value or len(value) > 253:
        raise ValueError('Specify an IPv4 address or DNS name, without https:// or port')
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        labels = value.split('.')
        if all(label.isdigit() for label in labels) or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
            raise ValueError('Invalid HTTPS hostname')
        return value.lower()
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise ValueError('Use a reachable IPv4 address or DNS name')
    return str(address)


def configure(root, requested=''):
    env_path = root / '.env'
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    values = dict(line.split('=', 1) for line in lines if line and not line.startswith('#') and '=' in line)
    host = requested or values.get('SHTAB_HTTPS_HOST', '')
    if not host:
        addresses = subprocess.check_output(['hostname', '-I'], text=True).split()
        host = next((a for a in addresses if ':' not in a and not ipaddress.ip_address(a).is_loopback), '')
    host = validate_host(host)
    trusted = values.get('SHTAB_TRUSTED_HOSTS', '127.0.0.1,localhost').split(',')
    values['SHTAB_TRUSTED_HOSTS'] = ','.join(dict.fromkeys(trusted + [host]))
    values['SHTAB_HTTPS_HOST'] = host
    values['SHTAB_HTTPS_SITES'] = 'https://' + host + (', https://localhost' if host != 'localhost' else '')
    values.setdefault('SHTAB_HTTPS_BIND_IP', '0.0.0.0')
    changed = {'SHTAB_TRUSTED_HOSTS', 'SHTAB_HTTPS_HOST', 'SHTAB_HTTPS_BIND_IP', 'SHTAB_HTTPS_SITES'}
    output = [line for line in lines if line.split('=', 1)[0] not in changed]
    output += [f'{key}={values[key]}' for key in sorted(changed)]
    temp = root / '.env.https.tmp'
    temp.write_text('\n'.join(output) + '\n')
    temp.chmod(0o600)
    os.replace(temp, env_path)
    for name in ('tls-data', 'tls-config'):
        (root / name).mkdir(exist_ok=True)
        (root / name).chmod(0o700)
    print(f'HTTPS address: https://{host}')
    return host


if __name__ == '__main__':
    try:
        configure(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else '')
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
