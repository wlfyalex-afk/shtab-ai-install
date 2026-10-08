#!/usr/bin/env python3
"""Create a URL shortcut for the sudo caller, never in root's desktop."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys


def create(root, user):
    if not user or user == 'root':
        print('Нет пользователя рабочего стола. Адрес входа показан в терминале.')
        return
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    # xdg-user-dir requires the real user's HOME and should not inherit root's XDG paths.
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'))
    try:
        output = subprocess.check_output(['runuser', '-u', user, '--', 'xdg-user-dir', 'DESKTOP'],
                                         env=env, text=True).strip()
        desktop = Path(output)
    except (OSError, subprocess.CalledProcessError):
        desktop = home / 'Desktop'
    if not desktop.is_absolute() or desktop == home or not desktop.is_relative_to(home):
        print('Рабочий стол не настроен. Адрес входа показан в терминале.')
        return
    values = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines()
                  if '=' in line and not line.startswith('#'))
    url = 'https://' + values['SHTAB_HTTPS_HOST'] + '/login'
    created = not desktop.exists()
    desktop.mkdir(parents=True, exist_ok=True)
    if created:
        os.chown(desktop, account.pw_uid, account.pw_gid)
    path = desktop / 'ShtabAI.desktop'
    if path.exists():
        print(f'Существующий ярлык сохранён: {path}')
        return
    path.write_text('[Desktop Entry]\nVersion=1.0\nType=Link\nName=Штаб.AI\nURL=' + url + '\nIcon=web-browser\n')
    path.chmod(0o755)
    os.chown(path, account.pw_uid, account.pw_gid)
    manager = desktop / 'ShtabAI-Manager.desktop'
    if manager.exists():
        print(f'Существующий ярлык управления сохранён: {manager}')
        manager_record = ''
    else:
        manager.write_text('[Desktop Entry]\nVersion=1.0\nType=Application\nName=Управление Штаб.AI\nExec=sudo /opt/shtab-ai-021/shtabctl menu\nTerminal=true\nIcon=utilities-terminal\n')
        manager.chmod(0o755)
        os.chown(manager, account.pw_uid, account.pw_gid)
        manager_record = str(manager)
    (root / 'desktop-shortcut.json').write_text(json.dumps({'path': str(path), 'url': url, 'manager': manager_record}))
    print(f'Ярлык входа: {path}')


if __name__ == '__main__':
    create(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else '')
