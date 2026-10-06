"""HEAD-DASHBOARD-007. Read-only extension of secretary-web-006."""
from uuid import UUID
from flask import Blueprint, abort, current_app, g, render_template, request

bp = Blueprint('head', __name__)
STATUS = {'PENDING':'Ожидает исполнения','CLAIMED_DONE':'Исполнение заявлено',
          'CONFIRMED_DONE':'Выполнение подтверждено','NOT_DONE':'Не исполнено',
          'BLOCKED':'Заблокировано','UNKNOWN':'Статус неясен'}
LIFECYCLE = {'DRAFT':'Черновик','APPROVED':'Утверждено','ACTIVE':'В работе',
             'CLOSED':'Закрыто','CANCELLED':'Отменено'}
VIEWS = {'all':'Все поручения','overdue':'Просроченные','attention':'Требуют внимания',
         'review':'Ответы на проверке','claimed':'Исполнение заявлено','done':'Выполнение подтверждено'}
# Parameters always bound. Every relation is scoped to the authenticated organisation.
BASE = '''WITH dashboard AS (
 SELECT t.id,t.instruction,t.lifecycle,t.execution_status,t.due_at,t.due_text,
 m.title,m.meeting_at,o.timezone,p.display_name,
 r.id AS response_id,r.received_at,r.classification,r.review_status,r.reviewed_result,
 r.transcript,r.reason_transcript,r.promised_due_text,
 EXISTS (SELECT 1 FROM task_responses q WHERE q.task_id=t.id
         AND q.organization_id=t.organization_id AND q.review_status='PENDING') AS needs_review,
 (t.lifecycle IN ('APPROVED','ACTIVE') AND t.execution_status<>'CONFIRMED_DONE'
  AND t.due_at IS NOT NULL AND t.due_at<now()) AS overdue
 FROM tasks t JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
 JOIN organizations o ON o.id=t.organization_id
 LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
 LEFT JOIN LATERAL (
   SELECT r.* FROM task_responses r
   WHERE r.task_id=t.id AND r.organization_id=t.organization_id
   ORDER BY r.received_at DESC,r.id DESC LIMIT 1
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
CONDITIONS = {'all':'true','overdue':'overdue','attention':'attention','review':'needs_review',
              'claimed':"execution_status='CLAIMED_DONE' AND lifecycle<>'CANCELLED'",
              'done':"execution_status='CONFIRMED_DONE' AND lifecycle<>'CANCELLED'"}

def response_view(row):
    """Never substitute unverified or rejected corrections for original evidence."""
    if not row.get('response_id'):
        return dict(label='Ответа пока нет',text='',reason='',due='',source='',reviewed=False)
    state = row['review_status']
    if state == 'REJECTED':
        return dict(label='Ответ отклонён',text='',reason='',due='',source='Секретарём',reviewed=False)
    reviewed = state in ('CONFIRMED','CORRECTED') and isinstance(row.get('reviewed_result'),dict)
    data = row['reviewed_result'] if reviewed else {}
    classification = data.get('classification') if reviewed else row['classification']
    return dict(label={'YES':'Исполнитель сообщил о выполнении','NO':'Исполнитель сообщил: не исполнено',
                       'UNCLEAR':'Ответ неясен'}.get(classification,'Результат не указан'),
                text=(data.get('corrected_transcript') if reviewed else row['transcript']) or '',
                reason=(data.get('reason') if reviewed else row.get('reason_transcript')) or '',
                due=(data.get('due_text') if reviewed else row.get('promised_due_text')) or '',
                source='Проверено секретарём' if reviewed else 'Распознавание; ответ не проверен секретарём',
                reviewed=reviewed)

def uuid_filter(name):
    value=request.args.get(name,'')
    if not value: return None
    try: return str(UUID(value))
    except (ValueError, AttributeError): abort(400)

def fetch_dashboard(store, org, meeting, person, view, page):
    params=(org,meeting,meeting,person,person)
    with store.connection() as c, c.cursor() as cur:
        # Counts and page share one DB snapshot. No writes in this module.
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cur.execute('SELECT id,title,meeting_at FROM meetings WHERE organization_id=%s ORDER BY meeting_at DESC NULLS LAST,id',(org,))
        meetings=cur.fetchall()
        cur.execute('SELECT id,display_name FROM people WHERE organization_id=%s ORDER BY display_name,id',(org,))
        people=cur.fetchall()
        cur.execute(BASE+'''SELECT count(*) AS total,
          count(*) FILTER (WHERE overdue) AS overdue,
          count(*) FILTER (WHERE attention) AS attention,
          count(*) FILTER (WHERE needs_review) AS review,
          count(*) FILTER (WHERE execution_status='CLAIMED_DONE' AND lifecycle<>'CANCELLED') AS claimed,
          count(*) FILTER (WHERE execution_status='CONFIRMED_DONE' AND lifecycle<>'CANCELLED') AS done
          FROM flagged''', params)
        counts=cur.fetchone()
        cur.execute(BASE+'SELECT * FROM flagged WHERE '+CONDITIONS[view]+
                    ' ORDER BY overdue DESC,attention DESC,due_at ASC NULLS LAST,id LIMIT 26 OFFSET %s',params+(page*25,))
        rows=cur.fetchall()
    for row in rows: row['answer']=response_view(row)
    return dict(meetings=meetings,people=people,counts=counts,rows=rows[:25],more=len(rows)>25)

@bp.get('/head')
def dashboard():
    view=request.args.get('view','all')
    if view not in VIEWS: abort(400)
    try:
        page=int(request.args.get('page','0'))
        if not 0<=page<=10000: raise ValueError()
    except ValueError: abort(400)
    meeting=uuid_filter('meeting'); person=uuid_filter('person')
    data=fetch_dashboard(current_app.store,g.user['organization_id'],meeting,person,view,page)
    return render_template('head007.html',**data,view=view,page=page,meeting=meeting or '',person=person or '',
                           views=VIEWS,statuses=STATUS,lifecycles=LIFECYCLE)

def register_head_dashboard(app):
    app.register_blueprint(bp)
