import os,uuid,wave
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Blueprint,current_app,g,abort,request,redirect,url_for,render_template,send_file,flash
from psycopg2.extras import Json
from task_drafts009 import extract_drafts,add_candidate
bp=Blueprint('meeting_review',__name__)
ROOT=Path('/srv/shtab-ai/meeting-imports')

def owned(cur,mid):
    cur.execute('SELECT * FROM meetings WHERE id=%s AND organization_id=%s',(str(mid),g.user['organization_id']))
    row=cur.fetchone()
    if not row:abort(404)
    return row

def latest(cur,mid):
    cur.execute('SELECT * FROM transcripts WHERE meeting_id=%s AND organization_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(str(mid),g.user['organization_id']))
    row=cur.fetchone()
    if not row:abort(404)
    return row

def selected_filters(source):
    status=source.get('status',source.get('return_status','')).upper()
    if status not in ('PENDING','APPROVED','REJECTED'):status=''
    person=source.get('person',source.get('return_person',''))
    if person:
        try:person=str(uuid.UUID(person))
        except ValueError:person=''
    return status,person

def review_redirect(mid,did,new_status=None,new_person=None):
    status,person=selected_filters(request.form)
    # The processed card must remain visible even if its status/assignee no longer matches the old filter.
    if new_status is not None and status and status!=new_status:status=''
    if new_person is not None and person and person!=new_person:person=''
    values=dict(mid=mid,_anchor=f'draft-{did}')
    if status:values['status']=status
    if person:values['person']=person
    return redirect(url_for('meeting_review.drafts',**values),303)

def audio_path(source,root=ROOT):
    # Only normalized audio next to an authorized imported original/assembly.
    target=(Path(source).parent/'audio-16k.wav').resolve();base=root.resolve()
    if not target.is_relative_to(base) or not target.is_file():raise ValueError('Audio unavailable')
    with wave.open(str(target),'rb') as f:
        if (f.getnchannels(),f.getframerate(),f.getsampwidth())!=(1,16000,2):raise ValueError('Invalid audio')
    return target

@bp.get('/meetings/<uuid:mid>/audio')
def audio(mid):
    with current_app.store.connection() as c,c.cursor() as cur:row=owned(cur,mid)
    try:path=audio_path(row['source_path'],current_app.config.get('IMPORT_ROOT',ROOT))
    except (OSError,ValueError,wave.Error):abort(404)
    return send_file(path,mimetype='audio/wav',conditional=True,etag=True,max_age=0)

@bp.post('/meetings/<uuid:mid>/drafts/extract')
def extract(mid):
    with current_app.store.connection() as c,c.cursor() as cur:
        owned(cur,mid);t=latest(cur,mid)
        count=extract_drafts(cur,g.user['organization_id'],str(mid),str(t['id']),t['content'])
    flash(f'Добавлено черновиков: {count}. Проверьте формулировки и исполнителей.')
    return redirect(url_for('meeting_review.drafts',mid=mid),303)

@bp.post('/meetings/<uuid:mid>/drafts/from-segment')
def from_segment(mid):
    try:index=int(request.form['index']);tid=str(uuid.UUID(request.form['transcript_id']))
    except (KeyError,ValueError):abort(400)
    with current_app.store.connection() as c,c.cursor() as cur:
        owned(cur,mid);t=latest(cur,mid)
        if str(t['id'])!=tid:abort(409)
        if not 0<=index<len(t['content']['segments']):abort(400)
        add_candidate(cur,g.user['organization_id'],str(mid),tid,t['content'],index,'manual')
    return redirect(url_for('meeting_review.drafts',mid=mid),303)

@bp.get('/meetings/<uuid:mid>/drafts')
def drafts(mid):
    filter_status,filter_person=selected_filters(request.args)
    with current_app.store.connection() as c,c.cursor() as cur:
        meeting=owned(cur,mid)
        cur.execute('''SELECT d.*,t.primary_assignee_id,t.due_at AS task_due_at,p.display_name AS assignee_name
          FROM meeting_task_drafts d
          LEFT JOIN tasks t ON t.id=d.task_id AND t.organization_id=d.organization_id
          LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=d.organization_id
          WHERE d.meeting_id=%s AND d.organization_id=%s ORDER BY d.start_seconds,d.id''',(str(mid),g.user['organization_id']));all_rows=cur.fetchall()
        cur.execute('SELECT id,display_name FROM people WHERE organization_id=%s AND active ORDER BY display_name',(g.user['organization_id'],));people=cur.fetchall()
    for draft_no,row in enumerate(all_rows,1):row['draft_no']=draft_no
    counts={status:sum(1 for row in all_rows if row['status']==status) for status in ('PENDING','APPROVED','REJECTED')}
    rows=[row for row in all_rows if (not filter_status or row['status']==filter_status) and
          (not filter_person or str(row['primary_assignee_id'] or '')==filter_person)]
    return render_template('drafts009.html',meeting=meeting,rows=rows,all_rows=all_rows,people=people,
      counts=counts,filter_status=filter_status,filter_person=filter_person)

@bp.post('/meetings/<uuid:mid>/drafts/<uuid:did>')
def review(mid,did):
    action=request.form.get('action')
    if action not in ('approve','reject'):abort(400)
    rejection_reason=request.form.get('rejection_reason','').strip() if action=='reject' else None
    if action=='reject' and not 3<=len(rejection_reason)<=2000:
        flash('Укажите причину отклонения: от 3 до 2000 символов.')
        return review_redirect(mid,did)
    try:version=int(request.form['version'])
    except (KeyError,ValueError):abort(400)
    with current_app.store.connection() as c,c.cursor() as cur:
        owned(cur,mid)
        cur.execute('SELECT * FROM meeting_task_drafts WHERE id=%s AND meeting_id=%s AND organization_id=%s FOR UPDATE',(str(did),str(mid),g.user['organization_id']));d=cur.fetchone()
        if not d:abort(404)
        if d['status']!='PENDING' or d['version']!=version:abort(409)
        task=None
        if action=='approve':
            instruction=request.form.get('instruction','').strip()
            if not 1<=len(instruction)<=5000:abort(400)
            try:person=str(uuid.UUID(request.form['person']))
            except (KeyError,ValueError):abort(400)
            cur.execute('SELECT id FROM people WHERE id=%s AND organization_id=%s AND active',(person,g.user['organization_id']))
            if not cur.fetchone():abort(400)
            date=None
            if request.form.get('due_at'):
                try:
                    date=datetime.fromisoformat(request.form['due_at'])
                    if date.tzinfo:raise ValueError()
                    date=date.replace(tzinfo=ZoneInfo(g.user['timezone']))
                except ValueError:abort(400)
            task=str(uuid.uuid4());span=[dict(transcript_id=str(d['transcript_id']),start=d['start_seconds'],end=d['end_seconds'],quote=d['source_quote'])]
            cur.execute('''INSERT INTO tasks(id,organization_id,meeting_id,instruction,lifecycle,execution_status,
              primary_assignee_id,due_at,due_resolution,source_spans,review_status,approved_by,approved_at)
              VALUES (%s,%s,%s,%s,'APPROVED','PENDING',%s,%s,%s,%s,'CONFIRMED',%s,now())''',
              (task,g.user['organization_id'],str(mid),instruction,person,date,'RESOLVED' if date else 'NOT_STATED',Json(span),g.user['person_id']))
            cur.execute("INSERT INTO task_assignees(task_id,person_id,role) VALUES (%s,%s,'PRIMARY')",(task,person))
        cur.execute('''UPDATE meeting_task_drafts SET status=%s,task_id=%s,instruction=%s,rejection_reason=%s,version=version+1,
          reviewed_by=%s,reviewed_at=now() WHERE id=%s''',('APPROVED' if task else 'REJECTED',task,instruction if task else d['instruction'],rejection_reason,g.user['person_id'],str(did)))
        cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
          VALUES (%s,%s,'USER',%s,'MEETING_DRAFT_REVIEWED','MEETING_TASK_DRAFT',%s,%s)''',
          (str(uuid.uuid4()),g.user['organization_id'],g.user['person_id'],str(did),Json(dict(action=action,task_id=task,rejection_reason=rejection_reason))))
    flash('Поручение добавлено на панель руководителя. Обзвон не назначен.' if task else 'Черновик отклонён.')
    return review_redirect(mid,did,'APPROVED' if task else 'REJECTED',person if task else '')

def register(app):app.register_blueprint(bp)
