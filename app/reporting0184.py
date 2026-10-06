"""PDF, XLSX and text reporting for meetings and date ranges."""
import io
import sys
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from zoneinfo import ZoneInfo


VENDOR = Path(__file__).resolve().parent / "vendor0184"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from xlsx0184 import workbook_bytes


TASK_STATUS = {
    "PENDING": "Ожидает исполнения",
    "CLAIMED_DONE": "Исполнение заявлено",
    "CONFIRMED_DONE": "Выполнение подтверждено",
    "NOT_DONE": "Не исполнено",
    "BLOCKED": "Есть препятствие",
    "UNKNOWN": "Статус неясен",
    "CANCELLED": "Отменено",
}
RESPONSE_CLASS = {"YES": "Заявлено исполнение", "NO": "Не исполнено", "UNCLEAR": "Ответ неясен"}
ROLE_NAMES = {"admin": "Администратор", "head": "Руководитель", "secretary": "Секретарь"}
BRIEF_STATUS = {"DRAFT": "Черновик", "APPROVED": "Утверждён", "SUPERSEDED": "Заменён"}
LIFECYCLE_STATUS = {
    "DRAFT": "Черновик", "APPROVED": "Утверждено", "ACTIVE": "В работе",
    "CLOSED": "Закрыто", "CANCELLED": "Отменено",
}


def _safe(value):
    return str(value or "").strip()


def _tz(name):
    try:
        return ZoneInfo(name)
    except (KeyError, TypeError, ValueError):
        return timezone.utc


def local_dt(value, timezone_name):
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo:
        value = value.astimezone(_tz(timezone_name))
    return value


def local_text(value, timezone_name):
    value = local_dt(value, timezone_name)
    return value.strftime("%d.%m.%Y %H:%M") if value else "Не указано"


def excel_date(value, timezone_name):
    value = local_dt(value, timezone_name)
    # Text is deliberate: every office suite displays the Russian date
    # consistently, while the report remains filterable as a normal column.
    return value.strftime("%d.%m.%Y %H:%M") if value else None


def brief_parts(brief):
    if not brief:
        return [], "", []
    content = brief.get("content") or {}
    narrative = content.get("narrative") or {}
    paragraphs = narrative.get("paragraphs") or content.get("overview") or []
    paragraph_texts = [_safe(item.get("text") if isinstance(item, dict) else item) for item in paragraphs]
    conclusion = narrative.get("conclusion")
    conclusion_text = _safe(conclusion.get("text") if isinstance(conclusion, dict) else conclusion)
    decisions = [_safe(item.get("text") if isinstance(item, dict) else item) for item in content.get("decisions", [])]
    return [item for item in paragraph_texts if item], conclusion_text, [item for item in decisions if item]


def _font_names():
    regular = VENDOR / "DejaVuSans.ttf"
    bold = VENDOR / "DejaVuSans-Bold.ttf"
    if "ShtabSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ShtabSans", str(regular)))
        pdfmetrics.registerFont(TTFont("ShtabSans-Bold", str(bold)))
        pdfmetrics.registerFontFamily(
            "ShtabSans",
            normal="ShtabSans",
            bold="ShtabSans-Bold",
            italic="ShtabSans",
            boldItalic="ShtabSans-Bold",
        )
    return "ShtabSans", "ShtabSans-Bold"


