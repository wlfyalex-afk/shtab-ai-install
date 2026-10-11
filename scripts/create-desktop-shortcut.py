#!/usr/bin/env python3
"""Create launchable desktop shortcuts for the sudo caller."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile


def export_certificate(root, account):
    source = root / 'shtab-ai-root.crt'
    if not source.is_file():
        print('Публичный сертификат ещё не создан. Выполните sudo /opt/shtab-ai/shtabctl certificate.')
        return
    destination = Path(account.pw_dir) / 'shtab-ai-root.crt'
    if destination.is_symlink():
        print(f'Путь сертификата является ссылкой; копирование остановлено: {destination}')
        return
    # Replace atomically without following a user-controlled destination link.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=account.pw_dir, prefix='.shtab-certificate-', delete=False) as out:
            temporary = Path(out.name)
            out.write(source.read_bytes())
            os.fchmod(out.fileno(), 0o644)
            os.fchown(out.fileno(), account.pw_uid, account.pw_gid)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'Сертификат для импорта в браузер: {destination}')
    return destination


def create(root, user):
    if not user or user == 'root':
        print('Нет пользователя рабочего стола. Адрес входа показан в терминале.')
        return
    account = pwd.getpwnam(user)
    export_certificate(root, account)
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
    legacy = '[Desktop Entry]\nVersion=1.0\nType=Link\nName=Штаб.AI\nURL=' + url + '\nIcon=web-browser\n'
    launcher = '[Desktop Entry]\nVersion=1.0\nType=Application\nName=Штаб.AI\nExec=xdg-open ' + url + '\nTerminal=false\nIcon=web-browser\n'
    if path.is_symlink() or (path.exists() and path.read_text() not in (legacy, launcher)):
        print(f'Изменённый ярлык сохранён: {path}')
        return
    path.write_text(launcher)
    path.chmod(0o755)
    os.chown(path, account.pw_uid, account.pw_gid)
    manager = desktop / 'ShtabAI-Manager.desktop'
    manager_launcher = '[Desktop Entry]\nVersion=1.0\nType=Application\nName=Управление Штаб.AI\nExec=sudo /opt/shtab-ai/shtabctl menu\nTerminal=true\nIcon=utilities-terminal\n'
    if manager.is_symlink() or (manager.exists() and manager.read_text() != manager_launcher):
        print(f'Существующий ярлык управления сохранён: {manager}')
        manager_record = ''
    else:
        manager.write_text(manager_launcher)
        manager.chmod(0o755)
        os.chown(manager, account.pw_uid, account.pw_gid)
        manager_record = str(manager)
    # GNOME's desktop launcher requires executable permissions and trusted metadata.
    session_env = dict(env, XDG_RUNTIME_DIR=f'/run/user/{account.pw_uid}',
                       DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{account.pw_uid}/bus')
    for shortcut in ([path, manager] if manager_record else [path]):
        if shortcut.is_symlink() or not shortcut.exists():
            continue
        try:
            result = subprocess.run(['runuser', '-u', user, '--', 'gio', 'set', str(shortcut),
                                     'metadata::trusted', 'true'], env=session_env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if result.returncode:
                print(f'Для ярлыка {shortcut.name}: правый клик → «Разрешить запуск», затем двойной щелчок.')
        except OSError:
            print(f'Для ярлыка {shortcut.name}: правый клик → «Разрешить запуск», затем двойной щелчок.')
    (root / 'desktop-shortcut.json').write_text(json.dumps({'path': str(path), 'url': url, 'manager': manager_record}))
    print(f'Ярлык входа: {path}')


if __name__ == '__main__':
    create(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else '')
