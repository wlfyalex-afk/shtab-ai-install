"""Organization-scoped meeting briefs with explicit human approval."""
import uuid

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from psycopg2.extras import Json

from brief_core017 import MODEL, VERSION, brief_chunks, fingerprint
from meeting_review009 import latest, owned

bp = Blueprint('brief017', __name__)


def editable():
    return bool(g.user and (g.user.get('is_admin') or g.user.get('app_role', 'secretary') == 'secretary'))


def require_editor():
    if not editable():
        abort(403)


def audit(cur, event, entity_type, entity_id, payload=None):
    cur.execute('''INSERT INTO audit_events
      (id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'USER',%s,%s,%s,%s,%s)''',
      (str(uuid.uuid4()), g.user['organization_id'], g.user['person_id'], event,
       entity_type, str(entity_id), Json(payload or {})))


@bp.get('/meetings/<uuid:mid>/brief')
def page(mid):
    with current_app.store.connection() as c, c.cursor() as cur:
        meeting = owned(cur, mid)
        cur.execute('''SELECT * FROM meeting_brief_jobs
          WHERE meeting_id=%s AND organization_id=%s ORDER BY created_at DESC,id DESC LIMIT 20''',
          (str(mid), g.user['organization_id']))
        jobs = cur.fetchall()
        cur.execute('''SELECT * FROM meeting_briefs
          WHERE meeting_id=%s AND organization_id=%s ORDER BY created_at DESC,id DESC LIMIT 1''',
          (str(mid), g.user['organization_id']))
        brief = cur.fetchone()
    source_map = {item['id']: item for item in (brief['content'].get('sources', []) if brief else [])}
    return render_template('brief017.html', meeting=meeting, jobs=jobs, brief=brief,
                           source_map=source_map, can_edit=editable())


@bp.post('/meetings/<uuid:mid>/brief/queue')
def queue(mid):
    require_editor()
    with current_app.store.connection() as c, c.cursor() as cur:
        owned(cur, mid)
        transcript = latest(cur, mid)
        chunks = brief_chunks(transcript['content'])
        if not chunks:
            flash('В стенограмме нет фрагментов для подготовки брифа.')
            return redirect(url_for('brief017.page', mid=mid), 303)
        job_id = str(uuid.uuid4())
        cur.execute('''INSERT INTO meeting_brief_jobs
          (id,organization_id,meeting_id,transcript_id,method,model,source_hash,total_chunks,created_by)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT DO NOTHING RETURNING id''',
          (job_id, g.user['organization_id'], str(mid), str(transcript['id']), VERSION, MODEL,
           fingerprint(transcript['content']), len(chunks), g.user['person_id']))
        created = cur.fetchone()
        if created:
            audit(cur, 'MEETING_BRIEF_QUEUED', 'MEETING_BRIEF_JOB', job_id,
                  {'meeting_id': str(mid), 'transcript_id': str(transcript['id'])})
    flash('Подготовка брифа поставлена в очередь.' if created else
          'Для этой версии стенограммы бриф уже поставлен в очередь или подготовлен.')
    return redirect(url_for('brief017.page', mid=mid), 303)


@bp.post('/meetings/<uuid:mid>/brief/jobs/<uuid:jid>/retry')
def retry(mid, jid):
    require_editor()
    with current_app.store.connection() as c, c.cursor() as cur:
        owned(cur, mid)
        cur.execute('''SELECT id,error_code,transcript_id,method FROM meeting_brief_jobs
          WHERE id=%s AND meeting_id=%s AND organization_id=%s AND status='FAILED' FOR UPDATE''',
          (str(jid), str(mid), g.user['organization_id']))
        failed = cur.fetchone()
        if not failed:
            abort(409)
        cur.execute('''SELECT id FROM meeting_brief_jobs WHERE transcript_id=%s AND method=%s
          AND status IN ('QUEUED','RUNNING') LIMIT 1''', (str(failed['transcript_id']), failed['method']))
        if cur.fetchone():
            flash('Для этой стенограммы уже выполняется другое задание.')
            return redirect(url_for('brief017.page', mid=mid), 303)
        if failed['error_code'] == 'MODEL_CHANGED':
            cur.execute('DELETE FROM meeting_brief_chunks WHERE job_id=%s', (str(jid),))
            cur.execute('''UPDATE meeting_brief_jobs SET status='QUEUED',error_code=NULL,model_digest=NULL,retry_count=retry_count+1,
              next_chunk=0,evidence_count=0,rejected_count=0,last_stage='QUEUED',updated_at=now() WHERE id=%s''', (str(jid),))
        else:
            cur.execute("UPDATE meeting_brief_jobs SET status='QUEUED',error_code=NULL,retry_count=retry_count+1,last_stage='QUEUED',updated_at=now() WHERE id=%s",
                        (str(jid),))
        audit(cur, 'MEETING_BRIEF_RETRIED', 'MEETING_BRIEF_JOB', jid, {'meeting_id': str(mid)})
    return redirect(url_for('brief017.page', mid=mid), 303)


@bp.post('/meetings/<uuid:mid>/brief/<uuid:bid>/approve')
def approve(mid, bid):
    require_editor()
    comment = request.form.get('review_comment', '').strip()
    if len(comment) > 2000:
        abort(400)
    with current_app.store.connection() as c, c.cursor() as cur:
        owned(cur, mid)
        cur.execute('''SELECT id,review_status FROM meeting_briefs
          WHERE id=%s AND meeting_id=%s AND organization_id=%s FOR UPDATE''',
          (str(bid), str(mid), g.user['organization_id']))
        row = cur.fetchone()
        if not row:
            abort(404)
        if row['review_status'] != 'DRAFT':
            abort(409)
        cur.execute('''UPDATE meeting_briefs SET review_status='SUPERSEDED'
          WHERE meeting_id=%s AND organization_id=%s AND review_status='APPROVED' AND id<>%s''',
          (str(mid), g.user['organization_id'], str(bid)))
        cur.execute('''UPDATE meeting_briefs SET review_status='APPROVED',approved_by=%s,
          approved_at=now(),review_comment=%s WHERE id=%s''',
          (g.user['person_id'], comment or None, str(bid)))
        audit(cur, 'MEETING_BRIEF_APPROVED', 'MEETING_BRIEF', bid, {'meeting_id': str(mid)})
    flash('Бриф утверждён. Пункты остаются привязанными к исходным фрагментам записи.')
    return redirect(url_for('brief017.page', mid=mid), 303)


def register(app):
    app.register_blueprint(bp)
