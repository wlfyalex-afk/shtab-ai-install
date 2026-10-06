"""Per-stage observations. Elapsed durations use monotonic clock, not wall time."""
import time,uuid,math
from contextlib import contextmanager
ACTIVE={}
RUN=None
LABELS={'FETCHING':'Скачивание','ASSEMBLING':'Сборка загрузки','VERIFYING':'Проверка файла','PROBING':'Проверка медиа','CONVERTING':'Извлечение звука','CONCATENATING':'Склейка звука','TRANSCRIBING':'Распознавание','SAVING':'Сохранение результата'}
def clock(value):
    try:seconds=max(0,int(float(value)))
    except (TypeError,ValueError,OverflowError):return '—'
    return f'{seconds//60:02d}:{seconds%60:02d}'
def execute(store,sql,args=()):
    with store.connection() as c,c.cursor() as cur:cur.execute(sql,args)
def begin_run(store,row):
    global RUN
    RUN=str(uuid.uuid4())
    execute(store,'''UPDATE meeting_import_metrics SET state='INTERRUPTED',finished_at=heartbeat_at
      WHERE state='RUNNING' AND import_id IN (SELECT id FROM meeting_imports WHERE id=%s OR (group_id IS NOT NULL AND group_id=%s))''',(str(row['id']),row.get('group_id')))
@contextmanager
def stage(store,uid,name):
    uid=str(uid);event=str(uuid.uuid4());start=time.monotonic()
    execute(store,"INSERT INTO meeting_import_metrics(id,import_id,run_id,stage,state) VALUES (%s,%s,%s,%s,'RUNNING')",(event,uid,RUN or str(uuid.uuid4()),name))
    ACTIVE[uid]=(event,start)
    try:
        yield
    except BaseException:
        execute(store,"UPDATE meeting_import_metrics SET state='FAILED',finished_at=now(),heartbeat_at=now(),elapsed_seconds=%s WHERE id=%s",(time.monotonic()-start,event))
        raise
    else:
        execute(store,"UPDATE meeting_import_metrics SET state='DONE',finished_at=now(),heartbeat_at=now(),elapsed_seconds=%s WHERE id=%s",(time.monotonic()-start,event))
    finally:ACTIVE.pop(uid,None)
def progress(store,uid,seconds=None,duration=None,received=None,expected=None):
    current=ACTIVE.get(str(uid))
    if not current:return
    event,start=current
    execute(store,'''UPDATE meeting_import_metrics SET heartbeat_at=now(),elapsed_seconds=%s,
      processed_seconds=COALESCE(%s,processed_seconds),duration_seconds=COALESCE(%s,duration_seconds),
      received_bytes=COALESCE(%s,received_bytes),expected_bytes=COALESCE(%s,expected_bytes) WHERE id=%s''',
      (time.monotonic()-start,seconds,duration,received,expected,event))
def measure(store,uid,name,fn,*args,**kwargs):
    with stage(store,uid,name):return fn(*args,**kwargs)
