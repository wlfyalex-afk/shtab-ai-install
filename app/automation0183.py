"""Unified meeting overview: processing status, metrics, brief and live tasks."""
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for


bp = Blueprint("automation0183", __name__)
ACTIVE_IMPORT = {"UPLOADING", "QUEUED", "FETCHING", "ASSEMBLING", "CONVERTING", "TRANSCRIBING"}
ACTIVE_JOB = {"QUEUED", "RUNNING"}
STATUS_NAMES = {
    "UPLOADING": "Загружается",
    "QUEUED": "В очереди",
    "FETCHING": "Получение файла",
    "ASSEMBLING": "Сборка записи",
    "CONVERTING": "Подготовка аудио",
    "TRANSCRIBING": "Распознавание",
    "REVIEW": "Стенограмма готова",
    "DUPLICATE": "Запись уже обработана",
    "RUNNING": "Выполняется",
    "DONE": "Готово",
    "FAILED": "Ошибка",
    "CANCELLED": "Остановлено",
    "WAITING": "Ожидает предыдущий этап",
}
ERROR_NAMES = {
    "MODEL_OUTPUT_INCOMPLETE": "Модель не завершила формирование результата.",
    "MODEL_INVALID_RESPONSE": "Модель вернула ответ в неподдерживаемом формате.",
    "NARRATIVE_NOT_GROUNDED": "Связный текст не прошёл проверку по источникам.",
    "NARRATIVE_UNKNOWN_EVIDENCE": "Модель указала неизвестный подтверждающий фрагмент.",
    "NARRATIVE_OUTPUT_INCOMPLETE": "Модель не завершила связный текст брифа.",
    "MODEL_INVALID_JSON": "Модель вернула незавершённый JSON; выполнена повторная попытка.",
    "MODEL_INVALID_SCHEMA": "Структура ответа модели не прошла проверку.",
    "MODEL_RESPONSE_TOO_LARGE": "Ответ модели превысил допустимый размер.",
    "MODEL_TOOL_CALL_REFUSED": "Модель не выполнила требуемый локальный запрос.",
    "MODEL_CHANGED": "Локальная модель изменилась во время обработки.",
    "MODEL_OR_DATABASE_ERROR": "Произошла ошибка модели или базы данных.",
    "SOURCE_CHANGED": "Исходная стенограмма изменилась во время обработки.",
    "SOURCE_OR_JOB_CHANGED": "Исходные данные или задание изменились.",
    "NO_GROUNDED_EVIDENCE": "Не найдено достаточно подтверждённых фрагментов для брифа.",
    "ASR_NOT_VERIFIED": "Стенограмма ещё не прошла требуемую проверку.",
    "CHUNKS_NOT_COMPLETE": "Обработаны не все части записи.",
    "CHUNK_PLAN_CHANGED": "Состав частей записи изменился.",
    "LOCAL_MODEL_NOT_INSTALLED": "Локальная языковая модель не установлена.",
    "RETRY_LIMIT": "Исчерпано число автоматических попыток.",
    "PROCESS_FAILED": "Фоновая обработка завершилась с ошибкой.",
}


def editable():
    return bool(g.user and (g.user.get("is_admin") or g.user.get("app_role", "secretary") == "secretary"))


def owned(cur, mid):
    cur.execute("SELECT * FROM meetings WHERE id=%s AND organization_id=%s", (str(mid), g.user["organization_id"]))
    row = cur.fetchone()
    if not row:
        abort(404)
    return row


def _seconds(value):
    return max(0.0, float(value or 0))


def _wall_seconds(row):
    if not row:
        return 0.0
    end = row.get("updated_at") or datetime.now(timezone.utc)
    start = row.get("created_at") or end
    return max(0.0, (end - start).total_seconds())


def _stage(label, status, compute=0, wall=0, detail=""):
    return {
        "label": label,
        "status": status,
        "status_label": STATUS_NAMES.get(status, status),
        "compute_seconds": _seconds(compute),
        "wall_seconds": _seconds(wall),
        "detail": detail,
    }


