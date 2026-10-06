"""Consolidated read-only views for automatic and secretary-entered results."""
from types import MethodType

from flask import Blueprint, abort, current_app, g, render_template


bp = Blueprint('response_integration0181', __name__)

OUTCOMES = {
    'CONNECTED': 'Соединились',
    'NO_ANSWER': 'Нет ответа',
    'BUSY': 'Занято',
    'CALLBACK': 'Перезвонить позже',
    'REFUSED': 'Отказался обсуждать',
    'WRONG_NUMBER': 'Неверный номер',
    'CANCELLED': 'Звонок отменён',
}
REPORTED = {
    'CLAIMED_DONE': 'Исполнение заявлено',
    'NOT_DONE': 'Не исполнено',
    'BLOCKED': 'Есть препятствие',
    'UNKNOWN': 'Статус неясен',
}

HEAD_BASE = '''WITH dashboard AS (
 SELECT t.id,t.instruction,t.lifecycle,t.execution_status,t.due_at,t.due_text,
 m.title,m.meeting_at,o.timezone,p.display_name,
 r.id AS response_id,r.received_at,r.classification,r.review_status,r.reviewed_result,
 r.transcript,r.reason_transcript,r.promised_due_text,r.source AS response_source,
 EXISTS (SELECT 1 FROM task_responses q WHERE q.task_id=t.id
         AND q.organization_id=t.organization_id AND q.review_status='PENDING') AS needs_review,
 (t.lifecycle IN ('APPROVED','ACTIVE') AND t.execution_status<>'CONFIRMED_DONE'
  AND t.due_at IS NOT NULL AND t.due_at<now()) AS overdue
 FROM tasks t JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
 JOIN organizations o ON o.id=t.organization_id
 LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
 LEFT JOIN LATERAL (
   SELECT x.* FROM (
     SELECT a.id,a.received_at,a.classification,a.review_status,a.reviewed_result,
       a.transcript,a.reason_transcript,a.promised_due_text,'AUTO'::text AS source
     FROM task_responses a WHERE a.task_id=t.id AND a.organization_id=t.organization_id
     UNION ALL
     SELECT s.id,COALESCE(s.finished_at,s.created_at),
       CASE s.reported_status WHEN 'CLAIMED_DONE' THEN 'YES'
         WHEN 'NOT_DONE' THEN 'NO' WHEN 'BLOCKED' THEN 'NO' ELSE 'UNCLEAR' END,
       'NOT_REQUIRED'::text,NULL::jsonb,s.note,NULL,s.promised_due_text,'MANUAL'::text
     FROM manual_call_sessions s WHERE s.task_id=t.id AND s.organization_id=t.organization_id
       AND s.status<>'PREPARED'
   ) x ORDER BY x.received_at DESC,x.id DESC LIMIT 1
 ) r ON true
 WHERE t.organization_id=%s
 AND (%s::uuid IS NULL OR t.meeting_id=%s::uuid)
 AND (%s::uuid IS NULL OR t.primary_assignee_id=%s::uuid)
), flagged AS (
 SELECT *, (lifecycle IN ('APPROVED','ACTIVE') AND execution_status<>'CONFIRMED_DONE'
 AND (overdue OR needs_review OR execution_status IN ('NOT_DONE','BLOCKED','UNKNOWN')
      OR due_at IS NULL OR review_status='REJECTED'
      OR CASE WHEN review_status IN ('CONFIRMED','CORRECTED')
         THEN reviewed_result->>'classification' ELSE classification END IN ('NO','UNCLEAR'))
 ) AS attention FROM dashboard
) '''


