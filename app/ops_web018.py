"""Administrator-only, organization-scoped control of background operations."""
import os
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from psycopg2.extras import Json

from import_common import ROOT, directory


bp = Blueprint('ops018', __name__)

KINDS = {
    'IMPORT': ('meeting_imports', "status IN ('UPLOADING','QUEUED','FETCHING','ASSEMBLING','CONVERTING','TRANSCRIBING')"),
    'EXTRACTION': ('meeting_llm_jobs', "status IN ('QUEUED','RUNNING')"),
    'BRIEF': ('meeting_brief_jobs', "status IN ('QUEUED','RUNNING')"),
}


def require_admin():
    if not g.user or not g.user.get('is_admin'):
        abort(403)


def audit(cur, event, entity_type, entity_id, payload=None):
    cur.execute('''INSERT INTO audit_events
      (id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'USER',%s,%s,%s,%s,%s)''',
      (str(uuid.uuid4()), g.user['organization_id'], g.user['person_id'], event,
       entity_type, str(entity_id), Json(payload or {})))


def _control(cur, kind, entity_id, desired, reason=None, actual='PENDING'):
    cur.execute('''INSERT INTO operation_controls
      (kind,entity_id,organization_id,desired_state,actual_state,reason,requested_by)
      VALUES (%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(kind,entity_id) DO UPDATE SET organization_id=EXCLUDED.organization_id,
        desired_state=EXCLUDED.desired_state,actual_state=EXCLUDED.actual_state,
        reason=EXCLUDED.reason,requested_by=EXCLUDED.requested_by,
        requested_at=now(),applied_at=NULL,updated_at=now(),last_error=NULL''',
      (kind, str(entity_id), g.user['organization_id'], desired, actual, reason,
       g.user['person_id']))


def _rows(cur):
    organization_id = g.user['organization_id']
    cur.execute('''SELECT i.id,'IMPORT' AS kind,i.title AS label,i.status,
      i.meeting_id,i.processed_seconds,i.duration_seconds,NULL::integer AS next_chunk,NULL::integer AS total_chunks,
      i.error_code,i.created_at,i.updated_at,c.desired_state,c.actual_state,c.reason
      FROM meeting_imports i LEFT JOIN operation_controls c
        ON c.kind='IMPORT' AND c.entity_id=i.id AND c.organization_id=i.organization_id
      WHERE i.organization_id=%s ORDER BY i.created_at DESC LIMIT 60''', (organization_id,))
    rows = list(cur.fetchall())
    for kind, table, title in (
        ('EXTRACTION', 'meeting_llm_jobs', 'Выделение поручений'),
        ('BRIEF', 'meeting_brief_jobs', 'Бриф совещания'),
    ):
        cur.execute(f'''SELECT j.id,%s AS kind,m.title || ' — ' || %s AS label,j.status,
          NULL::double precision AS processed_seconds,NULL::double precision AS duration_seconds,
          j.next_chunk,j.total_chunks,j.error_code,j.created_at,j.updated_at,
          c.desired_state,c.actual_state,c.reason
          FROM {table} j JOIN meetings m ON m.id=j.meeting_id AND m.organization_id=j.organization_id
          LEFT JOIN operation_controls c ON c.kind=%s AND c.entity_id=j.id
            AND c.organization_id=j.organization_id
          WHERE j.organization_id=%s ORDER BY j.created_at DESC LIMIT 60''',
          (kind, title, kind, organization_id))
        rows.extend(cur.fetchall())
    rows.sort(key=lambda x: (x['updated_at'], str(x['id'])), reverse=True)
    return rows[:100]


@bp.get('/admin/operations')
def page():
    require_admin()
    with current_app.store.connection() as connection, connection.cursor() as cur:
        cur.execute('''SELECT paused,reason,updated_at FROM organization_operation_state
          WHERE organization_id=%s''', (g.user['organization_id'],))
        state = cur.fetchone() or {'paused': False, 'reason': None, 'updated_at': None}
        rows = _rows(cur)
    return render_template('operations018.html', state=state, rows=rows)


