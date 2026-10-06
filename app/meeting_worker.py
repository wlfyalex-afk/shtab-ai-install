"""One durable meeting job at a time; CPU Whisper in isolated child process."""
import argparse
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import wave
from psycopg2.extras import Json
from import_common import (ROOT,CHUNK,MAX_DURATION,ACTIVE,ImportFailure,atomic_json,
                           atomic_bytes,directory,digest_file,validate_segments,exports)
from store import Store
from cloud_download import download
from multipart_audio import concatenate,bind_segments
from ops_control018 import (PauseRequested, CancelRequested, checkpoint as ops_checkpoint,
                            finish as ops_finish, fail as ops_fail)

from metrics011 import stage,measure,progress as metrics_progress,begin_run

FORMATS='mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mp3,wav,flac,ogg,aac,avi,asf,mpeg,mpegts'

def config():
    return json.loads(Path('/etc/shtab-ai/secretary-web.json').read_text())

def asr_config():
    return dict(model=os.environ.get('MODEL_NAME','large-v3-turbo'),
                cache=os.environ.get('MODEL_CACHE','/srv/shtab-ai/response-models'),
                threads=int(os.environ.get('CPU_THREADS','4')))

def emit(event,**kw):print(json.dumps(dict(event=event,**kw),ensure_ascii=False),flush=True)

def execute(store,sql,args=(),fetch=False):
    with store.connection() as c,c.cursor() as cur:
        cur.execute(sql,args)
        return cur.fetchone() if fetch else None

def update(store,uid,status,seconds=0,duration=None):
    ops_checkpoint(store,'IMPORT',uid)
    metrics_progress(store,uid,seconds,duration)
    execute(store,'''UPDATE meeting_imports SET status=%s,processed_seconds=%s,
      duration_seconds=COALESCE(%s,duration_seconds),updated_at=now() WHERE id=%s''',(status,seconds,duration,uid))

def assemble(folder,chunks,total):
    target=folder/'source.media';tmp=folder/'source.part';h=hashlib.sha256();size=0
    with open(tmp,'wb') as out:
        for expected_index,part in enumerate(chunks):
            if part['part_no']!=expected_index:raise ImportFailure('CHUNK_MISMATCH')
            p=folder/f'{expected_index:06}.chunk'
            data=p.read_bytes()
            if len(data)!=part['size_bytes'] or hashlib.sha256(data).hexdigest()!=part['sha256'].strip():raise ImportFailure('CHUNK_MISMATCH')
            out.write(data);h.update(data);size+=len(data)
        out.flush();os.fsync(out.fileno())
    if size!=total:raise ImportFailure('CHUNK_MISMATCH')
    os.replace(tmp,target)
    return target,h.hexdigest()

def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-format_whitelist',FORMATS,
        '-show_entries','format=duration:stream=codec_type','-of','json',str(path)],
        capture_output=True,timeout=60,check=False)
    if result.returncode:raise ImportFailure('INVALID_MEDIA')
    try:
        meta=json.loads(result.stdout);duration=float(meta['format']['duration'])
        if not any(s['codec_type']=='audio' for s in meta['streams']) or not math.isfinite(duration) or duration<=0:raise ValueError()
    except (ValueError,KeyError,TypeError):raise ImportFailure('INVALID_MEDIA')
    if duration>MAX_DURATION:raise ImportFailure('TOO_LONG')
    return duration

def wait_child(command,folder,timeout,tick,log_name):
    with open(folder/log_name,'wb') as log:
        child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
        started=time.monotonic();next_tick=0
        try:
            while child.poll() is None:
                elapsed=time.monotonic()-started
                if elapsed>timeout:raise ImportFailure('PROCESS_FAILED')
                if elapsed>=next_tick:tick();next_tick=elapsed+5
                time.sleep(1)
            if child.returncode:raise ImportFailure('PROCESS_FAILED')
            tick()
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait()

