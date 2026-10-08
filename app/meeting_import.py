"""Authenticated upload and transcript UI. Does not execute recognition in web workers."""
import hashlib
import json
import math
import os
import shutil
import uuid
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from flask import Blueprint,abort,current_app,g,jsonify,render_template,request,redirect,url_for,Response
from psycopg2.extras import Json
from import_common import CHUNK,MAX_FILE,ROOT,LABELS,ERRORS,ImportFailure,atomic_bytes,directory,part_size,exports
from cloud_download import parsed_url,YANDEX_HOSTS

bp=Blueprint('imports',__name__)

def root():return current_app.config.get('IMPORT_ROOT',ROOT)
def store():return current_app.store

def audit(cur,uid,event):
    cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'USER',%s,%s,'MEETING_IMPORT',%s,%s)''',
      (str(uuid.uuid4()),g.user['organization_id'],g.user['person_id'],event,str(uid),Json({})))

def get_row(cur,uid,lock=False):
    cur.execute('SELECT * FROM meeting_imports WHERE id=%s AND organization_id=%s'+(' FOR UPDATE' if lock else ''),
                (str(uid),g.user['organization_id']))
    row=cur.fetchone()
    if not row:abort(404)
    return row

def requested_group(cur):
    value=request.values.get('group_id','')
    if not value:return None
    try:value=str(uuid.UUID(value))
    except ValueError:abort(400)
    cur.execute('SELECT * FROM meeting_import_groups WHERE id=%s AND organization_id=%s FOR UPDATE',(value,g.user['organization_id']))
    group=cur.fetchone()
    if not group:abort(404)
    if group['status']!='DRAFT':abort(409)
    return group

def attach_group(cur,uid,group):
    if not group:return
    cur.execute('SELECT count(*) AS n FROM meeting_imports WHERE group_id=%s',(str(group['id']),))
    n=cur.fetchone()['n']
    if n>=8:abort(409)
    cur.execute('UPDATE meeting_imports SET group_id=%s,group_position=%s,title=%s,meeting_at=%s WHERE id=%s',
      (str(group['id']),n+1,group['title'],group['meeting_at'],uid))

def public(row):
    d={k:row[k] for k in ('id','title','status','total_bytes','uploaded_bytes','processed_seconds','duration_seconds','meeting_id')}
    d['source_kind']=row.get('source_kind','UPLOAD')
    d['downloaded_bytes']=row.get('downloaded_bytes',0)
    d['expected_download_bytes']=row.get('expected_download_bytes')
    d['label']=LABELS[row['status']];d['error']=ERRORS.get(row['error_code'],'')
    return d

@bp.get('/meetings')
def index():
    try:page=int(request.args.get('page','0'))
    except ValueError:abort(400)
    if not 0<=page<=10000:abort(400)
    with store().connection() as c,c.cursor() as cur:
        cur.execute('''SELECT m.*, (SELECT count(*) FROM transcripts t WHERE t.meeting_id=m.id AND t.organization_id=m.organization_id) AS transcript_count
         FROM meetings m WHERE m.organization_id=%s ORDER BY m.created_at DESC,m.id LIMIT 26 OFFSET %s''',(g.user['organization_id'],page*25))
        meetings=cur.fetchall()
        cur.execute('SELECT * FROM meeting_imports WHERE organization_id=%s ORDER BY created_at DESC,id LIMIT 30',(g.user['organization_id'],))
        jobs=cur.fetchall()
        cur.execute('SELECT * FROM meeting_import_groups WHERE organization_id=%s ORDER BY created_at DESC LIMIT 30',(g.user['organization_id'],))
        groups=cur.fetchall()
    return render_template('imports008.html',meetings=meetings[:25],more=len(meetings)>25,page=page,jobs=jobs,labels=LABELS,groups=groups)

@bp.get('/meetings/upload')
def upload():
    uid=request.args.get('resume','')
    row=None
    if uid:
        try:uid=uuid.UUID(uid)
        except ValueError:abort(400)
        with store().connection() as c,c.cursor() as cur:row=get_row(cur,uid)
        if row['status']!='UPLOADING':return redirect(url_for('imports.job',uid=uid))
    with store().connection() as c,c.cursor() as cur:group=requested_group(cur)
    return render_template('upload008.html',row=row,group=group,timezone=g.user['timezone'],max_gib=16)

@bp.post('/meetings/uploads')
def start():
    data=request.form
    title=data.get('title','').strip();name=data.get('name','').strip()
    try:
        size=int(data.get('size','0'));date=datetime.fromisoformat(data.get('meeting_at',''))
        if date.tzinfo is not None:raise ValueError()
        date=date.replace(tzinfo=ZoneInfo(g.user['timezone']))
    except (ValueError,TypeError):abort(400)
    if not 1<=len(title)<=500 or not 1<=len(name)<=255 or not 1<=size<=MAX_FILE:abort(400)
    if any(ord(ch)<32 for ch in name):abort(400)
    uid=str(uuid.uuid4())
    with store().connection() as c,c.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(8008001)')
        group=requested_group(cur)
        cur.execute("SELECT count(*) AS n,COALESCE(sum(CASE WHEN source_kind='UPLOAD' THEN total_bytes*2 ELSE total_bytes END),0) AS reserved FROM meeting_imports WHERE status IN ('UPLOADING','QUEUED','FETCHING','ASSEMBLING','CONVERTING','TRANSCRIBING')")
        quota=cur.fetchone()
        if quota['n']>=8 or shutil.disk_usage(root()).free < quota['reserved']+size*2+10*1024**3:
            return jsonify(error='Очередь заполнена или недостаточно места. Завершите текущие загрузки.'),409
        cur.execute('''INSERT INTO meeting_imports(id,organization_id,created_by,title,meeting_at,original_name,total_bytes)
         VALUES (%s,%s,%s,%s,%s,%s,%s)''',(uid,g.user['organization_id'],g.user['person_id'],title,date,name,size))
        attach_group(cur,uid,group)
        audit(cur,uid,'MEETING_UPLOAD_STARTED')
    return jsonify(id=uid,chunk_size=CHUNK),201

@bp.get('/meetings/cloud')
def cloud_form():
    with store().connection() as c,c.cursor() as cur:group=requested_group(cur)
    return render_template('cloud0081.html',group=group,timezone=g.user['timezone'])

@bp.post('/meetings/cloud')
def cloud_start():
    title=request.form.get('title','').strip()
    link=request.form.get('url','').strip()
    kind=request.form.get('kind','YANDEX')
    try:
        host,_=parsed_url(link)
        if kind not in ('YANDEX','HTTPS') or (kind=='YANDEX' and host not in YANDEX_HOSTS):raise ValueError()
        date=datetime.fromisoformat(request.form.get('meeting_at',''))
        if date.tzinfo is not None or not 1<=len(title)<=500:raise ValueError()
        date=date.replace(tzinfo=ZoneInfo(g.user['timezone']))
    except (ValueError,ImportFailure):abort(400)
    uid=str(uuid.uuid4())
    with store().connection() as c,c.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(8008001)')
        group=requested_group(cur)
        cur.execute("SELECT count(*) AS n,COALESCE(sum(CASE WHEN source_kind='UPLOAD' THEN total_bytes*2 ELSE total_bytes END),0) AS reserved FROM meeting_imports WHERE status IN ('UPLOADING','QUEUED','FETCHING','ASSEMBLING','CONVERTING','TRANSCRIBING')")
        quota=cur.fetchone()
        if quota['n']>=8 or shutil.disk_usage(root()).free < quota['reserved']+MAX_FILE+10*1024**3:
            return render_template('error.html',message='Недостаточно места для облачной загрузки. Завершите текущие задания или освободите диск.'),409
        cur.execute("""INSERT INTO meeting_imports(id,organization_id,created_by,title,meeting_at,original_name,total_bytes,status,source_kind,source_url)
          VALUES (%s,%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s)""",(uid,g.user['organization_id'],g.user['person_id'],title,date,'Файл из облака',MAX_FILE,kind,link))
        attach_group(cur,uid,group)
        audit(cur,uid,'MEETING_CLOUD_QUEUED')
    return redirect(url_for('imports.group_view',gid=group['id']) if group else url_for('imports.job',uid=uid),303)

@bp.get('/meetings/uploads/<uuid:uid>/state')
def state(uid):
    with store().connection() as c,c.cursor() as cur:
        row=get_row(cur,uid)
        cur.execute('SELECT part_no,sha256 FROM meeting_import_chunks WHERE import_id=%s ORDER BY part_no',(str(uid),))
        chunks=cur.fetchall()
        timing={}
        if row['status']=='FETCHING':
            cur.execute("SELECT id,started_at,heartbeat_at,state,elapsed_seconds FROM meeting_import_metrics WHERE import_id=%s AND stage='FETCHING' ORDER BY started_at DESC,id DESC LIMIT 1",(str(uid),))
            event=cur.fetchone()
            if event:
                now=datetime.now(timezone.utc)
                timing=dict(download_run_id=str(event['id']),
                  download_elapsed_seconds=max(0,(now-event['started_at']).total_seconds()) if event['state']=='RUNNING' else float(event['elapsed_seconds']),
                  download_stale=event['state']!='RUNNING' or (now-event['heartbeat_at']).total_seconds()>30)
    return jsonify(**public(row),**timing,chunks={str(x['part_no']):x['sha256'].strip() for x in chunks})

@bp.post('/meetings/uploads/<uuid:uid>/chunks/<int:index>')
def chunk(uid,index):
    if request.mimetype!='application/octet-stream':abort(415)
    data=request.get_data(cache=False)
    sha=hashlib.sha256(data).hexdigest()
    if request.headers.get('X-Chunk-SHA256')!=sha:abort(400)
    with store().connection() as c,c.cursor() as cur:
        row=get_row(cur,uid,True)
        if row['status']!='UPLOADING':abort(409)
        try:expected=part_size(row['total_bytes'],index)
        except ValueError:abort(400)
        if len(data)!=expected:abort(400)
        cur.execute('SELECT sha256 FROM meeting_import_chunks WHERE import_id=%s AND part_no=%s',(str(uid),index))
        existing=cur.fetchone()
        if existing:
            if existing['sha256'].strip()!=sha:abort(409)
        else:
            if shutil.disk_usage(root()).free<10*1024**3:return jsonify(error='Недостаточно места на диске.'),507
            folder=directory(root(),uid);folder.mkdir(mode=0o750,parents=True,exist_ok=True)
            atomic_bytes(folder/f'{index:06}.chunk',data)
            cur.execute('INSERT INTO meeting_import_chunks(import_id,part_no,size_bytes,sha256) VALUES (%s,%s,%s,%s)',(str(uid),index,len(data),sha))
            cur.execute('UPDATE meeting_imports SET uploaded_bytes=uploaded_bytes+%s,upload_started_at=COALESCE(upload_started_at,now()),upload_last_at=now(),upload_observed_bytes=upload_observed_bytes+%s,updated_at=now() WHERE id=%s',(len(data),len(data),str(uid)))
    return jsonify(ok=True)

@bp.post('/meetings/uploads/<uuid:uid>/finish')
def finish(uid):
    with store().connection() as c,c.cursor() as cur:
        row=get_row(cur,uid,True)
        if row['status']=='UPLOADING':
            cur.execute('UPDATE meeting_imports SET upload_finished_at=now() WHERE id=%s',(str(uid),))
        if row['status']=='UPLOADING':
            cur.execute('SELECT count(*) AS n,COALESCE(sum(size_bytes),0) AS size FROM meeting_import_chunks WHERE import_id=%s',(str(uid),))
            parts=cur.fetchone()
            if parts['n']!=math.ceil(row['total_bytes']/CHUNK) or parts['size']!=row['total_bytes']:abort(409)
            cur.execute("UPDATE meeting_imports SET status='QUEUED',updated_at=now() WHERE id=%s",(str(uid),))
            audit(cur,uid,'MEETING_UPLOAD_QUEUED')
        elif row['status'] in ('CANCELLED','FAILED'):abort(409)
    return jsonify(url=url_for('imports.group_view',gid=row['group_id']) if row.get('group_id') else url_for('imports.job',uid=uid))

@bp.post('/meetings/uploads/<uuid:uid>/cancel')
def cancel(uid):
    with store().connection() as c,c.cursor() as cur:
        row=get_row(cur,uid,True)
        if row['status']!='UPLOADING' or row.get('group_id'):abort(409)
        # Remove only unpublished chunk files. No meetings or recordings are deleted.
        folder=directory(root(),uid)
        if folder.exists():
            for p in folder.glob('*.chunk*'):p.unlink()
        cur.execute("UPDATE meeting_imports SET status='CANCELLED',updated_at=now() WHERE id=%s",(str(uid),))
        audit(cur,uid,'MEETING_UPLOAD_CANCELLED')
    return redirect(url_for('imports.index'),303)

@bp.post('/meetings/uploads/<uuid:uid>/retry')
def retry(uid):
    with store().connection() as c,c.cursor() as cur:
        row=get_row(cur,uid,True)
        if row['status']!='FAILED':abort(409)
        if row.get('group_id'):
            cur.execute('SELECT primary_import_id FROM meeting_import_groups WHERE id=%s',(row['group_id'],))
            if str(cur.fetchone()['primary_import_id'])!=str(uid):abort(409)
        cur.execute("UPDATE meeting_imports SET status='QUEUED',attempts=0,error_code=NULL,updated_at=now() WHERE id=%s",(str(uid),))
        audit(cur,uid,'MEETING_IMPORT_RETRY')
    return redirect(url_for('imports.job',uid=uid),303)

@bp.get('/meetings/uploads/<uuid:uid>')
def job(uid):
    with store().connection() as c,c.cursor() as cur:row=get_row(cur,uid)
    return render_template('job008.html',row=row,state=public(row),labels=LABELS)

@bp.get('/meetings/<uuid:mid>')
def meeting(mid):
    try:page=int(request.args.get('page','0'))
    except ValueError:abort(400)
    if not 0<=page<=10000:abort(400)
    with store().connection() as c,c.cursor() as cur:
        cur.execute('SELECT * FROM meetings WHERE id=%s AND organization_id=%s',(str(mid),g.user['organization_id']))
        row=cur.fetchone()
        if not row:abort(404)
        cur.execute('''SELECT id,engine,model,language,content->'parts' AS parts,jsonb_array_length(content->'segments') AS count
         FROM transcripts WHERE meeting_id=%s AND organization_id=%s ORDER BY created_at DESC,id DESC LIMIT 1''',(str(mid),g.user['organization_id']))
        transcript=cur.fetchone();segments=[]
        if transcript:
            cur.execute('''SELECT value FROM transcripts t, jsonb_array_elements(t.content->'segments') WITH ORDINALITY AS s(value,n)
             WHERE t.id=%s AND t.organization_id=%s ORDER BY n LIMIT 101 OFFSET %s''',(str(transcript['id']),g.user['organization_id'],page*100))
            segments=[x['value'] for x in cur.fetchall()]
    return render_template('meeting008.html',row=row,transcript=transcript,segments=segments[:100],more=len(segments)>100,page=page)

@bp.get('/meetings/<uuid:mid>/export/<fmt>')
def export(mid,fmt):
    if fmt not in ('json','txt','srt'):abort(404)
    with store().connection() as c,c.cursor() as cur:
        cur.execute('''SELECT t.content FROM transcripts t JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
         WHERE m.id=%s AND m.organization_id=%s ORDER BY t.created_at DESC,t.id DESC LIMIT 1''',(str(mid),g.user['organization_id']))
        row=cur.fetchone()
        if not row:abort(404)
    content=row['content']
    data=json.dumps(content,ensure_ascii=False,indent=2) if fmt=='json' else exports(content)[0 if fmt=='txt' else 1]
    return Response(data,content_type=('application/json' if fmt=='json' else 'text/plain')+'; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="meeting-{mid}.{fmt}"'})

@bp.route('/meetings/groups/new',methods=['GET','POST'])
def group_new():
    if request.method=='GET':return render_template('group_new0082.html',timezone=g.user['timezone'])
    title=request.form.get('title','').strip()
    try:
        date=datetime.fromisoformat(request.form.get('meeting_at',''))
        if date.tzinfo or not 1<=len(title)<=500:raise ValueError()
        date=date.replace(tzinfo=ZoneInfo(g.user['timezone']))
    except ValueError:abort(400)
    gid=str(uuid.uuid4())
    with store().connection() as c,c.cursor() as cur:
        cur.execute('INSERT INTO meeting_import_groups(id,organization_id,created_by,title,meeting_at) VALUES (%s,%s,%s,%s,%s)',(gid,g.user['organization_id'],g.user['person_id'],title,date))
        audit(cur,gid,'MEETING_GROUP_CREATED')
    return redirect(url_for('imports.group_view',gid=gid),303)

@bp.route('/meetings/groups/<uuid:gid>',methods=['GET','POST'])
def group_view(gid):
    with store().connection() as c,c.cursor() as cur:
        cur.execute('SELECT * FROM meeting_import_groups WHERE id=%s AND organization_id=%s FOR UPDATE',(str(gid),g.user['organization_id']))
        group=cur.fetchone()
        if not group:abort(404)
        cur.execute('SELECT * FROM meeting_imports WHERE group_id=%s AND organization_id=%s ORDER BY group_position FOR UPDATE',(str(gid),g.user['organization_id']))
        parts=cur.fetchall()
        if request.method=='POST':
            if group['status']!='DRAFT':abort(409)
            action=request.form.get('action')
            if action=='start':
                if not parts or any(x['status']!='QUEUED' for x in parts):abort(409)
                cur.execute("UPDATE meeting_import_groups SET status='QUEUED',primary_import_id=%s WHERE id=%s",(parts[0]['id'],str(gid)))
                audit(cur,gid,'MEETING_GROUP_QUEUED')
            elif action in ('up','down'):
                ids=[str(x['id']) for x in parts]
                try:i=ids.index(request.form.get('part'));j=i+(-1 if action=='up' else 1)
                except ValueError:abort(400)
                if not 0<=j<len(parts):abort(400)
                cur.execute('UPDATE meeting_imports SET group_position=NULL WHERE id IN (%s,%s)',(ids[i],ids[j]))
                for k,pos in ((i,j+1),(j,i+1)):
                    cur.execute('UPDATE meeting_imports SET group_position=%s WHERE id=%s',(pos,ids[k]))
            else:abort(400)
            return redirect(url_for('imports.group_view',gid=gid),303)
    return render_template('group0082.html',group=group,parts=parts,labels=LABELS)

def register_meeting_import(app):
    app.register_blueprint(bp)
    from metrics_web011 import register
    register(app)
    from meeting_review009 import register
    register(app)
