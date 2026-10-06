#!/usr/bin/env python3
"""Local maintenance of one existing Shtab.AI 021 installation."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

ROOT = Path('/opt/shtab-ai-021')
BACKUPS = Path('/var/backups/shtab-ai-021')
STATE = Path('/var/lib/shtab-ai-021')
WRITERS = ('proxy', 'web', 'meeting-worker', 'llm-worker', 'brief-worker')
PAYLOADS = ('database.dump', 'app-data.tar.gz', 'configuration.tar.gz')
IDLE_SQL = """SELECT
 (SELECT count(*) FROM meeting_imports WHERE status IN ('UPLOADING','QUEUED','FETCHING','ASSEMBLING','CONVERTING','TRANSCRIBING')) +
 (SELECT count(*) FROM meeting_llm_jobs WHERE status IN ('QUEUED','RUNNING')) +
 (SELECT count(*) FROM meeting_brief_jobs WHERE status IN ('QUEUED','RUNNING'));"""
RESET_SQL = """DO $$ DECLARE names text; BEGIN
 SELECT string_agg(format('%I.%I',n.nspname,c.relname), ',') INTO names
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('r','p');
 IF names IS NOT NULL THEN EXECUTE 'TRUNCATE TABLE ' || names || ' RESTART IDENTITY CASCADE'; END IF;