def head_response_view(row):
    if not row.get('response_id'):
        return dict(label='Ответа пока нет', text='', reason='', due='', source='', reviewed=False)
    if row.get('response_source') == 'MANUAL':
        classification = row.get('classification')
        return dict(
            label={'YES': 'Исполнитель сообщил о выполнении', 'NO': 'Исполнитель сообщил: не исполнено',
                   'UNCLEAR': 'Результат звонка сохранён'}.get(classification, 'Результат не указан'),
            text=row.get('transcript') or '', reason='', due=row.get('promised_due_text') or '',
            source='Ручной звонок секретаря', reviewed=True)
    state = row['review_status']
    if state == 'REJECTED':
        return dict(label='Ответ отклонён', text='', reason='', due='', source='Секретарём', reviewed=False)
    reviewed = state in ('CONFIRMED', 'CORRECTED') and isinstance(row.get('reviewed_result'), dict)
    data = row['reviewed_result'] if reviewed else {}
    classification = data.get('classification') if reviewed else row['classification']
    return dict(label={'YES': 'Исполнитель сообщил о выполнении', 'NO': 'Исполнитель сообщил: не исполнено',
                       'UNCLEAR': 'Ответ неясен'}.get(classification, 'Результат не указан'),
                text=(data.get('corrected_transcript') if reviewed else row['transcript']) or '',
                reason=(data.get('reason') if reviewed else row.get('reason_transcript')) or '',
                due=(data.get('due_text') if reviewed else row.get('promised_due_text')) or '',
                source='Проверено секретарём' if reviewed else 'Распознавание; ответ не проверен секретарём',
                reviewed=reviewed)


def _secretary_or_admin():
    if not g.user:
        abort(403)


def consolidated_queue(store, org, mode, page):
    """Return automatic answers and completed manual calls in one queue."""
    condition = {
        'pending': "source='AUTO' AND review_status='PENDING'",
        'reviewed': "review_status<>'PENDING'",
        'all': 'true',
    }[mode]
    common = '''WITH results AS (
      SELECT r.id,r.received_at,r.classification,r.review_status,r.reviewed_result,
        left(r.transcript,220) AS excerpt,p.display_name,t.instruction,m.title,'AUTO'::text AS source
      FROM task_responses r
      JOIN people p ON p.id=r.person_id AND p.organization_id=r.organization_id
      JOIN tasks t ON t.id=r.task_id AND t.organization_id=r.organization_id
      JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=r.organization_id
      WHERE r.organization_id=%s
      UNION ALL
      SELECT s.id,COALESCE(s.finished_at,s.created_at) AS received_at,
        CASE s.reported_status WHEN 'CLAIMED_DONE' THEN 'YES'
          WHEN 'NOT_DONE' THEN 'NO' WHEN 'BLOCKED' THEN 'NO' ELSE 'UNCLEAR' END AS classification,
        'NOT_REQUIRED'::text AS review_status,NULL::jsonb AS reviewed_result,
        left(COALESCE(NULLIF(s.note,''),CASE s.outcome
          WHEN 'CONNECTED' THEN 'Результат разговора сохранён без комментария.'
          WHEN 'NO_ANSWER' THEN 'Исполнитель не ответил.' WHEN 'BUSY' THEN 'Линия занята.'
          WHEN 'CALLBACK' THEN 'Исполнитель попросил перезвонить позже.'
          WHEN 'REFUSED' THEN 'Исполнитель отказался обсуждать поручение.'
          WHEN 'WRONG_NUMBER' THEN 'Указан неверный номер.' ELSE 'Звонок отменён.' END),220) AS excerpt,
        p.display_name,t.instruction,m.title,'MANUAL'::text AS source
      FROM manual_call_sessions s
      JOIN people p ON p.id=s.person_id AND p.organization_id=s.organization_id
      JOIN tasks t ON t.id=s.task_id AND t.organization_id=s.organization_id
      JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=s.organization_id
      WHERE s.organization_id=%s AND s.status<>'PREPARED'
    ) '''
    with store.connection() as c, c.cursor() as cur:
        cur.execute(common + '''SELECT
          count(*) FILTER (WHERE source='AUTO' AND review_status='PENDING') AS pending,
          count(*) FILTER (WHERE review_status<>'PENDING') AS reviewed,
          count(*) AS total FROM results''', (org, org))
        counts = cur.fetchone()
        cur.execute(common + 'SELECT * FROM results WHERE ' + condition +
                    ' ORDER BY received_at DESC,id LIMIT 26 OFFSET %s', (org, org, page * 25))
        rows = cur.fetchall()
    return counts, rows[:25], len(rows) > 25


