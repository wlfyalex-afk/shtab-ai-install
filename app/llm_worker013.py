#!/usr/bin/env python3
import argparse,json,os,time,uuid
from pathlib import Path
from psycopg2.extras import Json
from store import Store
from llm_core013 import MODEL,VERSION,Invalid,segments,chunks,fingerprint,model_identity,infer,validate
from ops_control018 import PauseRequested,CancelRequested,checkpoint as ops_checkpoint,finish as ops_finish,fail as ops_fail
from pipeline0183 import enqueue_brief
LOCKS=(13013013,8008002,74004004)
def emit(event,**fields):print(json.dumps(dict(event=event,**fields),ensure_ascii=False),flush=True)
def store():return Store(json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text()))
def take_locks(cur):
    for key in LOCKS:
        cur.execute('SELECT pg_try_advisory_lock(%s) AS locked',(key,))
        if not cur.fetchone()['locked']:return False
    return True

def persist(cur,job,candidates,raw,rejected,elapsed,identity):
    cur.execute('''SELECT j.*,t.content FROM meeting_llm_jobs j JOIN transcripts t ON t.id=j.transcript_id
      JOIN meetings m ON m.id=j.meeting_id WHERE j.id=%s AND j.organization_id=%s
      AND t.organization_id=j.organization_id AND t.meeting_id=j.meeting_id AND m.organization_id=j.organization_id FOR UPDATE OF j''',(str(job['id']),job['organization_id']))
    current=cur.fetchone()
    if not current or current['next_chunk']!=job['next_chunk'] or fingerprint(current['content'])!=job['source_hash']:raise Invalid('SOURCE_OR_JOB_CHANGED')
    created=0
    for d in candidates:
        analysis=dict(d['analysis'],job_id=str(job['id']),model=MODEL,model_digest=identity,method=VERSION)
        cur.execute('''INSERT INTO meeting_task_drafts(id,organization_id,meeting_id,transcript_id,segment_index,instruction,
          source_quote,start_seconds,end_seconds,method,candidate_key,model_analysis)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(transcript_id,segment_index,candidate_key) DO NOTHING RETURNING id''',
          (str(uuid.uuid4()),job['organization_id'],job['meeting_id'],job['transcript_id'],d['segment_index'],d['instruction'],
           d['source_quote'],d['start_seconds'],d['end_seconds'],VERSION,d['candidate_key'],Json(analysis)))
        if cur.fetchone():created+=1
    cur.execute('INSERT INTO meeting_llm_chunks(job_id,chunk_no,response,elapsed_seconds,created_count,rejected_count) VALUES (%s,%s,%s,%s,%s,%s)',
      (str(job['id']),job['next_chunk'],Json(raw),elapsed,created,rejected))
    status='DONE' if job['next_chunk']+1>=job['total_chunks'] else 'RUNNING'
    cur.execute('''UPDATE meeting_llm_jobs SET next_chunk=next_chunk+1,created_count=created_count+%s,rejected_count=rejected_count+%s,
      status=%s,model_digest=%s,error_code=NULL,updated_at=now() WHERE id=%s AND organization_id=%s''',(created,rejected,status,identity,str(job['id']),job['organization_id']))
    cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'WORKER','LLM_DRAFT_CHUNK_SAVED','MEETING',%s,%s)''',
      (str(uuid.uuid4()),job['organization_id'],job['meeting_id'],Json(dict(job_id=str(job['id']),chunk=job['next_chunk'],created=created,rejected=rejected,model_digest=identity))))
    return created,status

def once(db=None):
    if Path('/etc/shtab-ai/storage012.maintenance').exists():emit('maintenance');return
    db=db or store();job=None
    with db.connection() as c:
        with c.cursor() as cur:
            if not take_locks(cur):emit('asr_or_llm_busy');return
        c.commit()  # session locks remain; no long transaction across model inference
        with c.cursor() as cur:
            cur.execute("""SELECT j.*,t.content FROM meeting_llm_jobs j JOIN transcripts t ON t.id=j.transcript_id JOIN meetings m ON m.id=j.meeting_id
              WHERE j.status IN ('QUEUED','RUNNING') AND j.method=%s AND j.model=%s
              AND t.organization_id=j.organization_id AND t.meeting_id=j.meeting_id AND m.organization_id=j.organization_id
              AND (NOT EXISTS (SELECT 1 FROM organization_operation_state s WHERE s.organization_id=j.organization_id AND s.paused)
                OR EXISTS (SELECT 1 FROM operation_controls x WHERE x.kind='EXTRACTION' AND x.entity_id=j.id AND x.desired_state='CANCELLED'))
              AND NOT EXISTS (SELECT 1 FROM operation_controls c WHERE c.kind='EXTRACTION' AND c.entity_id=j.id AND c.desired_state='PAUSED')
              ORDER BY j.created_at,j.id LIMIT 1 FOR UPDATE OF j""",(VERSION,MODEL));job=cur.fetchone()
            if not job:emit('llm_queue_empty');return
            cur.execute("UPDATE meeting_llm_jobs SET status='RUNNING',updated_at=now() WHERE id=%s",(str(job['id']),))
        c.commit()
        try:
            ops_checkpoint(db,'EXTRACTION',str(job['id']))
            if fingerprint(job['content'])!=job['source_hash']:raise Invalid('SOURCE_CHANGED')
            work=chunks(segments(job['content']))
            if len(work)!=job['total_chunks'] or not 0<=job['next_chunk']<len(work):raise Invalid('CHUNK_PLAN_CHANGED')
            identity=model_identity()
            if job['model_digest'] and job['model_digest']!=identity:raise Invalid('MODEL_CHANGED')
            # Pin identity BEFORE inference so a restart never silently switches models.
            with c.cursor() as cur:cur.execute('UPDATE meeting_llm_jobs SET model_digest=%s WHERE id=%s',(identity,str(job['id'])))
            c.commit()
            emit('llm_chunk_started',job_id=str(job['id']),chunk=job['next_chunk']+1,total=len(work))
            started=time.monotonic();raw=infer(work[job['next_chunk']]);items,rejected=validate(raw,work[job['next_chunk']])
            ops_checkpoint(db,'EXTRACTION',str(job['id']))
            if model_identity()!=identity:raise Invalid('MODEL_CHANGED')
            brief_job_id=None
            with c.cursor() as cur:
                created,status=persist(cur,job,items,raw,rejected,time.monotonic()-started,identity)
                if status=='DONE':brief_job_id=enqueue_brief(cur,job)
            c.commit()
            if status=='DONE':ops_finish(db,'EXTRACTION',str(job['id']))
            if brief_job_id:emit('brief_auto_queued',job_id=brief_job_id,after_extraction=str(job['id']))
            emit('llm_chunk_saved',job_id=str(job['id']),created=created,rejected=rejected,status=status)
        except PauseRequested:
            c.rollback();emit('llm_paused',job_id=str(job['id']));return
        except CancelRequested:
            c.rollback()
            with c.cursor() as cur:cur.execute("UPDATE meeting_llm_jobs SET status='CANCELLED',error_code='CANCELLED_BY_ADMIN',updated_at=now() WHERE id=%s AND organization_id=%s",(str(job['id']),job['organization_id']))
            c.commit();emit('llm_cancelled',job_id=str(job['id']));return
        except Exception as exc:
            c.rollback();code=str(exc) if isinstance(exc,Invalid) else type(exc).__name__
            if not code.replace('_','').isalnum():code='MODEL_OR_DATABASE_ERROR'
            with c.cursor() as cur:cur.execute("UPDATE meeting_llm_jobs SET status='FAILED',error_code=%s,updated_at=now() WHERE id=%s AND organization_id=%s",(code[:100],str(job['id']),job['organization_id']))
            c.commit();ops_fail(db,'EXTRACTION',str(job['id']),code[:100]);emit('llm_failed',job_id=str(job['id']),error_code=code[:100]);raise SystemExit(1)

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('check','once','smoke'));a=p.parse_args()
    if a.command=='once':once();return
    if a.command=='check':
        with store().connection() as c,c.cursor() as cur:cur.execute('SELECT id,next_chunk FROM meeting_llm_jobs LIMIT 0');cur.execute('SELECT kind,entity_id FROM operation_controls LIMIT 0')
        emit('database_ok');emit('local_model_ok',model=MODEL,digest=model_identity());return
    sample=[{'index':0,'start':0,'end':8,'text':'Поручаю Иванову подготовить расчёт стоимости ремонта к пятнице.'},
            {'index':1,'start':8,'end':12,'text':'Отчёт за прошлый месяц уже утверждён.'}]
    identity=model_identity();items,rejected=validate(infer(sample),sample)
    print(json.dumps(dict(model=MODEL,model_digest=identity,candidates=items,rejected=rejected,database_changed=False),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
