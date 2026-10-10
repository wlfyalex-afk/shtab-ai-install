#!/usr/bin/env python3
"""Collect bounded service diagnostics without copying configuration or user files."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import pwd
import re
import subprocess
import uuid

ROOT = Path('/opt/shtab-ai-021')


def redact(text):
    text = re.sub(r'(?i)\b(password|passwd|secret|token|authorization)\b\s*[:=]\s*[^\r\n]+', r'\1=[скрыто]', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s]+', 'Bearer [скрыто]', text)
    return text


def capture(title, args):
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=8, errors='replace')
        output = result.stdout
        status = result.returncode
    except subprocess.TimeoutExpired:
        output, status = 'Команда не ответила за 8 секунд.', 'timeout'
    except OSError as error:
        output, status = str(error), 'unavailable'
    return f'\n=== {title} (код: {status}) ===\n' + redact(output[-262144:]) + '\n'


def report(root=ROOT):
    parts = ['Штаб.AI — минимальная диагностика\n',
             'Поддержка: wlfyalex@gmail.com\n',
             'UTC: ' + datetime.now(timezone.utc).isoformat() + '\n',
             'ОС: ' + platform.platform() + '\n']
    try:
        parts.append('Дистрибутив: ' + platform.freedesktop_os_release().get('PRETTY_NAME', '') + '\n')
    except OSError:
        pass
    dc = ['bash', str(root / 'scripts/dc.sh')]
    commands = [
        ('Память', ['free', '-h']),
        ('Свободное место', ['df', '-h', str(root)]),
        ('Служба установки', ['systemctl', 'show', 'shtab-ai-install', '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result']),
        ('Журнал установки', ['journalctl', '-u', 'shtab-ai-install', '-n', '300', '--no-pager']),
        ('Состояние контейнеров', dc + ['ps', '-a']),
        ('Ресурсы контейнеров', ['docker', 'stats', '--no-stream', '--format', '{{.Name}}: CPU={{.CPUPerc}} RAM={{.MemUsage}}',
                                  'shtab-ai-021-web-1', 'shtab-ai-021-meeting-worker-1',
                                  'shtab-ai-021-llm-worker-1', 'shtab-ai-021-brief-worker-1']),
        ('Журнал приложения за 2 часа', dc + ['logs', '--no-color', '--since', '2h', '--tail', '100',
                                             'web', 'meeting-worker', 'llm-worker', 'brief-worker', 'proxy']),
    ]
    if not (root / 'windows-acceleration').exists():
        commands.append(('Журнал Ollama за 2 часа', dc + ['logs', '--no-color', '--since', '2h', '--tail', '100', 'ollama']))
    for title, args in commands:
        parts.append(capture(title, args))
    for name in ('acceleration', 'windows-acceleration', 'qwen-compute.json'):
        try:
            parts.append('\n=== ' + name + ' ===\n' + redact((root / name).read_text()[:8192]) + '\n')
        except OSError:
            pass
    parts.append('\nОтчёт не включает .env, пароли подключения, базу, записи и стенограммы.\n'
                 'Сообщения сервисов могут содержать рабочие данные — просмотрите файл перед отправкой.\n')
    return ''.join(parts)


def save(text):
    user = pwd.getpwnam(os.environ['SUDO_USER']) if os.environ.get('SUDO_USER') else pwd.getpwuid(os.getuid())
    folder = Path(user.pw_dir) / 'ShtabAI-support'
    if folder.is_symlink():
        raise ValueError('Папка диагностики не должна быть символической ссылкой.')
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / ('ShtabAI-diagnostics-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.txt')
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)
    path.chmod(0o600)
    if os.geteuid() == 0:
        os.chown(folder, user.pw_uid, user.pw_gid)
        os.chown(path, user.pw_uid, user.pw_gid)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stdout', action='store_true')
    args = parser.parse_args()
    if args.stdout:
        print(report())
    else:
        print('Собираем состояние сервисов и последние журналы. Приложение продолжает работать.', flush=True)
        path = save(report())
        print('Диагностика сохранена: ' + str(path))
        print('Приложите файл и скриншот к письму на wlfyalex@gmail.com.')
