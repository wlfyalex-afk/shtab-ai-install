#!/usr/bin/env python3
"""Read-only progress display for Shtab.AI installer 021."""
import argparse
import pathlib
import subprocess
import sys
import time
import json
from model_progress import read_progress, describe
from install_operation import operation

STAGES = [
    ('INSTALLING_DEPENDENCIES', 'Установка зависимостей'),
    ('BUILDING_APP', 'Сборка образа приложения'),
    ('INITIALIZING_DATABASE', 'Подготовка базы данных и Ollama'),
    ('DOWNLOADING_QWEN', 'Загрузка модели Qwen'),
    ('DOWNLOADING_WHISPER', 'Загрузка модели Whisper'),
    ('CHECKING_DATABASE_AND_MODELS', 'Проверка базы данных и моделей'),
    ('STARTING_SERVICES', 'Запуск сервисов'),
]

def render(status):
    if status == 'READY_FOR_ADMIN':
        count, title = len(STAGES), 'Компоненты готовы — можно создать администратора'
    elif status.startswith('FAILED'):
        return f'Установка остановилась: {status}\nПодробности — в журнале ниже.'
    else:
        match = next(((i, title) for i, (key, title) in enumerate(STAGES) if key == status), None)
        if match is None:
            return f'Ожидание статуса установки: {status or "статус ещё не записан"}'
        count, title = match
    percent = round(count / len(STAGES) * 100)
    filled = round(count / len(STAGES) * 28)
    bar = '█' * filled + '░' * (28 - filled)
    return (f'Штаб.AI — установка\n[{bar}] {percent}% этапов завершено ({count}/{len(STAGES)})\n'
            f'Текущий этап: {title}\n'
            'Этапы имеют разную длительность; процент не является оценкой оставшегося времени.')

def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return (result.stdout or result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def display(text, live=False):
    if live:
        # Repaint the same reserved lines; do not clear the whole terminal or
        # append another screen on each poll. Clip to prevent line wrapping.
        import shutil
        width = max(20, shutil.get_terminal_size().columns - 1)
        lines = text.splitlines()
        print('\033[H' + '\n'.join('\033[2K' + line[:width] for line in lines) + '\033[J', end='', flush=True)
    else:
        print(text, flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Показать один раз')
    parser.add_argument('--json', action='store_true', help='Статус и сведения о модели в JSON')
    parser.add_argument('--state-file', default='/var/lib/shtab-ai-021/status')
    args = parser.parse_args()
    try:
        while True:
            try:
                status = pathlib.Path(args.state_file).read_text().strip()
            except FileNotFoundError:
                status = ''
            progress = read_progress(pathlib.Path(args.state_file).parent, status)
            journal = command(['journalctl', '-u', 'shtab-ai-install', '-n', '40', '--no-pager', '-o', 'cat']) if status == 'BUILDING_APP' or not args.json else ''
            current = operation(status, journal)
            if args.json:
                print(json.dumps(dict(status=status, progress=progress, operation=current), ensure_ascii=False))
                break
            lines = [render(status), '']
            if status in ('DOWNLOADING_QWEN', 'DOWNLOADING_WHISPER'):
                lines.append(describe(progress))
            elif current:
                lines.extend([current['title'], current['text']])
            if args.once or status.startswith('FAILED'):
                lines.extend(['', 'Служба:', command(['systemctl', 'show', 'shtab-ai-install.service', '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result']),
                              '', 'Последние сообщения:', '\n'.join(journal.splitlines()[-5:])])
            lines.extend(['', 'Ctrl+C — закрыть экран; установка продолжится.'])
            display('\n'.join(lines), live=sys.stdout.isatty() and not args.once)
            if args.once or status == 'READY_FOR_ADMIN' or status.startswith('FAILED'):
                break
            time.sleep(3)
    except KeyboardInterrupt:
        print('\nПросмотр закрыт. Установка продолжает работать в фоне.')

if __name__ == '__main__':
    main()

