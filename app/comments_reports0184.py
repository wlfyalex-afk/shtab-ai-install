"""Task comments and downloadable meeting/period reports."""
import io
import uuid
import zipfile
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, current_app, g, redirect, render_template, request, url_for
from psycopg2.extras import Json

from reporting0184 import pdf_bytes, transcript_text, xlsx_bytes


# Keep the report/comment module compatible with every deployed 018.1 patch.
# Some early installations contain the views but do not export these UI maps.
OUTCOMES = {
    "CONNECTED": "Соединились", "NO_ANSWER": "Нет ответа", "BUSY": "Занято",
    "CALLBACK": "Перезвонить позже", "REFUSED": "Отказался обсуждать",
    "WRONG_NUMBER": "Неверный номер", "CANCELLED": "Звонок отменён",
}
REPORTED = {
    "CLAIMED_DONE": "Исполнение заявлено", "NOT_DONE": "Не исполнено",
    "BLOCKED": "Есть препятствие", "UNKNOWN": "Статус неясен",
}
TASK_STATUSES = {
    "PENDING": "Ожидает исполнения", "CLAIMED_DONE": "Исполнение заявлено",
    "CONFIRMED_DONE": "Выполнение подтверждено", "NOT_DONE": "Не исполнено",
    "BLOCKED": "Есть препятствие", "UNKNOWN": "Статус неясен",
}
LIFECYCLES = {
    "DRAFT": "Черновик", "APPROVED": "Утверждено", "ACTIVE": "В работе",
    "CLOSED": "Закрыто", "CANCELLED": "Отменено",
}
REVIEW_STATUSES = {
    "PENDING": "Ожидает проверки", "CONFIRMED": "Подтверждено секретарём",
    "CORRECTED": "Исправлено секретарём", "REJECTED": "Отклонено",
    "NOT_REQUIRED": "Проверка не требуется",
}
EVENTS = {
    "MANUAL_CALL_PREPARED": "Подготовлен ручной звонок",
    "MANUAL_CALL_FINISHED": "Сохранён результат ручного звонка",
    "RESPONSE_REVIEWED": "Ответ проверен секретарём",
    "TASK_CREATED": "Поручение создано", "TASK_UPDATED": "Поручение изменено",
    "TASK_APPROVED": "Поручение утверждено",
}


bp = Blueprint("reports0184", __name__)


def _actor_role():
    if not g.user:
        abort(403)
    if g.user.get("is_admin"):
        return "admin"
    role = g.user.get("app_role", "secretary")
    if role not in ("head", "secretary"):
        abort(403)
    return role


def _meeting_ids(cur, organization_id, meeting_id=None, start=None, end=None):
    sql = "SELECT id FROM meetings WHERE organization_id=%s"
    params = [organization_id]
    if meeting_id:
        sql += " AND id=%s"
        params.append(str(meeting_id))
    if start:
        sql += " AND meeting_at>=%s"
        params.append(start)
    if end:
        sql += " AND meeting_at<%s"
        params.append(end)
    sql += " ORDER BY meeting_at,id"
    cur.execute(sql, params)
    return [str(row["id"]) for row in cur.fetchall()]


