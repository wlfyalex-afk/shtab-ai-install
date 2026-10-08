#!/usr/bin/env python3
"""Place this installation and its named volume payloads on a selected Linux disk."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

CANONICAL = Path('/opt/shtab-ai-021')
UNITS = Path('/etc/systemd/system')
VOLUMES = ('db_data', 'app_data', 'asr_models', 'ollama_models')


def validate_path(value):
    path = Path(value)
    if not re.fullmatch(r'/[\w./-]+', value) or '..' in path.parts or len(path.parts) < 3:
        raise ValueError('Use an absolute new application folder without spaces, for example /mnt/data/shtab-ai-021')
    if path == CANONICAL or CANONICAL not in path.parents:
        return path
    raise ValueError('The custom folder cannot be inside /opt/shtab-ai-021')


def filesystem(path):
    record = json.loads(subprocess.check_output(['findmnt', '-J', '-T', str(path), '-o', 'TARGET,FSTYPE,UUID'], text=True))['filesystems'][0]
    return record


def volume_configuration(root):
    return {'volumes': {name: {'driver': 'local', 'driver_opts': {
        'type': 'none', 'o': 'bind', 'device': str(root/'storage'/name)}} for name in VOLUMES}}


def prepare(value):
    selected = validate_path(value)
    for part in (selected, *selected.parents):
        if part.is_symlink(): raise ValueError('Installation path contains a symbolic link')
    if selected.exists() or CANONICAL.exists(): raise ValueError('Installation folder already exists; uninstall first')
    parent = selected.parent
    if not parent.is_dir(): raise ValueError('Mount the partition and create the parent folder first')
    fs = filesystem(parent)
    if fs['fstype'] not in ('ext4', 'xfs', 'btrfs') or not fs.get('uuid'):
        raise ValueError('Choose a persistent local ext4/xfs/btrfs filesystem with a UUID')
    if fs['target'] != '/':
        mounts = json.loads(subprocess.check_output(['findmnt', '--fstab', '--evaluate', '--list', '-J', '-o', 'TARGET,UUID'], text=True))['filesystems']
        if not any(item.get('target') == fs['target'] and item.get('uuid') == fs['uuid'] for item in mounts):
            raise ValueError('The selected partition must be mounted persistently through /etc/fstab before installation')
    if shutil.disk_usage(parent).free < 30*1024**3: raise ValueError('At least 30 GiB free on the selected filesystem is required')
    backups = Path('/var/backups/shtab-ai-021') if selected == CANONICAL else selected.with_name(selected.name+'-backups')
    if backups.is_symlink(): raise ValueError('Backup folder must not be a symbolic link')
    unit = '' if selected == CANONICAL else subprocess.check_output(['systemd-escape', '-p', '--suffix=mount', str(CANONICAL)], text=True).strip()
    if unit and (UNITS/unit).exists(): raise ValueError('Mount unit already exists; nothing changed')
    selected.mkdir(mode=0o700)
    (selected/'scripts').mkdir(mode=0o755)
    shutil.copyfile(Path(__file__), selected/'scripts/configure-storage.py')
    metadata = {'product':'ShtabAI', 'root':str(selected), 'canonical':str(CANONICAL), 'uuid':fs['uuid'], 'mount_unit':unit, 'backups':str(backups)}
    (selected/'storage.json').write_text(json.dumps(metadata, indent=2)+'\n')
    if selected != CANONICAL:
        CANONICAL.mkdir(mode=0o700)
        unit_path = UNITS/unit
        unit_path.write_text('[Unit]\nDescription=Shtab.AI selected installation disk\nRequiresMountsFor='+str(selected)+'\nConditionPathIsMountPoint='+fs['target']+'\n[Mount]\nWhat='+str(selected)+'\nWhere='+str(CANONICAL)+'\nType=none\nOptions=bind\n[Install]\nWantedBy=local-fs.target\n')
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', '--now', unit], check=True)
    for name in VOLUMES: (selected/'storage'/name).mkdir(parents=True, mode=0o700)
    (selected/'compose.storage.yaml').write_text(json.dumps(volume_configuration(CANONICAL), indent=2)+'\n')
    (selected/'backup-directory').write_text(str(backups)+'\n')
    print('Installation:', selected, '| Backups:', backups)


def verify(root=CANONICAL):
    record = json.loads((root/'storage.json').read_text())
    selected = validate_path(record['root'])
    if record.get('product') != 'ShtabAI' or record.get('canonical') != str(CANONICAL): raise ValueError('Storage ownership mismatch')
    expected_unit = '' if selected == CANONICAL else subprocess.check_output(['systemd-escape', '-p', '--suffix=mount', str(CANONICAL)], text=True).strip()
    if record.get('mount_unit') != expected_unit: raise ValueError('Storage mount unit mismatch')
    if any(part.is_symlink() for part in (selected, *selected.parents)): raise ValueError('Storage path contains a symbolic link')
    if filesystem(selected).get('uuid') != record['uuid']: raise ValueError('The installation disk is unavailable or has changed')
    if selected.is_symlink() or not os.path.samefile(selected, CANONICAL): raise ValueError('Installation mount mismatch')
    return record


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'prepare': prepare(sys.argv[2])
        elif sys.argv[1] == 'verify': verify()
        else: raise ValueError('Unknown storage command')
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