END $$;"""


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def dc(*args, **kwargs):
    return command(['docker', 'compose', '-f', str(ROOT/'compose.yaml'), *args], cwd=ROOT, **kwargs)


def sql(statement):
    return dc('exec', '-T', 'db', 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'shtab_ai', '-d', 'shtab_ai', '-At', '-c', statement, capture_output=True, text=True).stdout.strip()


def human_size(size):
    value = float(size)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if value < 1024 or unit == 'ТБ':
            number = (f'{value:.0f}' if unit == 'Б' else f'{value:.1f}').replace('.', ',')
            return f'{number} {unit}'
        value /= 1024


def human_time(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


class Progress:
    """Live heartbeat; measured bytes where possible, no invented global percent."""
    def __init__(self, label, path=None, total=None, stream=None, interval=1):
        self.label, self.path, self.total = label, path, total
        self.stream = sys.stderr if stream is None else stream
        self.interval = interval
        self.done = 0
        self.finished = threading.Event()
        self.started = time.monotonic()
        self.tty = self.stream.isatty()

    def render(self, result=None):
        elapsed = int(time.monotonic() - self.started)
        detail = f'прошло {human_time(elapsed)}'
        if self.total is not None:
            percent = 100 if result == 'готово' else min(99, self.done * 100 // max(1, self.total))
            detail += f' | {percent}% | {human_size(self.done)} из {human_size(self.total)}'
            if result == 'готово':
                detail += ' | осталось 00:00'
            elif self.done >= self.total and self.total:
                detail += ' | завершение этапа'
            elif self.done > 0 and elapsed >= 2:
                remaining = max(1, elapsed * (self.total - self.done) / self.done)
                detail += f' | осталось ≈ {human_time(remaining)}'
            else:
                detail += ' | осталось: расчёт скорости…'
        elif self.path is not None and self.path.exists():
            detail += f' | размер архива: {human_size(self.path.stat().st_size)}'
        text = f'{self.label} | {detail}' + (f' | {result}' if result else ' | выполняется')
        if self.tty:
            print('\r\033[2K' + text, end='\n' if result else '', file=self.stream, flush=True)
        else:
            print(text, file=self.stream, flush=True)

    def tick(self):
        while not self.finished.wait(self.interval if self.tty else max(5, self.interval)):
            self.render()

    def __enter__(self):
        self.render()
        self.thread = threading.Thread(target=self.tick, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.finished.set()
        self.thread.join()
        self.render('готово' if kind is None else 'ошибка / прервано')


def sha(path):
    digest = hashlib.sha256()
    with Progress('SHA-256: ' + path.name, total=path.stat().st_size) as progress, path.open('rb') as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
            progress.done += len(chunk)
    return digest.hexdigest()


class CountedReader:
    def __init__(self, source, progress):
        self.source, self.progress = source, progress

    def read(self, size=-1):
        chunk = self.source.read(size)
        self.progress.done += len(chunk)
        return chunk


class CountedWriter:
    def __init__(self, target=None, progress=None):
        self.target, self.progress, self.count = target, progress, 0

    def write(self, chunk):
        if self.target is not None: self.target.write(chunk)
        self.count += len(chunk)
        if self.progress is not None: self.progress.done = self.count
        return len(chunk)

    def flush(self):
        if self.target is not None: self.target.flush()


def validate_tar(path):
    with Progress('Проверка архива: ' + path.name, total=path.stat().st_size) as progress, path.open('rb') as source, tarfile.open(fileobj=CountedReader(source, progress), mode='r|gz') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsafe archive member: {member.name}')


def verify_backup(path):
    path = path.resolve(strict=True)
    if not path.is_dir() or not (path/'manifest.json').is_file():
        raise ValueError('Укажите КАТАЛОГ резервной копии с manifest.json, database.dump и архивами, а не каталог приложения.')
    meta = json.loads((path/'manifest.json').read_text())
    if meta.get('format') != 'shtab-ai-021-backup-v1' or meta.get('project') != 'shtab-ai-021':
        raise ValueError('Unsupported backup format/project')
    if not isinstance(meta.get('sha256'), dict) or not isinstance(meta.get('schema_sha256'), str):
        raise ValueError('Incomplete backup manifest')
    for name in PAYLOADS:
        file = path/name
        if file.is_symlink() or not file.is_file() or sha(file) != meta['sha256'].get(name):
            raise ValueError(f'Backup integrity check failed: {name}')
    for name in PAYLOADS[1:]: validate_tar(path/name)
    return path, meta


def ensure_idle():
    if int(sql(IDLE_SQL)):
        raise ValueError('Есть незавершённые загрузки/задания. Завершите или отмените их перед обслуживанием.')


def check_resources():
    project = json.loads(dc('config', '--format', 'json', capture_output=True, text=True).stdout)['name']
    if project != 'shtab-ai-021': raise ValueError('Unexpected Compose project')
    for key in ('db_data', 'app_data'):
        result = command(['docker', 'volume', 'inspect', 'shtab-ai-021_'+key], capture_output=True, text=True)
        labels = json.loads(result.stdout)[0].get('Labels') or {}
        if labels.get('com.docker.compose.project') != project or labels.get('com.docker.compose.volume') != key:
            raise ValueError('Unexpected volume ownership: '+key)
    command(['docker', 'image', 'inspect', 'alpine:3.20'], stdout=subprocess.DEVNULL)
    dc('exec', '-T', 'db', 'pg_isready', '-U', 'shtab_ai', '-d', 'shtab_ai', stdout=subprocess.DEVNULL)


@contextmanager
def locked():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE/'install.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another installer/maintenance operation is running')
        active = command(['systemctl', 'show', 'shtab-ai-install.service', '-p', 'ActiveState', '--value'], capture_output=True, text=True).stdout.strip()
        if active in ('active', 'activating'): raise ValueError('Installer service is running')
        check_resources()
        yield


@contextmanager
def paused():
    ensure_idle()
    running = dc('ps', '--services', '--status', 'running', capture_output=True, text=True).stdout.split()
    stopped = [service for service in WRITERS if service in running]
    try:
        if stopped:
            with Progress('Остановка сервисов для копирования'): dc('stop', *stopped)
        ensure_idle()
        yield stopped
    except BaseException:
        # After a data change callers suppress this resume; see destructive().
        if stopped:
            with Progress('Возобновление сервисов'): dc('start', *stopped)
        raise
    else:
        if stopped:
            with Progress('Возобновление сервисов'): dc('start', *stopped)


def transfer_process(args, source=None, target=None, progress=None):
    """Stream bytes with backpressure and check the producer/consumer exit code."""
    name = 'shtab-maint-' + str(os.getpid()) + '-' + str(time.monotonic_ns())
    if args[:2] == ['docker', 'run']:
        args = args[:2] + ['--name', name] + args[2:]
    else:
        name = None
    process = subprocess.Popen(args, stdin=subprocess.PIPE if source is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE if source is None else subprocess.DEVNULL)
    count = 0
    try:
        if source is not None:
            while chunk := source.read(1024 * 1024):
                process.stdin.write(chunk)
                process.stdin.flush()
                count += len(chunk)
                if progress is not None: progress.done = count
            process.stdin.close()
        else:
            while chunk := process.stdout.read1(1024 * 1024):
                if target is not None: target.write(chunk)
                count += len(chunk)
                if progress is not None: progress.done = count
            process.stdout.close()
        code = process.wait()
        if code: raise subprocess.CalledProcessError(code, args)
        return count
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for pipe in (process.stdin, process.stdout):
            if pipe is not None and not pipe.closed: pipe.close()
        if name is not None:
            try:
                subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except subprocess.TimeoutExpired:
                print('Не удалось завершить очистку служебного контейнера: ' + name, file=sys.stderr)


def volume_archive(destination):
    args = ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
            '-v', 'shtab-ai-021_app_data:/data:ro',
            'alpine:3.20', 'tar', '-cf', '-', '-C', '/data', '.']
    # Two passes give the exact tar stream size, including headers/padding,
    # without guessing the final compressed size. Writers remain stopped.
    with Progress('[3/6] Измерение полного объёма записей'):
        total = transfer_process(args)
    with Progress('[3/6] Упаковка записей и результатов', total=total) as progress, gzip.open(destination/'app-data.tar.gz', 'wb', compresslevel=6) as out:
        actual = transfer_process(args, target=out, progress=progress)
        if actual != total: raise ValueError('Объём записей изменился во время резервного копирования')
    return total


def configuration_archive(destination):
    names = [name for name in ('.env', 'secrets', 'tls-data', 'tls-config', 'installation-created') if (ROOT/name).exists()]
    measure = CountedWriter()
    with Progress('[4/6] Измерение полного объёма конфигурации'), tarfile.open(fileobj=measure, mode='w|') as archive:
        for name in names: archive.add(ROOT/name, arcname=name)
    with Progress('[4/6] Упаковка конфигурации', total=measure.count) as progress, gzip.open(destination/'configuration.tar.gz', 'wb', compresslevel=6) as out:
        writer = CountedWriter(out, progress)
        with tarfile.open(fileobj=writer, mode='w|') as archive:
            for name in names: archive.add(ROOT/name, arcname=name)
        if writer.count != measure.count: raise ValueError('Объём конфигурации изменился во время копирования')


def make_backup(destination=None):
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination is None:
        destination = BACKUPS/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+f'-{os.getpid()}')
        destination.mkdir(mode=0o700)
    else:
        destination = Path(destination)
        destination.mkdir(mode=0o700, exist_ok=True)
    print(f'Создание резервной копии: {destination}', flush=True)
    with Progress('[1/6] Дамп PostgreSQL', destination/'database.dump'), (destination/'database.dump').open('wb') as out:
        dc('exec', '-T', 'db', 'pg_dump', '-U', 'shtab_ai', '-d', 'shtab_ai', '-Fc', '--no-owner', '--no-acl', stdout=out)
    with Progress('[2/6] Проверка дампа PostgreSQL'), (destination/'database.dump').open('rb') as source:
        dc('exec', '-T', 'db', 'pg_restore', '--list', stdin=source, stdout=subprocess.DEVNULL)
    stream_size = volume_archive(destination)
    configuration_archive(destination)
    print('[5/6] Проверка архивов и вычисление контрольных сумм', flush=True)
    for name in PAYLOADS[1:]: validate_tar(destination/name)
    meta = dict(format='shtab-ai-021-backup-v1', project='shtab-ai-021', created_at=datetime.now(timezone.utc).isoformat(),
                schema_sha256=sha(ROOT/'database/schema-ubuntu.sql'), sha256={name:sha(destination/name) for name in PAYLOADS})
    if isinstance(stream_size, int): meta['app_data_stream_bytes'] = stream_size
    (destination/'manifest.json').write_text(json.dumps(meta, indent=2)+'\n')
    for file in destination.iterdir(): file.chmod(0o600)
    print('[6/6] Итоговая проверка резервной копии', flush=True)
    verify_backup(destination)
    print(f'Резервная копия проверена: {destination}', flush=True)
    return destination


def clear_or_restore_files(source=None):
    args = ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '-v', 'shtab-ai-021_app_data:/data']
    script = 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; '
    if source: script += 'tar -xf - -C /data; '
    script += 'mkdir -p /data/meeting-imports /data/response-evidence /data/evidence /data/logs; chown -R 10001:10001 /data'
    if source:
        # Works for old and new backups: derive total from the verified stream.
        with Progress('Измерение полного объёма восстанавливаемых записей'), gzip.open(source/'app-data.tar.gz', 'rb') as stream:
            total = 0
            while chunk := stream.read(1024 * 1024): total += len(chunk)
        with Progress('Восстановление записей и результатов', total=total) as progress, gzip.open(source/'app-data.tar.gz', 'rb') as stream:
            transfer_process(args+['-i', 'alpine:3.20', 'sh', '-ec', script], source=stream, progress=progress)
    else:
        command(args+['alpine:3.20', 'sh', '-ec', script])


def confirm(word, message):
    print(message)
    if input(f'Введите {word}: ').strip() != word:
        print('Отменено; данные не изменены.')
        return False
    return True


def destructive(kind, source=None):
    if kind == 'restore':
        print(f'Выбрана резервная копия: {source}\nПроверяем её до изменения данных...', flush=True)
        source, meta = verify_backup(source)
        if meta['schema_sha256'] != sha(ROOT/'database/schema-ubuntu.sql'):
            raise ValueError('Schema differs; restore requires the same 021 schema')
        # Inspect dump before any data changes.
        with Progress('Проверка дампа для восстановления'), (source/'database.dump').open('rb') as f:
            dc('exec', '-T', 'db', 'pg_restore', '--list', stdin=f, stdout=subprocess.DEVNULL)
        size = sum((source/name).stat().st_size for name in PAYLOADS)
        word, message = 'RESTORE-SHTAB-021', f'Копия: {source}\nСоздана (UTC): {meta.get("created_at", "не указано")}\nРазмер файлов копии: {human_size(size)}\nТекущая БД, пользователи и записи будут заменены этой копией.\nСначала будет сохранена текущая версия. Ключи HTTPS будут восстановлены; пароли подключения к БД и .env останутся текущими.'
    else:
        word, message = 'CLEAR-SHTAB-021', 'Будут удалены ВСЕ данные БД, включая пользователей и организации, и загруженные записи.\nМодели, схема БД и сертификаты сохранятся. Сначала будет создана резервная копия.'
    if not confirm(word, message): return
    ensure_idle()
    running = dc('ps', '--services', '--status', 'running', capture_output=True, text=True).stdout.split()
    stopped = [s for s in WRITERS if s in running]
    changed = False
    safety = None
    try:
        if stopped:
            with Progress('Остановка сервисов перед обслуживанием'): dc('stop', *stopped)
        ensure_idle()
        print('Страховочная копия текущего состояния перед изменением данных:', flush=True)
        safety = make_backup()
        if kind == 'restore':
            # Extract and validate configuration in a private staging directory before replacing TLS storage.
            with tempfile.TemporaryDirectory(prefix='shtab-restore-') as d:
                staging = Path(d)
                config = source/'configuration.tar.gz'
                with Progress('Подготовка сохранённой конфигурации', total=config.stat().st_size) as progress, config.open('rb') as stream, tarfile.open(fileobj=CountedReader(stream, progress), mode='r|gz') as archive:
                    archive.extractall(staging, filter='data')
                changed = True
                dump = source/'database.dump'
                with Progress('Восстановление PostgreSQL: передача дампа', total=dump.stat().st_size) as progress, dump.open('rb') as f:
                    transfer_process(['docker', 'compose', '-f', str(ROOT/'compose.yaml'), 'exec', '-T', 'db', 'pg_restore', '-U', 'shtab_ai', '-d', 'shtab_ai', '--clean', '--if-exists', '--no-owner', '--no-acl', '--single-transaction'], source=f, progress=progress)
                clear_or_restore_files(source)
                with Progress('Восстановление сертификатов'):
                    for name in ('tls-data', 'tls-config'):
                        if (staging/name).is_dir():
                            if (ROOT/name).exists(): shutil.rmtree(ROOT/name)
                            shutil.copytree(staging/name, ROOT/name)
                            (ROOT/name).chmod(0o700)
                print('БД, записи и сохранённый УЦ восстановлены. При смене УЦ обновите доверие на клиентских ПК.')
        else:
            changed = True
            with Progress('Очистка базы данных'): sql(RESET_SQL)
            with Progress('Удаление записей и результатов'): clear_or_restore_files()
            (STATE/'status').write_text('READY_FOR_ADMIN\n')
            print('База очищена. Создайте первого администратора через shtabctl bootstrap.')
        with Progress('Проверка приложения после обслуживания'):
            dc('run', '--rm', '--no-deps', 'web', 'python', 'manage.py', 'check')
    except BaseException:
        if changed:
            print(f'Операция не завершена. Web/обработчики оставлены остановленными. Копия до изменения: {safety}', file=sys.stderr)
        elif stopped:
            with Progress('Возобновление сервисов'): dc('start', *stopped)
        raise
    else:
        if stopped:
            with Progress('Возобновление сервисов'): dc('start', *stopped)
        print('Ранее работавшие сервисы запущены. Проверьте вход и результаты в браузере.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('backup', 'backups', 'restore', 'reset-db'))
    parser.add_argument('path', nargs='?')
    args = parser.parse_args()
    if os.geteuid() != 0: raise SystemExit('Run with sudo')
    os.umask(0o077)
    if args.action == 'backups':
        for path in sorted(BACKUPS.glob('*/manifest.json')):
            print(path.parent)
        return
    if args.action == 'restore' and not args.path: raise ValueError('Specify a backup directory')
    with locked():
        if args.action == 'backup':
            with paused(): make_backup()
        elif args.action == 'restore': destructive('restore', Path(args.path))
        else: destructive('reset-db')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError, EOFError, KeyboardInterrupt) as exc:
        print(f'Обслуживание остановлено: {exc}', file=sys.stderr)
        sys.exit(1)
