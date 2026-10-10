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
        return f'Установка остановилась: {status}'
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
            'Процент — завершённые этапы, не оставшееся время.')

def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return (result.stdout or result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def current_journal():
    invocation = command(['systemctl', 'show', 'shtab-ai-install.service', '-p', 'InvocationID', '--value'])
    if len(invocation) != 32 or any(c not in '0123456789abcdef' for c in invocation):
        return ''
    return command(['journalctl', '_SYSTEMD_INVOCATION_ID=' + invocation,
                    '-n', '40', '--no-pager', '-o', 'cat'])


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
    parser.add_argument('--timeout', type=int, default=0, help='Предельное время ожидания в секундах')
    parser.add_argument('--state-file', default='/var/lib/shtab-ai-021/status')
    args = parser.parse_args()
    live = sys.stdout.isatty() and not args.once and not args.json
    started = time.monotonic()
    previous = None
    final_text = ''
    if live:
        print('\033[?1049h\033[?25l', end='', flush=True)
    try:
        while True:
            try:
                status = pathlib.Path(args.state_file).read_text().strip()
            except FileNotFoundError:
                status = ''
            progress = read_progress(pathlib.Path(args.state_file).parent, status)
            journal = current_journal() if status == 'BUILDING_APP' or status.startswith('FAILED') else ''
            current = operation(status, journal)
            if args.json:
                # Windows PowerShell 5.1 decodes native pipes using the console
                # code page. ASCII JSON transports Unicode losslessly on any
                # code page; ConvertFrom-Json restores the original text.
                print(json.dumps(dict(status=status, progress=progress, operation=current), ensure_ascii=True))
                break
            lines = [render(status)]
            if status in ('DOWNLOADING_QWEN', 'DOWNLOADING_WHISPER'):
                lines.append(describe(progress))
            elif current:
                lines.append(current['text'])
            if status.startswith('FAILED'):
                lines.extend(['', 'Служба:', command(['systemctl', 'show', 'shtab-ai-install.service', '-p', 'ActiveState', '-p', 'SubState', '-p', 'Result']),
                              '', 'Последние сообщения:', '\n'.join(journal.splitlines()[-5:])])
            lines.extend(['', 'Ctrl+C — закрыть просмотр; установка работает отдельно.'])
            final_text = '\n'.join(lines)
            if live or final_text != previous:
                display(final_text, live=live)
                previous = final_text
            if args.once or status == 'READY_FOR_ADMIN' or status.startswith('FAILED'):
                break
            if args.timeout and time.monotonic() - started >= args.timeout:
                raise SystemExit('Время ожидания истекло. Проверьте статус установки.')
            time.sleep(3)
    except KeyboardInterrupt:
        final_text = 'Просмотр закрыт. Служба установки работает отдельно; проверьте её статус.'
    finally:
        if live:
            print('\033[?25h\033[?1049l', end='', flush=True)
            if final_text:
                print(final_text, flush=True)

if __name__ == '__main__':
    main()