def conversion(store,uid,source,folder,duration):
    progress=folder/'ffmpeg-progress.txt';progress.unlink(missing_ok=True)
    tmp=folder/'audio-16k.part.wav'
    def tick():
        sec=0
        if progress.exists():
            for line in progress.read_text(errors='replace').splitlines():
                if line.startswith('out_time_us='):
                    try:sec=int(line.split('=',1)[1])/1000000
                    except ValueError:pass
        update(store,uid,'CONVERTING',min(duration,max(0,sec)),duration)
    wait_child(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
        '-protocol_whitelist','file,pipe','-format_whitelist',FORMATS,'-i',str(source),
        '-map','0:a:0','-vn','-t',str(MAX_DURATION+1),'-ac','1','-ar','16000','-c:a','pcm_s16le',
        '-threads','2','-progress',str(progress),str(tmp)],folder,7200,tick,'conversion.log')
    with wave.open(str(tmp),'rb') as f:
        actual=f.getnframes()/f.getframerate()
        if f.getnchannels()!=1 or f.getsampwidth()!=2 or f.getframerate()!=16000 or actual<=0:raise ImportFailure('INVALID_MEDIA')
    if actual>MAX_DURATION:raise ImportFailure('TOO_LONG')
    os.replace(tmp,folder/'audio-16k.wav')
    return actual

def transcribe_child(folder):
    cfg=asr_config()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    from faster_whisper import WhisperModel
    model=WhisperModel(cfg['model'],device='cpu',compute_type='int8',cpu_threads=cfg['threads'],
                       num_workers=1,download_root=cfg['cache'],local_files_only=True)
    audio=folder/'audio-16k.wav'
    with wave.open(str(audio),'rb') as f:duration=f.getnframes()/f.getframerate()
    metadata=json.loads((folder/'source.json').read_text())
    iterator,info=model.transcribe(str(audio),language='ru',beam_size=5,vad_filter=True,
          vad_parameters={'min_silence_duration_ms':500},condition_on_previous_text=False)
    segments=[];last_update=0
    for s in iterator:
        segments.append(dict(start=s.start,end=s.end,text=s.text,
            avg_logprob=s.avg_logprob,no_speech_prob=s.no_speech_prob))
        if time.monotonic()-last_update>=5:
            atomic_json(folder/'asr-progress.json',dict(seconds=min(duration,s.end),segments=len(segments)))
            last_update=time.monotonic()
    validate_segments(segments,duration)
    if not any(s['text'].strip() for s in segments):
        atomic_json(folder/'asr-error.json',{'code':'ASR_EMPTY'});raise ImportFailure('ASR_EMPTY')
    content=dict(engine='faster-whisper',model=cfg['model'],language='ru',duration=duration,
        source_sha256=metadata['sha256'],audio_sha256=digest_file(audio),segments=segments,
        transcript=' '.join(s['text'].strip() for s in segments))
    if metadata.get('parts'):
        content['parts']=metadata['parts'];content['timeline']='concatenated_audio'
        bind_segments(content['segments'],metadata['parts'])
    atomic_json(folder/'asr.json',content)
    txt,srt=exports(content)
    atomic_bytes(folder/'asr.txt',txt.encode());atomic_bytes(folder/'asr.srt',srt.encode())

def get_source(store,row,folder):
    uid=str(row['id']);source=folder/'source.media';metadata=folder/'source.json'
    if source.exists() and metadata.exists():
        try:
            saved=json.loads(metadata.read_text())
            if source.stat().st_size==saved['size'] and measure(store,uid,'VERIFYING',digest_file,source)==saved['sha256']:
                if row.get('source_kind','UPLOAD')!='UPLOAD':
                    execute(store,"UPDATE meeting_imports SET total_bytes=%s,uploaded_bytes=%s,downloaded_bytes=%s,expected_download_bytes=%s,updated_at=now() WHERE id=%s",(saved['size'],saved['size'],saved['size'],saved['size'],uid))
                return source,saved['sha256']
        except (OSError,KeyError,ValueError):pass
    if row.get('source_kind','UPLOAD')!='UPLOAD':
        update(store,uid,'FETCHING')
        def tick(received,expected):
            ops_checkpoint(store,'IMPORT',uid)
            metrics_progress(store,uid,received=received,expected=expected)
            execute(store,"UPDATE meeting_imports SET downloaded_bytes=%s,expected_download_bytes=%s,updated_at=now() WHERE id=%s",(received,expected,uid))
        try:
            source,sha,size=measure(store,uid,'FETCHING',download,row['source_url'],row['source_kind'],folder,tick)
        except (OSError,http.client.HTTPException):raise ImportFailure('CLOUD_NETWORK')
        execute(store,"UPDATE meeting_imports SET total_bytes=%s,uploaded_bytes=%s,updated_at=now() WHERE id=%s",(size,size,uid))
        return source,sha
    if shutil.disk_usage(ROOT).free<row['total_bytes']+10*1024**3:raise ImportFailure('DISK_SPACE')
    with store.connection() as c,c.cursor() as cur:
        cur.execute('SELECT * FROM meeting_import_chunks WHERE import_id=%s ORDER BY part_no',(uid,));chunks=cur.fetchall()
    update(store,uid,'ASSEMBLING')
    source,sha=measure(store,uid,'ASSEMBLING',assemble,folder,chunks,row['total_bytes'])
    atomic_json(folder/'source.json',dict(sha256=sha,size=row['total_bytes']))
    return source,sha