def collect_report(cur, organization_id, timezone_name, meeting_id=None, start=None, end=None, title=None, subtitle=None):
    ids = _meeting_ids(cur, organization_id, meeting_id, start, end)
    generated = datetime.now(ZoneInfo(timezone_name))
    if not ids:
        return {"title": title or "Отчёт Штаб.AI", "subtitle": subtitle or "Совещаний нет", "timezone": timezone_name,
                "generated_at": generated, "meetings": [], "tasks": [], "responses": [], "comments": []}
    cur.execute(
        """SELECT m.*,t.content AS transcript_content,
                  COALESCE(jsonb_array_length(t.content->'segments'),0) AS segment_count
           FROM meetings m LEFT JOIN LATERAL (
             SELECT content FROM transcripts WHERE meeting_id=m.id AND organization_id=m.organization_id
             ORDER BY created_at DESC,id DESC LIMIT 1
           ) t ON true
           WHERE m.organization_id=%s AND m.id=ANY(%s::uuid[]) ORDER BY m.meeting_at,m.id""",
        (organization_id, ids),
    )
    meetings = cur.fetchall()
    cur.execute(
        """SELECT DISTINCT ON (meeting_id) * FROM meeting_briefs
           WHERE organization_id=%s AND meeting_id=ANY(%s::uuid[])
           ORDER BY meeting_id,created_at DESC,id DESC""",
        (organization_id, ids),
    )
    briefs = {str(row["meeting_id"]): row for row in cur.fetchall()}
    for meeting in meetings:
        meeting["brief"] = briefs.get(str(meeting["id"]))
        meeting["transcript"] = {"content": meeting.pop("transcript_content")} if meeting.get("transcript_content") else None
    cur.execute(
        """SELECT t.*,m.title AS meeting_title,p.display_name
           FROM tasks t JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
           LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
           WHERE t.organization_id=%s AND t.meeting_id=ANY(%s::uuid[])
           ORDER BY m.meeting_at,t.created_at,t.id""",
        (organization_id, ids),
    )
    tasks = cur.fetchall()
    cur.execute(
        """SELECT r.task_id,t.meeting_id,m.title AS meeting_title,t.instruction AS task_instruction,
                  p.display_name,r.received_at,
                  COALESCE(r.reviewed_result->>'classification',r.classification) AS classification,
                  COALESCE(r.reviewed_result->>'corrected_transcript',r.transcript) AS text,
                  COALESCE(r.reviewed_result->>'due_text',r.promised_due_text) AS promised_due_text,
                  'AUTO'::text AS source
           FROM task_responses r JOIN tasks t ON t.id=r.task_id AND t.organization_id=r.organization_id
           JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
           LEFT JOIN people p ON p.id=r.person_id AND p.organization_id=r.organization_id
           WHERE r.organization_id=%s AND t.meeting_id=ANY(%s::uuid[])
           UNION ALL
           SELECT s.task_id,t.meeting_id,m.title,t.instruction,p.display_name,
                  COALESCE(s.finished_at,s.created_at),
                  CASE s.reported_status WHEN 'CLAIMED_DONE' THEN 'YES'
                    WHEN 'NOT_DONE' THEN 'NO' WHEN 'BLOCKED' THEN 'NO' ELSE 'UNCLEAR' END,
                  s.note,s.promised_due_text,'MANUAL'::text
           FROM manual_call_sessions s JOIN tasks t ON t.id=s.task_id AND t.organization_id=s.organization_id
           JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
           LEFT JOIN people p ON p.id=s.person_id AND p.organization_id=s.organization_id
           WHERE s.organization_id=%s AND t.meeting_id=ANY(%s::uuid[]) AND s.status<>'PREPARED'
           ORDER BY received_at,task_id""",
        (organization_id, ids, organization_id, ids),
    )
    responses = cur.fetchall()
    cur.execute(
        """SELECT c.*,p.display_name AS author_name,t.instruction AS task_instruction,
                  t.meeting_id,m.title AS meeting_title
           FROM task_comments c JOIN people p ON p.id=c.author_id AND p.organization_id=c.organization_id
           JOIN tasks t ON t.id=c.task_id AND t.organization_id=c.organization_id
           JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
           WHERE c.organization_id=%s AND t.meeting_id=ANY(%s::uuid[])
           ORDER BY c.created_at,c.id""",
        (organization_id, ids),
    )
    comments = cur.fetchall()
    if not title:
        title = "Отчёт по совещанию" if len(meetings) == 1 else "Отчёт по штабу"
    if not subtitle:
        subtitle = meetings[0]["title"] if len(meetings) == 1 else f"Совещаний: {len(meetings)}"
    return {"title": title, "subtitle": subtitle, "timezone": timezone_name, "generated_at": generated,
            "meetings": meetings, "tasks": tasks, "responses": responses, "comments": comments}


def report_zip(report):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("01_Отчёт.pdf", pdf_bytes(report))
        archive.writestr("02_Поручения.xlsx", xlsx_bytes(report))
        archive.writestr("03_Стенограмма.txt", transcript_text(report).encode("utf-8-sig"))
    return output.getvalue()


@bp.post("/tasks/<uuid:task_id>/comments")
def add_comment(task_id):
    role = _actor_role()
    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 4000:
        abort(400)
    organization_id = g.user["organization_id"]
    comment_id = str(uuid.uuid4())
    with current_app.store.connection() as connection, connection.cursor() as cur:
        cur.execute("SELECT id FROM tasks WHERE id=%s AND organization_id=%s", (str(task_id), organization_id))
        if not cur.fetchone():
            abort(404)
        cur.execute(
            "INSERT INTO task_comments(id,organization_id,task_id,author_id,author_role,body) VALUES (%s,%s,%s,%s,%s,%s)",
            (comment_id, organization_id, str(task_id), g.user["person_id"], role, body),
        )
        cur.execute(
            """INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
               VALUES (%s,%s,'USER',%s,'TASK_COMMENT_ADDED','TASK',%s,%s)""",
            (str(uuid.uuid4()), organization_id, g.user["person_id"], str(task_id), Json({"comment_id": comment_id, "author_role": role})),
        )
    return redirect(url_for("response_integration0181.task_detail", task_id=task_id), 303)


