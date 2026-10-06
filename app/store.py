import json
import uuid
from contextlib import contextmanager
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from domain import Conflict, Invalid

def plain(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))

class Store:
    def __init__(self, settings): self.settings = settings

    @contextmanager
    def connection(self):
        c = psycopg2.connect(**self.settings['database'], connect_timeout=5,
            application_name='shtab-secretary-006', cursor_factory=RealDictCursor,
            options='-c statement_timeout=10000 -c lock_timeout=5000')
        try:
            with c: yield c
        finally: c.close()

    def user(self, username=None, user_id=None):
        with self.connection() as c, c.cursor() as cur:
            cur.execute('''SELECT u.*,p.organization_id,p.display_name,p.timezone
                FROM secretary_users u JOIN people p ON p.id=u.person_id
                WHERE u.active AND p.active AND
                ((%s IS NOT NULL AND u.username=%s) OR (%s IS NOT NULL AND u.id=%s::uuid))''',
                (username, username, user_id, user_id))
            return cur.fetchone()

    def queue(self, org, mode, page):
        condition = {'pending':"r.review_status='PENDING'", 'reviewed':"r.review_status<>'PENDING'", 'all':'true'}[mode]
        with self.connection() as c, c.cursor() as cur:
            cur.execute('''SELECT count(*) FILTER (WHERE review_status='PENDING') AS pending,
                count(*) FILTER (WHERE review_status<>'PENDING') AS reviewed,
                count(*) AS total FROM task_responses WHERE organization_id=%s''', (org,))
            counts = cur.fetchone()
            cur.execute('''SELECT r.id,r.received_at,r.classification,r.review_status,r.reviewed_result,
                left(r.transcript,220) AS excerpt,p.display_name,t.instruction,m.title
                FROM task_responses r JOIN people p ON p.id=r.person_id
                JOIN tasks t ON t.id=r.task_id JOIN meetings m ON m.id=t.meeting_id
                WHERE r.organization_id=%s AND p.organization_id=r.organization_id
                AND t.organization_id=r.organization_id AND m.organization_id=r.organization_id
                AND ''' + condition + ' ORDER BY r.received_at DESC,r.id LIMIT 26 OFFSET %s',
                (org, page*25))
            rows = cur.fetchall()
            return counts, rows[:25], len(rows)>25

    def detail(self, response_id, org):
        with self.connection() as c, c.cursor() as cur:
            cur.execute('''SELECT r.*,p.display_name,t.instruction,t.execution_status,t.lifecycle,
                t.updated_at AS task_updated_at,m.title,m.meeting_at,o.timezone AS org_timezone
                FROM task_responses r JOIN people p ON p.id=r.person_id
                JOIN tasks t ON t.id=r.task_id JOIN meetings m ON m.id=t.meeting_id
                JOIN organizations o ON o.id=r.organization_id
                WHERE r.id=%s AND r.organization_id=%s
                AND p.organization_id=r.organization_id AND t.organization_id=r.organization_id
                AND m.organization_id=r.organization_id''', (response_id, org))
            return cur.fetchone()

    def history(self, response_id, org):
        with self.connection() as c, c.cursor() as cur:
            cur.execute('''SELECT v.*,p.display_name FROM reviews v
                JOIN people p ON p.id=v.reviewer_id
                WHERE v.entity_type='TASK_RESPONSE' AND v.entity_id=%s AND v.organization_id=%s
                ORDER BY v.created_at DESC,v.id LIMIT 50''', (response_id, org))
            return cur.fetchall()

    def review(self, response_id, actor, decision, expected_version, expected_task_time):
        org = actor['organization_id']
        with self.connection() as c, c.cursor() as cur:
            # Lock task before response; serialises decisions from different response cards.
            cur.execute('SELECT task_id FROM task_responses WHERE id=%s AND organization_id=%s', (response_id,org))
            link = cur.fetchone()
            if not link: raise Invalid('Ответ не найден.')
            cur.execute('SELECT * FROM tasks WHERE id=%s AND organization_id=%s FOR UPDATE', (link['task_id'],org))
            task = cur.fetchone()
            if not task: raise Invalid('Поручение не найдено.')
            cur.execute('SELECT * FROM task_responses WHERE id=%s AND organization_id=%s FOR UPDATE', (response_id,org))
            row = cur.fetchone()
            if row['task_id'] != link['task_id'] or row['review_version'] != expected_version:
                raise Conflict('Ответ уже изменён. Обновите карточку и проверьте историю.')
            if str(task['updated_at']) != expected_task_time:
                raise Conflict('Поручение изменено после открытия карточки. Обновите страницу.')
            change = decision['task_status'] != 'KEEP'
            if change and task['lifecycle'] in ('CLOSED','CANCELLED'):
                raise Invalid('Закрытое или отменённое поручение менять здесь нельзя.')
            if change and task['execution_status'] == 'CONFIRMED_DONE':
                raise Invalid('Исполнение уже подтверждено. Пересмотр поручения требует отдельного действия.')
            if change:
                cur.execute('''SELECT 1 FROM task_responses WHERE task_id=%s AND organization_id=%s
                    AND received_at>%s AND review_status<>'REJECTED' LIMIT 1''',
                    (task['id'],org,row['received_at']))
                if cur.fetchone(): raise Conflict('Есть более новый ответ. Проверьте его перед изменением поручения.')
            action = decision['action']
            previous = row['reviewed_result'] or dict(classification=row['classification'],
                corrected_transcript=row['transcript'], reason=row['reason_transcript'] or '',
                due_text=row['promised_due_text'] or '', due_at=row['promised_due_at'].isoformat() if row['promised_due_at'] else None)
            if action == 'CONFIRM' and any(decision[k] != previous.get(k) for k in
                    ('classification','corrected_transcript','reason','due_text','due_at')):
                action = 'CORRECT'
            status = {'CONFIRM':'CONFIRMED','CORRECT':'CORRECTED','REJECT':'REJECTED'}[action]
            before = dict(review_status=row['review_status'], reviewed_result=row['reviewed_result'],
                review_version=row['review_version'], task_execution_status=task['execution_status'],
                original=dict(classification=row['classification'],transcript=row['transcript'],
                    reason=row['reason_transcript'],due_text=row['promised_due_text']))
            decision = dict(decision, action=action)
            after = dict(review_status=status, reviewed_result=decision, review_version=expected_version+1,
                         task_execution_status=decision['task_status'] if change else task['execution_status'])
            cur.execute('''UPDATE task_responses SET reviewed_result=%s,review_status=%s,
                reviewed_by=%s,reviewed_at=now(),review_version=review_version+1 WHERE id=%s''',
                (Json(decision),status,actor['person_id'],response_id))
            if change:
                cur.execute('UPDATE tasks SET execution_status=%s,updated_at=now() WHERE id=%s',
                            (decision['task_status'],task['id']))
            cur.execute('''INSERT INTO reviews(id,organization_id,entity_type,entity_id,reviewer_id,
                action,before_data,after_data,comment) VALUES (%s,%s,'TASK_RESPONSE',%s,%s,%s,%s,%s,%s)''',
                (str(uuid.uuid4()),org,response_id,actor['person_id'],action,Json(plain(before)),Json(after),decision['comment']))
            cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,
                entity_type,entity_id,correlation_id,payload)
                VALUES (%s,%s,'USER',%s,'RESPONSE_REVIEWED','TASK_RESPONSE',%s,%s,%s)''',
                (str(uuid.uuid4()),org,actor['person_id'],response_id,row['call_job_id'],
                 Json(dict(username=actor['username'],web_user_id=str(actor['id']),before=plain(before),after=after))))