def prepare_group(store,row):
    uid=str(row['id'])
    with store.connection() as c,c.cursor() as cur:
        cur.execute('SELECT * FROM meeting_imports WHERE group_id=%s AND organization_id=%s ORDER BY group_position',(row['group_id'],row['organization_id']))
        rows=cur.fetchall()
    parts=[];total=0
    for part in rows:
        folder=directory(ROOT,str(part['id']));folder.mkdir(parents=True,exist_ok=True)
        source,sha=get_source(store,part,folder)
        for chunk in folder.glob('*.chunk'):chunk.unlink()
        duration=measure(store,str(part['id']),'PROBING',probe,source)
        if total+duration>MAX_DURATION:raise ImportFailure('TOO_LONG')
        if shutil.disk_usage(ROOT).free<3*1024**3:raise ImportFailure('DISK_SPACE')
        total+=measure(store,str(part['id']),'CONVERTING',conversion,store,str(part['id']),source,folder,duration)
        parts.append(dict(id=str(part['id']),name=part['original_name'],sha=sha,audio=folder/'audio-16k.wav'))
    folder=directory(ROOT,uid)/'bundle';folder.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(ROOT).free<total*32000+3*1024**3:raise ImportFailure('DISK_SPACE')
    source,sha,duration=measure(store,uid,'CONCATENATING',concatenate,parts,folder)
    return folder,source,sha