@bp.get('/manual-responses/<uuid:session_id>')
def manual_detail(session_id):
    _secretary_or_admin()
    with current_app.store.connection() as c, c.cursor() as cur:
        cur.execute('''SELECT s.*,t.instruction,t.execution_status,t.lifecycle,t.due_at,t.due_text,
          m.title,m.meeting_at,p.display_name,secretary.display_name AS secretary_name,
          cp.kind AS contact_kind,cp.label AS contact_label,cp.value AS contact_value,
          o.timezone AS org_timezone
          FROM manual_call_sessions s
          JOIN tasks t ON t.id=s.task_id AND t.organization_id=s.organization_id
          JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=s.organization_id
          JOIN people p ON p.id=s.person_id AND p.organization_id=s.organization_id
          JOIN people secretary ON secretary.id=s.secretary_id AND secretary.organization_id=s.organization_id
          JOIN contact_points cp ON cp.id=s.contact_point_id AND cp.organization_id=s.organization_id
          JOIN organizations o ON o.id=s.organization_id
          WHERE s.id=%s AND s.organization_id=%s AND s.status<>'PREPARED' ''',
                    (str(session_id), g.user['organization_id']))
        row = cur.fetchone()
    if not row:
        abort(404)
    return render_template('manual_response0181.html', row=row, outcomes=OUTCOMES, reported=REPORTED)


@bp.get('/tasks/<uuid:task_id>')
def task_detail(task_id):
    with current_app.store.connection() as c, c.cursor() as cur:
        cur.execute('''SELECT t.*,m.title,m.meeting_at,p.display_name,o.timezone AS org_timezone
          FROM tasks t
          JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
          JOIN organizations o ON o.id=t.organization_id
          LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
          WHERE t.id=%s AND t.organization_id=%s''', (str(task_id), g.user['organization_id']))
        task = cur.fetchone()
        if not task:
            abort(404)
        cur.execute('''SELECT r.id,r.received_at,r.classification,r.review_status,r.transcript,
          r.reason_transcript,r.promised_due_text,r.reviewed_result,'AUTO'::text AS source
          FROM task_responses r WHERE r.task_id=%s AND r.organization_id=%s
          UNION ALL
          SELECT s.id,COALESCE(s.finished_at,s.created_at),
          CASE s.reported_status WHEN 'CLAIMED_DONE' THEN 'YES'
            WHEN 'NOT_DONE' THEN 'NO' WHEN 'BLOCKED' THEN 'NO' ELSE 'UNCLEAR' END,
          'NOT_REQUIRED',s.note,NULL,s.promised_due_text,
          jsonb_build_object('outcome',s.outcome,'reported_status',s.reported_status,
            'secretary_id',s.secretary_id),'MANUAL'::text
          FROM manual_call_sessions s
          WHERE s.task_id=%s AND s.organization_id=%s AND s.status<>'PREPARED'
          ORDER BY received_at DESC,id''',
                    (str(task_id), g.user['organization_id'], str(task_id), g.user['organization_id']))
        responses = cur.fetchall()
        cur.execute('''SELECT event_type,created_at,payload FROM audit_events
          WHERE organization_id=%s AND ((entity_type='TASK' AND entity_id=%s)
            OR (event_type LIKE 'MANUAL_CALL_%%' AND payload->>'task_id'=%s))
          ORDER BY created_at DESC,id LIMIT 100''',
                    (g.user['organization_id'], str(task_id), str(task_id)))
        history = cur.fetchall()
    return render_template('task_detail0181.html', task=task, responses=responses, history=history,
                           outcomes=OUTCOMES, reported=REPORTED)


def register(app):
    import head_dashboard

    head_dashboard.BASE = HEAD_BASE
    head_dashboard.response_view = head_response_view
    app.store.queue = MethodType(
        lambda store, org, mode, page: consolidated_queue(store, org, mode, page), app.store
    )
    app.register_blueprint(bp)
