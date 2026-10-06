import hashlib
import json
import math
import os
from pathlib import Path
from uuid import UUID

CHUNK=2*1024*1024
MAX_FILE=16*1024**3
MAX_DURATION=8*3600
ROOT=Path('/srv/shtab-ai/meeting-imports')
ACTIVE=('QUEUED','FETCHING','ASSEMBLING','CONVERTING','TRANSCRIBING')
LABELS={'FETCHING':'Скачивание из облака','UPLOADING':'Загрузка файла','QUEUED':'В очереди','ASSEMBLING':'Проверка файла',
        'CONVERTING':'Подготовка звука','TRANSCRIBING':'Распознавание','REVIEW':'Стенограмма готова',
        'DUPLICATE':'Запись уже есть','FAILED':'Ошибка обработки','CANCELLED':'Загрузка отменена'}
ERRORS={'CLOUD_URL':'Нужна публичная HTTPS-ссылка на файл. Адрес недопустим.',
 'CLOUD_NETWORK':'Не удалось скачать файл. Проверьте интернет на сервере и повторите.',
 'CLOUD_HTTP':'Облако вернуло неожиданный ответ. Проверьте ссылку.',
 'CLOUD_REDIRECT':'Слишком много перенаправлений ссылки.',
 'CLOUD_ACCESS':'Файл недоступен. Проверьте доступ по ссылке и разрешение скачивания.',
 'CLOUD_RATE_LIMIT':'Облако временно ограничило скачивание. Повторите позже.',
 'CLOUD_NOT_FILE':'Ссылка ведёт на страницу или архив, а нужен аудио- или видеофайл.',
 'CLOUD_TOO_LARGE':'Размер файла превышает 16 ГиБ или не соответствует ответу сервера.',
 'CLOUD_CHANGED':'Файл в облаке изменился или сервер некорректно продолжил скачивание.',
 'CLOUD_INCOMPLETE':'Скачивание оборвалось. Нажмите «Повторить обработку».',
 'INVALID_MEDIA':'Не удалось прочитать звуковую дорожку. Проверьте файл.',
 'TOO_LONG':'Запись длиннее 8 часов. Разделите её на части.',
 'DISK_SPACE':'Недостаточно места на диске.',
 'CHUNK_MISMATCH':'Файл повреждён или загружены части разных файлов.',
 'ASR_EMPTY':'Речь не обнаружена. Проверьте звук.',
 'PROCESS_FAILED':'Обработка не завершена. Можно повторить; если ошибка повторяется, проверьте журнал.',
 'RETRY_LIMIT':'Достигнут предел попыток восстановления. Проверьте журнал.'}

class ImportFailure(Exception):pass

def directory(root,uid):return Path(root)/str(UUID(str(uid)))

def digest_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def atomic_bytes(path,data):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.part')
    with open(tmp,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def atomic_json(path,data):
    atomic_bytes(path,json.dumps(data,ensure_ascii=False,allow_nan=False).encode())

def part_size(total,index):
    if type(index) is not int or index<0 or index*CHUNK>=total:raise ValueError('Invalid part')
    return min(CHUNK,total-index*CHUNK)

def validate_segments(segments,duration):
    last=0
    for s in segments:
        if not all(math.isfinite(s[k]) for k in ('start','end','avg_logprob','no_speech_prob')):raise ImportFailure('PROCESS_FAILED')
        if s['start']<0 or s['end']<s['start'] or s['end']>duration+2 or s['start']<last-1:raise ImportFailure('PROCESS_FAILED')
        last=s['start']

def srt_time(seconds):
    ms=max(0,round(seconds*1000));hours,ms=divmod(ms,3600000);minutes,ms=divmod(ms,60000);secs,ms=divmod(ms,1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{ms:03}'

def exports(content):
    segments=content['segments']
    txt='\n'.join(f"[{s['start']:.1f}–{s['end']:.1f}] {s['text'].strip()}" for s in segments)+'\n'
    srt='\n\n'.join(f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}" for i,s in enumerate(segments,1))+'\n'
    return txt,srt
