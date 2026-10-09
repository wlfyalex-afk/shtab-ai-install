"""Atomic, non-interactive download telemetry shared by installer viewers."""
import json
import os
from pathlib import Path
import time


class Reporter:
    def __init__(self, model, path=None):
        self.model = model
        self.path = Path(path or os.environ.get('SHTAB_PROGRESS_FILE', f'/var/lib/shtab-ai-021/{model}-progress.json'))
        self.previous = None
        self.last_write = 0

    def update(self, completed=0, total=0, detail='', phase='download', force=False):
        now = time.monotonic()
        if not force and now - self.last_write < 1:
            return
        key = (detail, phase)
        speed = 0
        if self.previous and self.previous[0] == key:
            elapsed = now - self.previous[2]
            if elapsed > 0:
                speed = max(0, completed - self.previous[1]) / elapsed
        self.previous = (key, completed, now)
        self.last_write = now
        data = dict(model=self.model, completed=int(completed), total=int(total),
                    bytes_per_second=round(speed), detail=detail, phase=phase,
                    updated_at=time.time(), eta_seconds=round((total-completed)/speed) if speed > 0 and total > completed else None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data), encoding='utf-8')
        os.replace(temporary, self.path)


def read_progress(state, status):
    model = {'DOWNLOADING_QWEN': 'qwen', 'DOWNLOADING_WHISPER': 'whisper'}.get(status)
    if not model:
        return None
    try:
        data = json.loads((state / f'{model}-progress.json').read_text())
        if data['model'] != model:
            return None
        data['stale'] = time.time() - data['updated_at'] > 20
        if data['stale']:
            data['bytes_per_second'] = 0
            data['eta_seconds'] = None
        return data
    except (OSError, ValueError, KeyError):
        return None


def size(value):
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if value < 1024 or unit == 'ТБ':
            return f'{value:.1f} {unit}'
        value /= 1024


def describe(data):
    if not data:
        return 'Ожидаем сведения от загрузчика модели…'
    if data['phase'] != 'download':
        return {'verify': 'Файлы получены. Проверка модели…', 'done': 'Модель готова.', 'error': 'Ошибка загрузки — см. журнал.'}.get(data['phase'], 'Подготовка загрузки…')
    amount = size(data['completed'])
    if data['total']:
        amount += f" / {size(data['total'])} ({min(100, 100*data['completed']/data['total']):.1f}%)"
    else:
        amount += ' / общий объём уточняется'
    rate = size(data['bytes_per_second']) + '/с'
    eta = f"≈ {data['eta_seconds']//60} мин {data['eta_seconds']%60} с" if data['eta_seconds'] is not None else 'уточняется'
    waiting = ' | ожидаем новые данные' if data.get('stale') else ''
    return f"{data['detail']} | {amount} | {rate} | осталось {eta}{waiting}"
