"""Human-initiated secretary calls. No autodial queue, recording, ASR or TTS."""
import uuid

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from psycopg2 import IntegrityError
from psycopg2.extras import Json
from call_assist_core016 import KINDS, dial_uri, normalize_contact

bp = Blueprint('call_assist', __name__)
OUTCOMES = {
    'CONNECTED':'Соединились', 'NO_ANSWER':'Нет ответа', 'BUSY':'Занято',
    'CALLBACK':'Перезвонить позже', 'REFUSED':'Отказался обсуждать',
    'WRONG_NUMBER':'Неверный номер', 'CANCELLED':'Звонок отменён'
}
REPORTED = {
    '':'Статус поручения не менять', 'CLAIMED_DONE':'Исполнение заявлено',
    'NOT_DONE':'Не исполнено', 'BLOCKED':'Есть препятствие', 'UNKNOWN':'Статус неясен'
}

def secretary():
    if not g.user or g.user.get('app_role') == 'head':
        abort(403)

def administrator():
    if not g.user or not g.user.get('is_admin'):
        abort(403)

def audit(cur, event, entity_type, entity_id, payload=None):
    cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'USER',%s,%s,%s,%s,%s)''',
      (str(uuid.uuid4()), g.user['organization_id'], g.user['person_id'], event,
       entity_type, entity_id, Json(payload or {})))

@bp.get('/admin/contacts')
def contacts():
    administrator()
    return redirect(url_for('user_admin.users'), 302)

@bp.post('/admin/contacts/<uuid:person_id>/add')
def contact_add(person_id):
    administrator()
    kind = request.form.get('kind','')
    label = request.form.get('label','').strip()
    if kind not in KINDS or not 1 <= len(label) <= 80:
        abort(400)
    try:
        normalized = normalize_contact(kind, request.form.get('value',''))
        priority = int(request.form.get('priority','1'))
        if not 1 <= priority <= 99:
            raise ValueError('Приоритет должен быть от 1 до 99.')
    except (ValueError, TypeError) as exc:
        flash(str(exc)); return redirect(url_for('user_admin.users')+'#person-'+str(person_id),303)
    cid = str(uuid.uuid4())
    try:
        with current_app.store.connection() as c, c.cursor() as cur:
            cur.execute('SELECT id FROM people WHERE id=%s AND organization_id=%s FOR UPDATE',
                        (str(person_id),g.user['organization_id']))
            if not cur.fetchone(): abort(404)
            cur.execute('''INSERT INTO contact_points
              (id,organization_id,person_id,kind,label,value,normalized_value,priority,enabled,allow_automated_calls,call_windows)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,false,'[]'::jsonb)''',
              (cid,g.user['organization_id'],str(person_id),kind,label,request.form['value'].strip(),normalized,priority))
            audit(cur,'CONTACT_POINT_CREATED','CONTACT_POINT',cid,{'person_id':str(person_id),'kind':kind})
    except IntegrityError:
        flash('Такой номер или SIP-адрес уже есть в этой организации.')
        return redirect(url_for('user_admin.users')+'#person-'+str(person_id),303)
    flash('Контакт сохранён. Автоматические звонки для него запрещены.')
    return redirect(url_for('user_admin.users')+'#person-'+str(person_id),303)

@bp.post('/admin/contacts/<uuid:contact_id>/state')
def contact_state(contact_id):
    administrator()
    action = request.form.get('action')
    if action not in ('enable','disable'): abort(400)
    with current_app.store.connection() as c, c.cursor() as cur:
        cur.execute('''SELECT cp.id,cp.person_id FROM contact_points cp JOIN people p ON p.id=cp.person_id
          AND p.organization_id=cp.organization_id WHERE cp.id=%s AND cp.organization_id=%s FOR UPDATE OF cp''',
          (str(contact_id),g.user['organization_id']))
        row = cur.fetchone()
        if not row: abort(404)
        cur.execute('UPDATE contact_points SET enabled=%s,allow_automated_calls=false,updated_at=now() WHERE id=%s',
                    (action=='enable',str(contact_id)))
        audit(cur,'CONTACT_POINT_'+action.upper(),'CONTACT_POINT',str(contact_id),{'person_id':str(row['person_id'])})
    flash('Контакт включён.' if action=='enable' else 'Контакт отключён.')
    return redirect(url_for('user_admin.users')+'#person-'+str(row['person_id']),303)

@bp.get('/calls')
def calls():
    secretary()
    with current_app.store.connection() as c, c.cursor() as cur:
        cur.execute('''SELECT t.id,t.instruction,t.lifecycle,t.execution_status,t.due_at,t.due_text,
          m.title,p.id AS person_id,p.display_name,cp.id AS contact_id,cp.kind AS contact_kind,
          cp.label AS contact_label,cp.value AS contact_value,cp.normalized_value,
          last_call.outcome AS last_outcome,last_call.created_at AS last_called_at
          FROM tasks t JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
          LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
          LEFT JOIN LATERAL (SELECT x.* FROM contact_points x WHERE x.organization_id=t.organization_id
            AND x.person_id=t.primary_assignee_id AND x.enabled ORDER BY x.priority,x.created_at LIMIT 1) cp ON true
          LEFT JOIN LATERAL (SELECT s.outcome,s.created_at FROM manual_call_sessions s
            WHERE s.organization_id=t.organization_id AND s.task_id=t.id AND s.status<>'PREPARED'
            ORDER BY s.created_at DESC LIMIT 1) last_call ON true
          WHERE t.organization_id=%s AND t.lifecycle IN ('APPROVED','ACTIVE')
            AND t.execution_status<>'CONFIRMED_DONE'
          ORDER BY t.due_at ASC NULLS LAST,t.created_at,t.id LIMIT 300''',(g.user['organization_id'],))
        rows = cur.fetchall()
    return render_template('calls016.html',rows=rows,outcomes=OUTCOMES)

@bp.post('/calls/<uuid:task_id>/prepare')
def prepare(task_id):
    secretary()
    try: contact_id = str(uuid.UUID(request.form.get('contact_id','')))
    except (ValueError,AttributeError): abort(400)
    sid = str(uuid.uuid4())
    with current_app.store.connection() as c, c.cursor() as cur:
        cur.execute('''SELECT t.id,t.primary_assignee_id FROM tasks t JOIN contact_points cp
          ON cp.person_id=t.primary_assignee_id AND cp.organization_id=t.organization_id
          WHERE t.id=%s AND t.organization_id=%s AND t.lifecycle IN ('APPROVED','ACTIVE')
            AND t.execution_status<>'CONFIRMED_DONE' AND cp.id=%s AND cp.enabled FOR UPDATE OF t''',
          (str(task_id),g.user['organization_id'],contact_id))
        row = cur.fetchone()
        if not row: abort(404)
        cur.execute('''INSERT INTO manual_call_sessions
          (id,organization_id,task_id,person_id,contact_point_id,secretary_id)
          VALUES (%s,%s,%s,%s,%s,%s)''',
          (sid,g.user['organization_id'],str(task_id),str(row['primary_assignee_id']),contact_id,g.user['person_id']))
        audit(cur,'MANUAL_CALL_PREPARED','MANUAL_CALL',sid,{'task_id':str(task_id),'contact_point_id':contact_id})
    return redirect(url_for('call_assist.ready',session_id=sid),303)

def session_row(cur, session_id, lock=False):
    cur.execute('''SELECT s.*,t.instruction,t.execution_status,t.due_text,m.title,p.display_name,
      cp.kind AS contact_kind,cp.label AS contact_label,cp.value AS contact_value,cp.normalized_value
      FROM manual_call_sessions s JOIN tasks t ON t.id=s.task_id AND t.organization_id=s.organization_id
      JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
      JOIN people p ON p.id=s.person_id AND p.organization_id=s.organization_id
      JOIN contact_points cp ON cp.id=s.contact_point_id AND cp.organization_id=s.organization_id
      WHERE s.id=%s AND s.organization_id=%s''' + (' FOR UPDATE OF s,t' if lock else ''),
      (str(session_id),g.user['organization_id']))
    return cur.fetchone()

@bp.get('/calls/session/<uuid:session_id>')
def ready(session_id):
    secretary()
    with current_app.store.connection() as c, c.cursor() as cur:
        row = session_row(cur,session_id)
    if not row: abort(404)
    return render_template('call_ready016.html',row=row,dial_href=dial_uri(row['contact_kind'],row['normalized_value']),
                           outcomes=OUTCOMES,reported=REPORTED)

@bp.post('/calls/session/<uuid:session_id>/finish')
def finish(session_id):
    secretary()
    outcome = request.form.get('outcome','')
    reported = request.form.get('reported_status','')
    note = request.form.get('note','').strip()
    due = request.form.get('promised_due_text','').strip()
    if outcome not in OUTCOMES or reported not in REPORTED or len(note)>2000 or len(due)>200:
        abort(400)
    if outcome != 'CONNECTED' and reported:
        flash('Статус поручения можно изменить только после состоявшегося разговора.')
        return redirect(url_for('call_assist.ready',session_id=session_id),303)
    with current_app.store.connection() as c, c.cursor() as cur:
        row = session_row(cur,session_id,True)
        if not row: abort(404)
        if row['status'] != 'PREPARED':
            flash('Результат этого звонка уже сохранён.')
            return redirect(url_for('call_assist.calls'),303)
        status = 'CANCELLED' if outcome=='CANCELLED' else 'COMPLETED'
        cur.execute('''UPDATE manual_call_sessions SET status=%s,outcome=%s,reported_status=%s,
          note=%s,promised_due_text=%s,finished_at=now() WHERE id=%s''',
          (status,outcome,reported or None,note or None,due or None,str(session_id)))
        if reported:
            cur.execute('UPDATE tasks SET execution_status=%s,updated_at=now() WHERE id=%s AND organization_id=%s',
                        (reported,str(row['task_id']),g.user['organization_id']))
        audit(cur,'MANUAL_CALL_FINISHED','MANUAL_CALL',str(session_id),
              {'task_id':str(row['task_id']),'outcome':outcome,'reported_status':reported or None})
    flash('Результат разговора сохранён. Аудиозапись не создавалась.')
    return redirect(url_for('call_assist.calls'),303)

def register(app):
    app.register_blueprint(bp)