@bp.post('/admin/operations/maintenance')
def maintenance():
    require_admin()
    action = request.form.get('action')
    reason = request.form.get('reason', '').strip()[:500] or None
    if action not in ('pause', 'resume', 'cancel'):
        abort(400)
    paused = action != 'resume'
    with current_app.store.connection() as connection, connection.cursor() as cur:
        cur.execute('''INSERT INTO organization_operation_state
          (organization_id,paused,reason,updated_by) VALUES (%s,%s,%s,%s)
          ON CONFLICT(organization_id) DO UPDATE SET paused=EXCLUDED.paused,reason=EXCLUDED.reason,
          updated_by=EXCLUDED.updated_by,updated_at=now()''',
          (g.user['organization_id'], paused, reason, g.user['person_id']))
        affected = 0
        if action == 'cancel':
            for kind, (table, active) in KINDS.items():
                cur.execute(f'''SELECT id,status FROM {table} WHERE organization_id=%s AND {active}
                  FOR UPDATE''', (g.user['organization_id'],))
                for row in cur.fetchall():
                    immediate = row['status'] in ('UPLOADING', 'QUEUED')
                    _control(cur, kind, row['id'], 'CANCELLED', reason,
                             'CANCELLED' if immediate else 'PENDING')
                    if immediate:
                        cur.execute(f"UPDATE {table} SET status='CANCELLED',error_code='CANCELLED_BY_ADMIN',updated_at=now() WHERE id=%s",
                                    (str(row['id']),))
                    affected += 1
        event = {'pause': 'OPERATIONS_PAUSED', 'resume': 'OPERATIONS_RESUMED',
                 'cancel': 'OPERATIONS_CANCELLED'}[action]
        audit(cur, event, 'ORGANIZATION', g.user['organization_id'],
              {'reason': reason, 'affected': affected})
    if action == 'cancel':
        flash('Команда остановки передана всем операциям. Исходные данные сохранены; организация оставлена на паузе.')
    elif paused:
        flash('Новые этапы фоновой обработки приостановлены.')
    else:
        flash('Фоновая обработка организации продолжена.')
    return redirect(url_for('ops018.page'), 303)


def _targets(cur, kind, entity_id):
    table, _ = KINDS[kind]
    cur.execute(f'SELECT * FROM {table} WHERE id=%s AND organization_id=%s FOR UPDATE',
                (str(entity_id), g.user['organization_id']))
    row = cur.fetchone()
    if not row:
        abort(404)
    if kind == 'IMPORT' and row.get('group_id'):
        cur.execute('''SELECT * FROM meeting_imports WHERE group_id=%s AND organization_id=%s
          ORDER BY group_position FOR UPDATE''', (str(row['group_id']), g.user['organization_id']))
        return list(cur.fetchall())
    return [row]


@bp.post('/admin/operations/<kind>/<uuid:entity_id>/command')
def command(kind, entity_id):
    require_admin()
    kind = kind.upper()
    if kind not in KINDS:
        abort(404)
    action = request.form.get('action')
    reason = request.form.get('reason', '').strip()[:500] or None
    if action not in ('pause', 'resume', 'cancel', 'retry'):
        abort(400)
    with current_app.store.connection() as connection, connection.cursor() as cur:
        targets = _targets(cur, kind, entity_id)
        if action == 'retry':
            if len(targets) != 1 or targets[0]['status'] not in ('FAILED', 'CANCELLED'):
                abort(409)
            table = KINDS[kind][0]
            if kind == 'IMPORT':
                cur.execute("UPDATE meeting_imports SET status='QUEUED',attempts=0,error_code=NULL,updated_at=now() WHERE id=%s",
                            (str(entity_id),))
            else:
                cur.execute(f"UPDATE {table} SET status='QUEUED',error_code=NULL,updated_at=now() WHERE id=%s",
                            (str(entity_id),))
            _control(cur, kind, entity_id, 'RUNNING')
        else:
            desired = {'pause': 'PAUSED', 'resume': 'RUNNING', 'cancel': 'CANCELLED'}[action]
            for row in targets:
                if row['status'] in ('REVIEW', 'DUPLICATE', 'DONE'):
                    abort(409)
                immediate = action == 'cancel' and row['status'] in ('UPLOADING', 'QUEUED', 'FAILED', 'CANCELLED')
                _control(cur, kind, row['id'], desired, reason,
                         'CANCELLED' if immediate else 'PENDING')
                if immediate:
                    cur.execute(f"UPDATE {KINDS[kind][0]} SET status='CANCELLED',error_code='CANCELLED_BY_ADMIN',updated_at=now() WHERE id=%s",
                                (str(row['id']),))
        audit(cur, 'OPERATION_' + action.upper(), kind, entity_id,
              {'reason': reason, 'affected': len(targets)})
    flash({'pause': 'Команда паузы принята.', 'resume': 'Операция продолжена.',
           'cancel': 'Команда остановки принята. Исходные данные сохранены.',
           'retry': 'Операция возвращена в очередь.'}[action])
    return redirect(url_for('ops018.page'), 303)