def task_detail(task_id):
    _actor_role()
    organization_id = g.user["organization_id"]
    with current_app.store.connection() as connection, connection.cursor() as cur:
        cur.execute(
            """SELECT t.*,m.title,m.meeting_at,p.display_name,o.timezone AS org_timezone
               FROM tasks t JOIN meetings m ON m.id=t.meeting_id AND m.organization_id=t.organization_id
               JOIN organizations o ON o.id=t.organization_id
               LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
               WHERE t.id=%s AND t.organization_id=%s""",
            (str(task_id), organization_id),
        )
        task = cur.fetchone()
        if not task:
            abort(404)
        cur.execute(
            """SELECT r.id,r.received_at,r.classification,r.review_status,r.transcript,
                 r.reason_transcript,r.promised_due_text,r.reviewed_result,'AUTO'::text AS source
               FROM task_responses r WHERE r.task_id=%s AND r.organization_id=%s
               UNION ALL
               SELECT s.id,COALESCE(s.finished_at,s.created_at),
                 CASE s.reported_status WHEN 'CLAIMED_DONE' THEN 'YES' WHEN 'NOT_DONE' THEN 'NO'
                   WHEN 'BLOCKED' THEN 'NO' ELSE 'UNCLEAR' END,
                 'NOT_REQUIRED',s.note,NULL,s.promised_due_text,
                 jsonb_build_object('outcome',s.outcome,'reported_status',s.reported_status,
                   'secretary_id',s.secretary_id),'MANUAL'::text
               FROM manual_call_sessions s WHERE s.task_id=%s AND s.organization_id=%s AND s.status<>'PREPARED'
               ORDER BY received_at DESC,id""",
            (str(task_id), organization_id, str(task_id), organization_id),
        )
        responses = cur.fetchall()
        cur.execute(
            """SELECT c.*,p.display_name AS author_name FROM task_comments c
               JOIN people p ON p.id=c.author_id AND p.organization_id=c.organization_id
               WHERE c.task_id=%s AND c.organization_id=%s ORDER BY c.created_at,c.id""",
            (str(task_id), organization_id),
        )
        comments = cur.fetchall()
        cur.execute(
            """SELECT event_type,created_at,payload FROM audit_events
               WHERE organization_id=%s AND ((entity_type='TASK' AND entity_id=%s)
                 OR (event_type LIKE 'MANUAL_CALL_%%' AND payload->>'task_id'=%s))
               ORDER BY created_at DESC,id LIMIT 100""",
            (organization_id, str(task_id), str(task_id)),
        )
        history = cur.fetchall()
    event_names = dict(EVENTS, TASK_COMMENT_ADDED="Добавлен комментарий")
    return render_template(
        "task_detail0184.html", task=task, responses=responses, comments=comments, history=history,
        outcomes=OUTCOMES, reported=REPORTED, task_statuses=TASK_STATUSES, lifecycles=LIFECYCLES,
        review_statuses=REVIEW_STATUSES, events=event_names,
        role_names={"admin": "Администратор", "head": "Руководитель", "secretary": "Секретарь"},
    )


@bp.get("/reports/period")
def period():
    _actor_role()
    raw_from = request.args.get("date_from", "")
    raw_to = request.args.get("date_to", "")
    if not raw_from and not raw_to:
        today = date.today()
        return render_template("report_period0184.html", date_from=today - timedelta(days=30), date_to=today)
    try:
        first = date.fromisoformat(raw_from)
        last = date.fromisoformat(raw_to)
    except ValueError:
        abort(400)
    if last < first or (last - first).days > 366:
        abort(400)
    tz = ZoneInfo(g.user.get("timezone") or "UTC")
    start = datetime.combine(first, time.min, tzinfo=tz)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz)
    subtitle = f"Период: {first.strftime('%d.%m.%Y')}–{last.strftime('%d.%m.%Y')}"
    with current_app.store.connection() as connection, connection.cursor() as cur:
        report = collect_report(cur, g.user["organization_id"], g.user.get("timezone") or "UTC", start=start, end=end,
                                title="Отчёт по штабу", subtitle=subtitle)
    return Response(report_zip(report), content_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="shtab-ai-report-{first}-{last}.zip"'})


def register(app):
    app.register_blueprint(bp)
    app.view_functions["response_integration0181.task_detail"] = task_detail
    previous_export = app.view_functions["imports.export"]

    def export_report(mid, fmt):
        if fmt != "zip":
            return previous_export(mid, fmt)
        with current_app.store.connection() as connection, connection.cursor() as cur:
            report = collect_report(cur, g.user["organization_id"], g.user.get("timezone") or "UTC", meeting_id=mid)
        if not report["meetings"]:
            abort(404)
        return Response(report_zip(report), content_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="shtab-ai-meeting-{mid}.zip"'})

    export_report.__name__ = "export_report0184"
    app.view_functions["imports.export"] = export_report