def format_duration(value):
    seconds = max(0, int(round(float(value or 0))))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин {seconds:02d} с"
    if minutes:
        return f"{minutes} мин {seconds:02d} с"
    return f"{seconds} с"


def status_page(mid):
    organization_id = g.user["organization_id"]
    with current_app.store.connection() as connection, connection.cursor() as cur:
        meeting = owned(cur, mid)
        cur.execute(
            """SELECT * FROM meeting_imports WHERE meeting_id=%s AND organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (str(mid), organization_id),
        )
        import_job = cur.fetchone()

        transcript = None
        if import_job and import_job.get("transcript_id"):
            cur.execute(
                """SELECT id,created_at,engine,model,language,
                          jsonb_array_length(content->'segments') AS segment_count
                   FROM transcripts WHERE id=%s AND organization_id=%s""",
                (str(import_job["transcript_id"]), organization_id),
            )
            transcript = cur.fetchone()
        if not transcript:
            cur.execute(
                """SELECT id,created_at,engine,model,language,
                          jsonb_array_length(content->'segments') AS segment_count
                   FROM transcripts WHERE meeting_id=%s AND organization_id=%s
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (str(mid), organization_id),
            )
            transcript = cur.fetchone()

        cur.execute(
            """SELECT * FROM meeting_llm_jobs WHERE meeting_id=%s AND organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (str(mid), organization_id),
        )
        extraction = cur.fetchone()
        extraction_compute = 0.0
        if extraction:
            cur.execute("SELECT COALESCE(sum(elapsed_seconds),0) AS seconds FROM meeting_llm_chunks WHERE job_id=%s", (str(extraction["id"]),))
            extraction_compute = cur.fetchone()["seconds"]

        cur.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE status='PENDING') AS pending,
                      count(*) FILTER (WHERE status='APPROVED') AS approved,
                      count(*) FILTER (WHERE status='REJECTED') AS rejected
               FROM meeting_task_drafts WHERE meeting_id=%s AND organization_id=%s""",
            (str(mid), organization_id),
        )
        drafts = cur.fetchone()

        cur.execute(
            """SELECT * FROM meeting_brief_jobs WHERE meeting_id=%s AND organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (str(mid), organization_id),
        )
        brief_job = cur.fetchone()
        brief_compute = 0.0
        brief_attempt = None
        if brief_job:
            cur.execute("SELECT COALESCE(sum(elapsed_seconds),0) AS seconds FROM meeting_brief_chunks WHERE job_id=%s", (str(brief_job["id"]),))
            brief_compute = _seconds(cur.fetchone()["seconds"]) + _seconds(brief_job.get("final_elapsed_seconds"))
            cur.execute("""SELECT stage,chunk_no,attempt_no,outcome,done_reason,input_bytes,output_bytes,
                                  prompt_tokens,output_tokens,elapsed_seconds,error_code,created_at
                           FROM meeting_brief_attempts WHERE job_id=%s
                           ORDER BY created_at DESC,id DESC LIMIT 1""", (str(brief_job["id"]),))
            brief_attempt = cur.fetchone()

        cur.execute(
            """SELECT * FROM meeting_briefs WHERE meeting_id=%s AND organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (str(mid), organization_id),
        )
        brief = cur.fetchone()

        cur.execute(
            """SELECT t.id,t.instruction,t.execution_status,t.due_text,t.due_at,p.display_name
               FROM tasks t LEFT JOIN people p
                 ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
               WHERE t.meeting_id=%s AND t.organization_id=%s
                 AND t.lifecycle IN ('APPROVED','ACTIVE','CLOSED')
                 AND t.review_status IN ('CONFIRMED','CORRECTED')
               ORDER BY t.created_at,t.id""",
            (str(mid), organization_id),
        )
        tasks = cur.fetchall()

        metric_rows = []
        upload_seconds = 0.0
        if import_job:
            if import_job.get("upload_started_at"):
                upload_end = import_job.get("upload_finished_at") or import_job.get("updated_at")
                upload_seconds = max(0.0, (upload_end - import_job["upload_started_at"]).total_seconds())
            if import_job.get("group_id"):
                cur.execute(
                    """SELECT COALESCE(sum(EXTRACT(EPOCH FROM
                         (COALESCE(upload_finished_at,updated_at)-upload_started_at))),0) AS seconds
                       FROM meeting_imports WHERE group_id=%s AND organization_id=%s
                         AND upload_started_at IS NOT NULL""",
                    (str(import_job["group_id"]), organization_id),
                )
                upload_seconds = _seconds(cur.fetchone()["seconds"])
                cur.execute(
                    """SELECT stage,COALESCE(sum(elapsed_seconds),0) AS seconds
                       FROM meeting_import_metrics WHERE import_id IN
                         (SELECT id FROM meeting_imports WHERE group_id=%s AND organization_id=%s)
                       GROUP BY stage""",
                    (str(import_job["group_id"]), organization_id),
                )
            else:
                cur.execute(
                    """SELECT stage,COALESCE(sum(elapsed_seconds),0) AS seconds
                       FROM meeting_import_metrics WHERE import_id=%s GROUP BY stage""",
                    (str(import_job["id"]),),
                )
            metric_rows = cur.fetchall()

    metric = {row["stage"]: _seconds(row["seconds"]) for row in metric_rows}
    preparation = sum(metric.get(name, 0) for name in ("FETCHING", "VERIFYING", "ASSEMBLING", "PROBING", "CONVERTING", "CONCATENATING"))
    recognition = metric.get("TRANSCRIBING", 0) + metric.get("SAVING", 0)
    import_status = import_job["status"] if import_job else "WAITING"
    source_ready = bool(transcript)
    recognition_status = ("DUPLICATE" if import_status == "DUPLICATE" and source_ready else ("DONE" if source_ready else import_status))
    recognition_detail = ((f"Использована ранее созданная стенограмма · {transcript['segment_count']} фрагментов") if recognition_status == "DUPLICATE" else (f"{transcript['segment_count']} фрагментов" if transcript else "ожидает готовности записи"))
    extraction_status = extraction["status"] if extraction else "WAITING"
    brief_status = brief_job["status"] if brief_job else "WAITING"
    stages = [
        _stage("Загрузка записи", "DONE" if source_ready else import_status, upload_seconds, 0, import_job["original_name"] if import_job else ""),
        _stage("Подготовка аудио", "DONE" if source_ready else import_status, preparation, 0, "конвертация и проверка файла"),
        _stage("Распознавание", recognition_status, recognition, 0, recognition_detail),
        _stage(
            "Выделение поручений",
            extraction_status,
            extraction_compute,
            _wall_seconds(extraction),
            f"{drafts['total']} черновиков · {drafts['approved']} утверждено",
        ),
        _stage(
            "Связный бриф",
            brief_status,
            brief_compute,
            _wall_seconds(brief_job),
            (f"{brief_job['next_chunk']} из {brief_job['total_chunks']} частей" if brief_job else "ожидает автоматического запуска или ручного запуска для старой записи"),
        ),
    ]
    active = bool(
        (import_job and import_job["status"] in ACTIVE_IMPORT)
        or (extraction and extraction["status"] in ACTIVE_JOB)
        or (brief_job and brief_job["status"] in ACTIVE_JOB)
    )
    source_map = {item["id"]: item for item in (brief["content"].get("sources", []) if brief else [])}
    return render_template(
        "meeting_status0183.html",
        meeting=meeting,
        import_job=import_job,
        transcript=transcript,
        extraction=extraction,
        drafts=drafts,
        brief_job=brief_job,
        brief=brief,
        brief_attempt=brief_attempt,
        source_map=source_map,
        tasks=tasks,
        stages=stages,
        total_compute=sum(item["compute_seconds"] for item in stages),
        can_edit=editable(),
        active=active,
        status_names=STATUS_NAMES,
        error_names=ERROR_NAMES,
    )


def brief_redirect(mid):
    return redirect(url_for("workflow0182.status", mid=mid), 302)


def register(app):
    app.register_blueprint(bp)
    app.jinja_env.filters["duration0183"] = format_duration
    # Preserve old URLs/bookmarks while making the overview the single home for the brief.
    app.view_functions["workflow0182.status"] = status_page
    app.view_functions["brief017.page"] = brief_redirect
