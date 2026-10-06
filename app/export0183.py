"""One-click meeting archive with transcript, approved tasks and brief."""
import csv
import io
import zipfile

from flask import Response, abort, current_app, g

from import_common import exports


TASK_STATUS = {
    "PENDING": "Ожидает исполнения",
    "IN_PROGRESS": "В работе",
    "BLOCKED": "Есть препятствие",
    "DONE_REPORTED": "Исполнение заявлено",
    "DONE_CONFIRMED": "Выполнение подтверждено",
    "CANCELLED": "Отменено",
}


def _text(value):
    return str(value or "").strip()


def _date(value, timezone_name):
    if not value:
        return ""
    try:
        from zoneinfo import ZoneInfo

        value = value.astimezone(ZoneInfo(timezone_name))
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    return value.strftime("%d.%m.%Y %H:%M")


def tasks_csv(tasks, timezone_name):
    stream = io.StringIO(newline="")
    stream.write("sep=;\r\n")
    writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
    writer.writerow(("№", "Поручение", "Ответственный", "Срок", "Статус"))
    for number, task in enumerate(tasks, 1):
        due = _date(task.get("due_at"), timezone_name) or _text(task.get("due_text"))
        writer.writerow(
            (
                number,
                _text(task.get("instruction")),
                _text(task.get("display_name")) or "Не указан",
                due or "Не указан",
                TASK_STATUS.get(task.get("execution_status"), task.get("execution_status") or "Не указан"),
            )
        )
    return stream.getvalue().encode("utf-8-sig")


def brief_text(meeting, brief, tasks, timezone_name):
    lines = [
        "Штаб.AI — бриф совещания",
        "",
        f"Совещание: {_text(meeting.get('title'))}",
        f"Дата: {_date(meeting.get('meeting_at'), timezone_name) or 'Не указана'}",
        "",
    ]
    if not brief:
        lines.append("Бриф ещё не подготовлен.")
        return "\n".join(lines).rstrip() + "\n"

    content = brief.get("content") or {}
    narrative = content.get("narrative") or {}
    paragraphs = narrative.get("paragraphs") or content.get("overview") or []
    lines.append("КРАТКОЕ ОПИСАНИЕ")
    for item in paragraphs:
        text = _text(item.get("text") if isinstance(item, dict) else item)
        if text:
            lines.extend((text, ""))

    conclusion = narrative.get("conclusion")
    conclusion_text = _text(conclusion.get("text") if isinstance(conclusion, dict) else conclusion)
    lines.extend(("ИТОГ", conclusion_text or "Итог отдельно не сформулирован.", ""))

    lines.append("РЕШЕНИЯ")
    decisions = content.get("decisions") or []
    if decisions:
        for number, item in enumerate(decisions, 1):
            lines.append(f"{number}. {_text(item.get('text') if isinstance(item, dict) else item)}")
    else:
        lines.append("Явно зафиксированных решений нет.")
    lines.append("")

    lines.append("УТВЕРЖДЁННЫЕ ПОРУЧЕНИЯ")
    if tasks:
        for number, task in enumerate(tasks, 1):
            due = _date(task.get("due_at"), timezone_name) or _text(task.get("due_text")) or "срок не указан"
            assignee = _text(task.get("display_name")) or "ответственный не указан"
            lines.append(f"{number}. {_text(task.get('instruction'))} — {assignee}; {due}.")
    else:
        lines.append("Утверждённых поручений нет.")

    extra = (
        ("РИСКИ И ПРЕПЯТСТВИЯ", "risks"),
        ("СУЩЕСТВЕННЫЕ ФАКТЫ", "facts"),
        ("ОТКРЫТЫЕ ВОПРОСЫ", "open_questions"),
    )
    for heading, key in extra:
        items = content.get(key) or []
        if not items:
            continue
        lines.extend(("", heading))
        for number, item in enumerate(items, 1):
            lines.append(f"{number}. {_text(item.get('text') if isinstance(item, dict) else item)}")
    return "\n".join(lines).rstrip() + "\n"


def build_archive(meeting, transcript, tasks, brief, timezone_name):
    output = io.BytesIO()
    transcript_text = exports(transcript["content"])[0] if transcript else "Стенограмма ещё не подготовлена.\n"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("01_Стенограмма.txt", transcript_text.encode("utf-8-sig"))
        archive.writestr("02_Поручения.csv", tasks_csv(tasks, timezone_name))
        archive.writestr("03_Бриф.txt", brief_text(meeting, brief, tasks, timezone_name).encode("utf-8-sig"))
    return output.getvalue()


def register(app):
    original_export = app.view_functions["imports.export"]

    def export_with_package(mid, fmt):
        if fmt != "zip":
            return original_export(mid, fmt)
        organization_id = g.user["organization_id"]
        with current_app.store.connection() as connection, connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM meetings WHERE id=%s AND organization_id=%s",
                (str(mid), organization_id),
            )
            meeting = cur.fetchone()
            if not meeting:
                abort(404)
            cur.execute(
                """SELECT * FROM transcripts WHERE meeting_id=%s AND organization_id=%s
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (str(mid), organization_id),
            )
            transcript = cur.fetchone()
            cur.execute(
                """SELECT t.instruction,t.execution_status,t.due_text,t.due_at,p.display_name
                   FROM tasks t LEFT JOIN people p
                     ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
                   WHERE t.meeting_id=%s AND t.organization_id=%s
                     AND t.lifecycle IN ('APPROVED','ACTIVE','CLOSED')
                     AND t.review_status IN ('CONFIRMED','CORRECTED')
                   ORDER BY t.created_at,t.id""",
                (str(mid), organization_id),
            )
            tasks = cur.fetchall()
            cur.execute(
                """SELECT * FROM meeting_briefs WHERE meeting_id=%s AND organization_id=%s
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (str(mid), organization_id),
            )
            brief = cur.fetchone()
        data = build_archive(meeting, transcript, tasks, brief, g.user.get("timezone", "UTC"))
        return Response(
            data,
            content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="shtab-ai-meeting-{mid}.zip"'},
        )

    export_with_package.__name__ = "export_with_package"
    app.view_functions["imports.export"] = export_with_package
