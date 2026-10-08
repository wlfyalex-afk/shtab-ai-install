import shutil
from datetime import datetime,timezone
from flask import Blueprint,abort,current_app,g,jsonify
from import_common import ROOT
from metrics011 import clock,LABELS
bp=Blueprint('metrics',__name__)
@bp.get('/meetings/uploads/<uuid:uid>/metrics')
def report(uid):
    with current_app.store.connection() as c,c.cursor() as cur:
        cur.execute('SELECT id,group_id,meeting_id,status FROM meeting_imports WHERE id=%s AND organization_id=%s',(str(uid),g.user['organization_id']))
        own=cur.fetchone()
        if not own:abort(404)
        cur.execute('''SELECT id,original_name,status,uploaded_bytes,total_bytes,downloaded_bytes,expected_download_bytes,
          source_kind,upload_started_at,upload_finished_at,upload_last_at,upload_observed_bytes
          FROM meeting_imports WHERE organization_id=%s AND (id=%s OR (group_id IS NOT NULL AND group_id=%s)) ORDER BY group_position NULLS FIRST,created_at,id''',
          (g.user['organization_id'],str(uid),own['group_id']))
        parts=cur.fetchall()
        cur.execute('''SELECT e.* FROM meeting_import_metrics e JOIN meeting_imports i ON i.id=e.import_id
          WHERE i.organization_id=%s AND (i.id=%s OR (i.group_id IS NOT NULL AND i.group_id=%s)) ORDER BY e.started_at,e.id''',
          (g.user['organization_id'],str(uid),own['group_id']))
        events=cur.fetchall()
    now=datetime.now(timezone.utc);result=[]
    for p in parts:
        stages=[]
        if p['upload_started_at']:
            end=p['upload_finished_at'] or p['upload_last_at'] or p['upload_started_at']
            elapsed=max(0,(end-p['upload_started_at']).total_seconds())
            stages.append(dict(stage='Загрузка из браузера (включая паузы)',state='DONE' if p['upload_finished_at'] else 'OBSERVED',elapsed=elapsed,
              received=p['upload_observed_bytes'],expected=p['total_bytes'],speed=p['upload_observed_bytes']/elapsed if elapsed>0 else None,processed=None,duration=None,run='—',stale=False))
        for e in events:
            if str(e['import_id'])!=str(p['id']):continue
            elapsed=float(e['elapsed_seconds']);received=e['received_bytes']
            if e['stage']=='FETCHING' and e['state']=='RUNNING':
                elapsed=max(elapsed,(now-e['started_at']).total_seconds())
            stages.append(dict(stage=LABELS.get(e['stage'],e['stage']),state=e['state'],elapsed=elapsed,
              received=received if e['stage']=='FETCHING' else None,expected=e['expected_bytes'],
              speed=received/elapsed if elapsed>0 and e['stage']=='FETCHING' else None,
              processed=float(e['processed_seconds']) if e['stage'] in ('CONVERTING','TRANSCRIBING') else None,
              duration=float(e['duration_seconds']) if e['duration_seconds'] else None,
              run=str(e['run_id'])[:8],stale=e['state']=='RUNNING' and (now-e['heartbeat_at']).total_seconds()>30))
        result.append(dict(id=str(p['id']),name=p['original_name'],status=p['status'],stages=stages,
          received=p['uploaded_bytes'] if p['source_kind']=='UPLOAD' else p['downloaded_bytes'],
          expected=p['total_bytes'] if p['source_kind']=='UPLOAD' else p['expected_download_bytes']))
    try:free=shutil.disk_usage(current_app.config.get('IMPORT_ROOT',ROOT)).free
    except OSError:free=None
    return jsonify(parts=result,free_bytes=free,meeting_id=str(own['meeting_id']) if own['meeting_id'] else None,status=own['status'])
def register(app):
    app.register_blueprint(bp);app.add_template_filter(clock,'mmss')