@bp.post('/admin/operations/import/<uuid:entity_id>/discard')
def discard(entity_id):
    require_admin()
    moved = []
    trash = Path(ROOT) / '.trash018' / uuid.uuid4().hex
    try:
        with current_app.store.connection() as connection, connection.cursor() as cur:
            targets = _targets(cur, 'IMPORT', entity_id)
            if any(row['status'] not in ('UPLOADING', 'FAILED', 'CANCELLED') or row.get('transcript_id')
                   for row in targets):
                abort(409)
            group_id = targets[0].get('group_id')
            trash.mkdir(mode=0o750, parents=True, exist_ok=False)
            for row in targets:
                source = directory(ROOT, str(row['id']))
                if source.exists():
                    destination = trash / str(row['id'])
                    os.replace(source, destination)
                    moved.append((source, destination))
            ids = [str(row['id']) for row in targets]
            meeting_ids = [str(row['meeting_id']) for row in targets if row.get('meeting_id')]
            if group_id:
                cur.execute('UPDATE meeting_import_groups SET primary_import_id=NULL WHERE id=%s AND organization_id=%s',
                            (str(group_id), g.user['organization_id']))
            cur.execute('DELETE FROM meeting_import_metrics WHERE import_id=ANY(%s::uuid[])', (ids,))
            cur.execute("DELETE FROM operation_controls WHERE kind='IMPORT' AND entity_id=ANY(%s::uuid[]) AND organization_id=%s",
                        (ids, g.user['organization_id']))
            cur.execute('DELETE FROM meeting_imports WHERE id=ANY(%s::uuid[]) AND organization_id=%s',
                        (ids, g.user['organization_id']))
            if group_id:
                cur.execute('DELETE FROM meeting_import_groups WHERE id=%s AND organization_id=%s',
                            (str(group_id), g.user['organization_id']))
            if meeting_ids:
                cur.execute('''DELETE FROM meetings m WHERE m.id=ANY(%s::uuid[]) AND m.organization_id=%s
                  AND NOT EXISTS(SELECT 1 FROM transcripts t WHERE t.meeting_id=m.id)
                  AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.meeting_id=m.id)
                  AND NOT EXISTS(SELECT 1 FROM meeting_imports i WHERE i.meeting_id=m.id)''',
                  (meeting_ids, g.user['organization_id']))
            audit(cur, 'INCOMPLETE_IMPORT_DISCARDED', 'MEETING_IMPORT', entity_id,
                  {'affected': len(ids)})
    except Exception:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                os.replace(destination, source)
        if trash.exists():
            shutil.rmtree(trash, ignore_errors=True)
        raise
    shutil.rmtree(trash, ignore_errors=True)
    flash('Незавершённая загрузка и её временные файлы удалены.')
    return redirect(url_for('ops018.page'), 303)


def register(app):
    app.register_blueprint(bp)