def pdf_bytes(report):
    normal_font, bold_font = _font_names()
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=report["title"],
        author="Штаб.AI",
    )
    base = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=base["BodyText"], fontName=normal_font, fontSize=9.5, leading=14, spaceAfter=7)
    small = ParagraphStyle("Small", parent=body, fontSize=8, leading=11, textColor=colors.HexColor("#607484"))
    title = ParagraphStyle("Title", parent=body, fontName=bold_font, fontSize=18, leading=22, textColor=colors.HexColor("#0D466E"), spaceAfter=7)
    heading = ParagraphStyle("Heading", parent=body, fontName=bold_font, fontSize=12, leading=15, textColor=colors.HexColor("#16354D"), spaceBefore=9, spaceAfter=5)
    table_head = ParagraphStyle("TableHead", parent=small, fontName=bold_font, textColor=colors.white, alignment=TA_CENTER)
    table_body = ParagraphStyle("TableBody", parent=small, textColor=colors.HexColor("#172D3D"))

    story = [Paragraph(html_escape(report["title"]), title), Paragraph(html_escape(report["subtitle"]), small), Spacer(1, 4 * mm)]
    counts = [
        ["Совещаний", "Поручений", "Ответов", "Комментариев"],
        [str(len(report["meetings"])), str(len(report["tasks"])), str(len(report["responses"])), str(len(report["comments"]))],
    ]
    summary_table = Table(counts, colWidths=[42 * mm] * 4, repeatRows=1)
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), bold_font), ("FONTNAME", (0, 1), (-1, -1), normal_font),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16354D")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#EAF3F8")), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend((summary_table, Spacer(1, 5 * mm)))

    tasks_by_meeting = {}
    for task in report["tasks"]:
        tasks_by_meeting.setdefault(str(task["meeting_id"]), []).append(task)
    for meeting_no, meeting in enumerate(report["meetings"], 1):
        if meeting_no > 1:
            story.append(PageBreak())
        story.append(Paragraph(f"{meeting_no}. {html_escape(_safe(meeting['title']))}", heading))
        story.append(Paragraph(f"Дата: {html_escape(local_text(meeting.get('meeting_at'), report['timezone']))}", small))
        paragraphs, conclusion, decisions = brief_parts(meeting.get("brief"))
        if paragraphs:
            story.append(Paragraph("Краткое описание", heading))
            story.extend(Paragraph(html_escape(item), body) for item in paragraphs)
        else:
            story.append(Paragraph("Бриф не подготовлен.", small))
        if conclusion:
            story.extend((Paragraph("Итог", heading), Paragraph(f"<b>{html_escape(conclusion)}</b>", body)))
        if decisions:
            story.append(Paragraph("Решения", heading))
            story.extend(Paragraph(f"{number}. {html_escape(item)}", body) for number, item in enumerate(decisions, 1))
        meeting_tasks = tasks_by_meeting.get(str(meeting["id"]), [])
        story.append(Paragraph("Поручения", heading))
        if meeting_tasks:
            data = [[Paragraph(value, table_head) for value in ("№", "Поручение", "Ответственный", "Срок", "Статус")]]
            for number, task in enumerate(meeting_tasks, 1):
                due = local_text(task.get("due_at"), report["timezone"]) if task.get("due_at") else (_safe(task.get("due_text")) or "Не указан")
                data.append([
                    Paragraph(str(number), table_body), Paragraph(html_escape(_safe(task.get("instruction"))), table_body),
                    Paragraph(html_escape(_safe(task.get("display_name")) or "Не указан"), table_body),
                    Paragraph(html_escape(due), table_body), Paragraph(html_escape(TASK_STATUS.get(task.get("execution_status"), _safe(task.get("execution_status")))), table_body),
                ])
            task_table = Table(data, colWidths=[9 * mm, 70 * mm, 34 * mm, 29 * mm, 36 * mm], repeatRows=1, splitByRow=True)
            task_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16354D")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D4DCE2")),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(task_table)
        else:
            story.append(Paragraph("Поручений нет.", small))

    if report["comments"]:
        story.append(PageBreak())
        story.append(Paragraph("Комментарии руководителя и секретаря", heading))
        for comment in report["comments"]:
            label = f"{_safe(comment.get('author_name'))} · {ROLE_NAMES.get(comment.get('author_role'), comment.get('author_role'))} · {local_text(comment.get('created_at'), report['timezone'])}"
            story.append(KeepTogether([
                Paragraph(html_escape(label), small),
                Paragraph(html_escape(_safe(comment.get("task_instruction"))), ParagraphStyle("CommentTask", parent=small, fontName=bold_font)),
                Paragraph(html_escape(_safe(comment.get("body"))), body),
            ]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(normal_font, 7.5)
        canvas.setFillColor(colors.HexColor("#607484"))
        canvas.drawString(17 * mm, 10 * mm, "Штаб.AI · сформировано " + local_text(report["generated_at"], report["timezone"]))
        canvas.drawRightString(193 * mm, 10 * mm, f"Страница {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def xlsx_bytes(report):
    subtitle = report["subtitle"] + " · сформировано " + local_text(report["generated_at"], report["timezone"])
    task_counts = {}
    response_counts = {}
    comment_counts = {}
    for task in report["tasks"]:
        task_counts[str(task["meeting_id"])] = task_counts.get(str(task["meeting_id"]), 0) + 1
    for item in report["responses"]:
        response_counts[str(item["meeting_id"])] = response_counts.get(str(item["meeting_id"]), 0) + 1
    for item in report["comments"]:
        comment_counts[str(item["meeting_id"])] = comment_counts.get(str(item["meeting_id"]), 0) + 1
    summary_rows = []
    for number, meeting in enumerate(report["meetings"], 1):
        key = str(meeting["id"])
        brief = meeting.get("brief")
        summary_rows.append([
            number, _safe(meeting.get("title")), excel_date(meeting.get("meeting_at"), report["timezone"]),
            int(meeting.get("segment_count") or 0), task_counts.get(key, 0), response_counts.get(key, 0),
            comment_counts.get(key, 0), BRIEF_STATUS.get(brief.get("review_status"), brief.get("review_status")) if brief else "Не подготовлен",
        ])
    task_rows = []
    for number, task in enumerate(report["tasks"], 1):
        task_rows.append([
            number, _safe(task.get("meeting_title")), _safe(task.get("instruction")), _safe(task.get("display_name")) or "Не указан",
            excel_date(task.get("due_at"), report["timezone"]), _safe(task.get("due_text")),
            TASK_STATUS.get(task.get("execution_status"), _safe(task.get("execution_status"))),
            LIFECYCLE_STATUS.get(task.get("lifecycle"), _safe(task.get("lifecycle"))),
        ])
    response_rows = []
    for number, response in enumerate(report["responses"], 1):
        response_rows.append([
            number, _safe(response.get("meeting_title")), _safe(response.get("task_instruction")), _safe(response.get("display_name")),
            "Ручной звонок" if response.get("source") == "MANUAL" else "Автоматический ответ",
            RESPONSE_CLASS.get(response.get("classification"), _safe(response.get("classification"))),
            _safe(response.get("text")), _safe(response.get("promised_due_text")),
            excel_date(response.get("received_at"), report["timezone"]),
        ])
    comment_rows = []
    for number, comment in enumerate(report["comments"], 1):
        comment_rows.append([
            number, _safe(comment.get("meeting_title")), _safe(comment.get("task_instruction")), _safe(comment.get("author_name")),
            ROLE_NAMES.get(comment.get("author_role"), comment.get("author_role")), _safe(comment.get("body")),
            excel_date(comment.get("created_at"), report["timezone"]),
        ])
    sheets = [
        {"name": "Сводка", "title": report["title"], "subtitle": subtitle, "headers": ["№", "Совещание", "Дата", "Фрагменты", "Поручения", "Ответы", "Комментарии", "Бриф"], "rows": summary_rows, "widths": [6, 42, 19, 13, 13, 11, 14, 18], "status_column": 8},
        {"name": "Поручения", "title": "Поручения", "subtitle": subtitle, "headers": ["№", "Совещание", "Поручение", "Ответственный", "Срок", "Срок текстом", "Статус", "Состояние"], "rows": task_rows, "widths": [6, 32, 58, 24, 19, 20, 24, 16], "status_column": 7},
        {"name": "Ответы", "title": "Ответы исполнителей", "subtitle": subtitle, "headers": ["№", "Совещание", "Поручение", "Исполнитель", "Источник", "Результат", "Текст ответа", "Обещанный срок", "Получено"], "rows": response_rows, "widths": [6, 28, 44, 22, 20, 23, 48, 20, 19], "status_column": 6},
        {"name": "Комментарии", "title": "Комментарии", "subtitle": subtitle, "headers": ["№", "Совещание", "Поручение", "Автор", "Роль", "Комментарий", "Дата"], "rows": comment_rows, "widths": [6, 28, 44, 22, 16, 60, 19]},
    ]
    return workbook_bytes(sheets, report["generated_at"])


def transcript_text(report):
    lines = [report["title"], report["subtitle"], ""]
    for meeting in report["meetings"]:
        lines.extend(("=" * 72, _safe(meeting.get("title")), local_text(meeting.get("meeting_at"), report["timezone"]), ""))
        transcript = meeting.get("transcript") or {}
        for segment in (transcript.get("content") or {}).get("segments", []):
            start = float(segment.get("start") or 0)
            minutes, seconds = divmod(int(start), 60)
            lines.append(f"[{minutes:02d}:{seconds:02d}] {_safe(segment.get('text'))}")
        if not transcript:
            lines.append("Стенограмма не подготовлена.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