def process(store,row):
    uid=str(row['id']);folder=directory(ROOT,uid)
    if row.get('group_id'):folder,source,sha=prepare_group(store,row)
    else:source,sha=get_source(store,row,folder)
    # Once the complete source and its hash are durable, transfer chunks are redundant.
    for part in folder.glob('*.chunk'):part.unlink()
    if shutil.disk_usage(ROOT).free<3*1024**3:raise ImportFailure('DISK_SPACE')
    duration=measure(store,uid,'PROBING',probe,source)
    proposed=str(uuid.uuid4())
    with store.connection() as c,c.cursor() as cur:
        cur.execute('''INSERT INTO meetings(id,organization_id,title,meeting_at,source_path,source_sha256,duration_seconds,processing_status)
          VALUES (%s,%s,%s,%s,%s,%s,%s,'UPLOADED') ON CONFLICT (organization_id,source_sha256) DO NOTHING RETURNING id''',
          (proposed,row['organization_id'],row['title'],row['meeting_at'],str(source),sha,duration))
        created=cur.fetchone()
        if created:mid=str(created['id'])
        else:
            cur.execute('SELECT id FROM meetings WHERE organization_id=%s AND source_sha256=%s',(row['organization_id'],sha));mid=str(cur.fetchone()['id'])
            if str(row['meeting_id'])!=mid:
                cur.execute("UPDATE meeting_imports SET status='DUPLICATE',meeting_id=%s,duration_seconds=%s,updated_at=now() WHERE id=%s",(mid,duration,uid))
                return
        cur.execute('UPDATE meeting_imports SET meeting_id=%s,duration_seconds=%s,updated_at=now() WHERE id=%s',(mid,duration,uid))
    execute(store,"UPDATE meetings SET processing_status='CONVERTING',updated_at=now() WHERE id=%s",(mid,))
    duration=measure(store,uid,'CONVERTING',conversion,store,uid,source,folder,duration)
    update(store,uid,'TRANSCRIBING',0,duration)
    execute(store,"UPDATE meetings SET processing_status='TRANSCRIBING',duration_seconds=%s,updated_at=now() WHERE id=%s",(duration,mid))
    # Recognition checkpoints are progress only. After interruption recognition restarts from beginning.
    (folder/'asr-error.json').unlink(missing_ok=True)
    (folder/'asr-progress.json').unlink(missing_ok=True)
    (folder/'asr.json').unlink(missing_ok=True)
    def tick():
        progress=folder/'asr-progress.json';sec=0
        if progress.exists():sec=json.loads(progress.read_text())['seconds']
        update(store,uid,'TRANSCRIBING',min(duration,max(0,sec)),duration)
    try:
        measure(store,uid,'TRANSCRIBING',wait_child,[sys.executable,str(Path(__file__).resolve()),'asr-child','--directory',str(folder)],
                   folder,30*3600,tick,'asr.log')
    except ImportFailure:
        if (folder/'asr-error.json').exists():raise ImportFailure('ASR_EMPTY')
        raise
    # Never publish a transcript after a stop command that arrived at the end of recognition.
    ops_checkpoint(store,'IMPORT',uid)
    with stage(store,uid,'SAVING'):
        content=json.loads((folder/'asr.json').read_text());validate_segments(content['segments'],duration)
        if content['source_sha256']!=sha:raise ImportFailure('CHUNK_MISMATCH')
        tid=str(uuid.uuid4())
        with store.connection() as c,c.cursor() as cur:
            cur.execute('SELECT status,transcript_id FROM meeting_imports WHERE id=%s FOR UPDATE',(uid,))
            current=cur.fetchone()
            if current['transcript_id']:return
            cur.execute('''INSERT INTO transcripts(id,organization_id,meeting_id,engine,model,language,content,immutable)
              VALUES (%s,%s,%s,'faster-whisper',%s,'ru',%s,true)''',(tid,row['organization_id'],mid,content['model'],Json(content)))
            from task_drafts009 import extract_drafts
            extract_drafts(cur,row['organization_id'],mid,tid,content)
            from llm_queue013 import enqueue
            from llm_core013 import Invalid as LLMInvalid
            try:
                enqueue(cur,row['organization_id'],mid,tid,content)
            except LLMInvalid:
                emit('llm_enqueue_skipped',import_id=uid)
            cur.execute("UPDATE meetings SET processing_status='REVIEW',updated_at=now() WHERE id=%s",(mid,))
            cur.execute("UPDATE meeting_imports SET status='REVIEW',transcript_id=%s,processed_seconds=%s,error_code=NULL,updated_at=now() WHERE id=%s",(tid,duration,uid))
            cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
               VALUES (%s,%s,'WORKER','MEETING_TRANSCRIBED','MEETING',%s,%s)''',
               (str(uuid.uuid4()),row['organization_id'],mid,Json(dict(import_id=uid,transcript_id=tid,source_sha256=sha))))
        # The verified original source and ASR evidence remain; redundant transfer chunks were removed.
        emit('meeting_transcribed',import_id=uid,segments=len(content['segments']))

def once(store):
    # Session lock survives commits on other connections; released even on worker crash.
    with store.connection() as lock,lock.cursor() as cur:
        cur.execute('SELECT pg_try_advisory_lock(8008002) AS locked')
        if not cur.fetchone()['locked']:emit('meeting_worker_busy');return
        row=execute(store,"""SELECT * FROM meeting_imports WHERE (status IN ('QUEUED','FETCHING','ASSEMBLING','CONVERTING','TRANSCRIBING') OR (group_id IS NOT NULL AND status IN ('REVIEW','DUPLICATE')))
                      AND (group_id IS NULL OR EXISTS (SELECT 1 FROM meeting_import_groups g WHERE g.id=meeting_imports.group_id AND g.status='QUEUED' AND g.primary_import_id=meeting_imports.id))
                      AND (NOT EXISTS (SELECT 1 FROM organization_operation_state s WHERE s.organization_id=meeting_imports.organization_id AND s.paused)
                        OR EXISTS (SELECT 1 FROM operation_controls x WHERE x.kind='IMPORT' AND x.entity_id=meeting_imports.id AND x.desired_state='CANCELLED'))
                      AND NOT EXISTS (SELECT 1 FROM operation_controls c WHERE c.kind='IMPORT' AND c.entity_id=meeting_imports.id AND c.desired_state='PAUSED')
                      ORDER BY created_at,id LIMIT 1""",fetch=True)
        if not row:emit('meeting_queue_empty');return
        uid=str(row['id'])
        try:
            ops_checkpoint(store,'IMPORT',uid)
            if row['status'] not in ('REVIEW','DUPLICATE') and row['attempts']>=3:raise ImportFailure('RETRY_LIMIT')
            execute(store,'UPDATE meeting_imports SET attempts=attempts+1,error_code=NULL,updated_at=now() WHERE id=%s',(uid,))
            begin_run(store,row)
            emit('meeting_processing',import_id=uid)
            if row['status'] not in ('REVIEW','DUPLICATE'):process(store,row)
            ops_checkpoint(store,'IMPORT',uid)
            if row.get('group_id'):
                with store.connection() as c,c.cursor() as cur:
                    cur.execute('SELECT status,meeting_id,transcript_id FROM meeting_imports WHERE id=%s',(uid,))
                    result=cur.fetchone()
                    if result['status'] not in ('REVIEW','DUPLICATE'):raise ImportFailure('PROCESS_FAILED')
                    cur.execute('UPDATE meeting_imports SET status=%s,meeting_id=%s,transcript_id=%s,updated_at=now() WHERE group_id=%s',(result['status'],result['meeting_id'],result['transcript_id'],row['group_id']))
                    cur.execute("UPDATE meeting_import_groups SET status='DONE' WHERE id=%s",(row['group_id'],))
            ops_finish(store,'IMPORT',uid)
        except PauseRequested:
            emit('meeting_paused',import_id=uid)
            return
        except CancelRequested:
            with store.connection() as c,c.cursor() as cur:
                if row.get('group_id'):
                    cur.execute("""UPDATE meeting_imports SET status='CANCELLED',error_code='CANCELLED_BY_ADMIN',updated_at=now()
                      WHERE group_id=%s AND organization_id=%s AND status NOT IN ('REVIEW','DUPLICATE')""",
                      (row['group_id'],row['organization_id']))
                else:
                    cur.execute("UPDATE meeting_imports SET status='CANCELLED',error_code='CANCELLED_BY_ADMIN',updated_at=now() WHERE id=%s AND status NOT IN ('REVIEW','DUPLICATE')",(uid,))
            emit('meeting_cancelled',import_id=uid)
            return
        except Exception as exc:
            code=str(exc) if isinstance(exc,ImportFailure) else 'PROCESS_FAILED'
            # No transcript, file content, or credentials in journal.
            emit('meeting_failed',import_id=uid,code=code,exception=type(exc).__name__,sqlstate=getattr(exc,'pgcode',None))
            with store.connection() as c,c.cursor() as cur:
                cur.execute("UPDATE meeting_imports SET status='FAILED',error_code=%s,updated_at=now() WHERE id=%s AND status NOT IN ('REVIEW','DUPLICATE') RETURNING meeting_id",(code,uid))
                failed=cur.fetchone()
                if failed and failed['meeting_id']:
                    cur.execute("UPDATE meetings SET processing_status='FAILED',updated_at=now() WHERE id=%s AND processing_status<>'REVIEW'",(failed['meeting_id'],))
            ops_fail(store,'IMPORT',uid,code)

def check(store,load_model=False):
    with store.connection() as c,c.cursor() as cur:
        cur.execute('SELECT id,group_id,group_position FROM meeting_imports LIMIT 0')
        cur.execute('SELECT id,status,primary_import_id FROM meeting_import_groups LIMIT 0')
        cur.execute('SELECT id,status,source_kind,source_url,downloaded_bytes FROM meeting_imports LIMIT 0')
        cur.execute('SELECT import_id,part_no FROM meeting_import_chunks LIMIT 0')
        cur.execute('SELECT kind,entity_id,desired_state FROM operation_controls LIMIT 0')
    for tool in ('ffmpeg','ffprobe'):
        if not shutil.which(tool):raise RuntimeError('Missing '+tool)
    if not os.access(ROOT,os.W_OK|os.X_OK):raise RuntimeError('Import directory not writable')
    import faster_whisper
    if load_model:
        os.environ['HF_HUB_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
        cfg=asr_config()
        faster_whisper.WhisperModel(cfg['model'],device='cpu',compute_type='int8',cpu_threads=cfg['threads'],
           download_root=cfg['cache'],local_files_only=True)
    emit('meeting_import_check_ok',model=asr_config()['model'],model_loaded=load_model)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['once','check','asr-child'])
    parser.add_argument('--directory');parser.add_argument('--load-model',action='store_true');args=parser.parse_args()
    if args.command=='asr-child':transcribe_child(Path(args.directory))
    elif args.command=='check':check(Store(config()),args.load_model)
    else:
        with open(ROOT/'.worker.lock','a') as guard:
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:emit('meeting_worker_busy')
            else:once(Store(config()))
