#!/usr/bin/env python3
import argparse
import json
import os
import time
import uuid
from pathlib import Path

from psycopg2.extras import Json

from store import Store
from llm_core013 import MODEL, Invalid, model_identity
from brief_core017 import (
    RELIABILITY_VERSION,
    VERSION,
    brief_chunks,
    fallback_final,
    fingerprint,
    infer_final,
    infer_map,
    make_empty_brief,
    select_evidence,
    validate_final,
    validate_map,
)
from brief_narrative0182 import (
    attach_narrative,
    empty_narrative,
    fallback_narrative,
    infer_narrative,
    validate_narrative,
)
from ops_control018 import (
    CancelRequested,
    PauseRequested,
    checkpoint as ops_checkpoint,
    fail as ops_fail,
    finish as ops_finish,
)


LOCKS = (13013013, 8008002, 74004004)


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)


def store():
    return Store(json.loads(Path(os.environ.get("SHTAB_WEB_CONFIG", "/etc/shtab-ai/secretary-web.json")).read_text()))


def take_locks(cur):
    for key in LOCKS:
        cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (key,))
        if not cur.fetchone()["locked"]:
            return False
    return True


def load_current(cur, job):
    cur.execute(
        """SELECT j.*,t.content FROM meeting_brief_jobs j JOIN transcripts t ON t.id=j.transcript_id
           JOIN meetings m ON m.id=j.meeting_id WHERE j.id=%s AND j.organization_id=%s
           AND t.organization_id=j.organization_id AND t.meeting_id=j.meeting_id
           AND m.organization_id=j.organization_id FOR UPDATE OF j""",
        (str(job["id"]), job["organization_id"]),
    )
    current = cur.fetchone()
    if not current or current["next_chunk"] != job["next_chunk"] or fingerprint(current["content"]) != job["source_hash"]:
        raise Invalid("SOURCE_OR_JOB_CHANGED")
    return current


def validation_attempt(stage, error_code):
    return {
        "stage": stage,
        "attempt_no": 0,
        "outcome": "FALLBACK",
        "done_reason": None,
        "input_bytes": 0,
        "output_bytes": 0,
        "prompt_tokens": None,
        "output_tokens": None,
        "elapsed_seconds": 0,
        "error_code": error_code,
    }


def save_attempts(cur, job, attempts, chunk_no=None):
    if not attempts:
        return
    for row in attempts:
        cur.execute(
            """INSERT INTO meeting_brief_attempts
               (job_id,run_no,stage,chunk_no,attempt_no,outcome,done_reason,input_bytes,output_bytes,
                prompt_tokens,output_tokens,elapsed_seconds,error_code)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                str(job["id"]),
                int(job.get("retry_count") or 0),
                row["stage"],
                chunk_no,
                row["attempt_no"],
                row["outcome"],
                row.get("done_reason"),
                row.get("input_bytes", 0),
                row.get("output_bytes", 0),
                row.get("prompt_tokens"),
                row.get("output_tokens"),
                row.get("elapsed_seconds", 0),
                row.get("error_code"),
            ),
        )
    last = attempts[-1]
    cur.execute(
        """UPDATE meeting_brief_jobs SET last_stage=%s,last_done_reason=%s,
           last_prompt_tokens=%s,last_output_tokens=%s,last_input_bytes=%s,last_output_bytes=%s,
           last_attempt_at=now() WHERE id=%s""",
        (
            last["stage"],
            last.get("done_reason"),
            last.get("prompt_tokens"),
            last.get("output_tokens"),
            last.get("input_bytes", 0),
            last.get("output_bytes", 0),
            str(job["id"]),
        ),
    )


def save_chunk(cur, job, raw, evidence, rejected, elapsed, identity, attempts):
    load_current(cur, job)
    save_attempts(cur, job, attempts, job["next_chunk"])
    cur.execute(
        """INSERT INTO meeting_brief_chunks(job_id,chunk_no,response,evidence,elapsed_seconds,rejected_count)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (str(job["id"]), job["next_chunk"], Json(raw), Json(evidence), elapsed, rejected),
    )
    cur.execute(
        """UPDATE meeting_brief_jobs SET status='RUNNING',next_chunk=next_chunk+1,
           evidence_count=evidence_count+%s,rejected_count=rejected_count+%s,model_digest=%s,
           error_code=NULL,updated_at=now() WHERE id=%s AND organization_id=%s""",
        (len(evidence), rejected, identity, str(job["id"]), job["organization_id"]),
    )
    cur.execute(
        """INSERT INTO audit_events(id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
           VALUES (%s,%s,'WORKER','MEETING_BRIEF_CHUNK_SAVED','MEETING',%s,%s)""",
        (
            str(uuid.uuid4()),
            job["organization_id"],
            job["meeting_id"],
            Json({"job_id": str(job["id"]), "chunk": job["next_chunk"], "evidence": len(evidence), "rejected": rejected}),
        ),
    )


def approved_tasks(cur, job):
    cur.execute(
        """SELECT t.id,t.instruction,t.execution_status,t.due_text,t.due_at,p.display_name
           FROM tasks t LEFT JOIN people p ON p.id=t.primary_assignee_id AND p.organization_id=t.organization_id
           WHERE t.meeting_id=%s AND t.organization_id=%s AND t.lifecycle IN ('APPROVED','ACTIVE','CLOSED')
           AND t.review_status IN ('CONFIRMED','CORRECTED') ORDER BY t.created_at,t.id""",
        (job["meeting_id"], job["organization_id"]),
    )
    return [
        dict(
            id=str(row["id"]),
            instruction=row["instruction"],
            assignee=row["display_name"],
            status=row["execution_status"],
            due_text=row["due_text"],
            due_at=row["due_at"].isoformat() if row["due_at"] else None,
        )
        for row in cur.fetchall()
    ]


def prepare_final(cur, job):
    current = load_current(cur, job)
    if current["next_chunk"] != current["total_chunks"]:
        raise Invalid("CHUNKS_NOT_COMPLETE")
    cur.execute("SELECT evidence FROM meeting_brief_chunks WHERE job_id=%s ORDER BY chunk_no", (str(job["id"]),))
    selected = select_evidence([row["evidence"] for row in cur.fetchall()])
    return selected, approved_tasks(cur, job)


def save_final(cur, job, identity, content, attempts, elapsed):
    current = load_current(cur, job)
    if current["next_chunk"] != current["total_chunks"]:
        raise Invalid("CHUNKS_NOT_COMPLETE")
    save_attempts(cur, job, attempts, None)
    brief_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO meeting_briefs(id,organization_id,meeting_id,transcript_id,job_id,method,model,model_digest,content)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            brief_id,
            job["organization_id"],
            job["meeting_id"],
            job["transcript_id"],
            str(job["id"]),
            VERSION,
            MODEL,
            identity,
            Json(content),
        ),
    )
    cur.execute(
        """UPDATE meeting_brief_jobs SET status='DONE',error_code=NULL,final_elapsed_seconds=%s,
           last_stage='DONE',updated_at=now() WHERE id=%s""",
        (elapsed, str(job["id"])),
    )
    cur.execute(
        """INSERT INTO audit_events(id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
           VALUES (%s,%s,'WORKER','MEETING_BRIEF_CREATED','MEETING_BRIEF',%s,%s)""",
        (
            str(uuid.uuid4()),
            job["organization_id"],
            brief_id,
            Json(
                {
                    "meeting_id": str(job["meeting_id"]),
                    "job_id": str(job["id"]),
                    "sources": len(content["sources"]),
                    "final_rejected": content.get("final_rejected", 0),
                    "reliability_version": RELIABILITY_VERSION,
                }
            ),
        ),
    )
    return brief_id, len(content["sources"])


def once(db=None):
    if Path("/etc/shtab-ai/storage012.maintenance").exists():
        emit("maintenance")
        return
    db = db or store()
    job = None
    attempts = []
    current_chunk = None
    with db.connection() as connection:
        with connection.cursor() as cur:
            if not take_locks(cur):
                emit("asr_or_llm_busy")
                return
        connection.commit()
        with connection.cursor() as cur:
            cur.execute(
                """SELECT j.*,t.content FROM meeting_brief_jobs j JOIN transcripts t ON t.id=j.transcript_id
                   JOIN meetings m ON m.id=j.meeting_id WHERE j.status IN ('QUEUED','RUNNING')
                   AND j.method=%s AND j.model=%s
                   AND t.organization_id=j.organization_id AND t.meeting_id=j.meeting_id
                   AND m.organization_id=j.organization_id
                   AND (NOT EXISTS (SELECT 1 FROM organization_operation_state s
                                    WHERE s.organization_id=j.organization_id AND s.paused)
                     OR EXISTS (SELECT 1 FROM operation_controls x WHERE x.kind='BRIEF'
                                AND x.entity_id=j.id AND x.desired_state='CANCELLED'))
                   AND NOT EXISTS (SELECT 1 FROM operation_controls x WHERE x.kind='BRIEF'
                                   AND x.entity_id=j.id AND x.desired_state='PAUSED')
                   ORDER BY j.created_at,j.id LIMIT 1 FOR UPDATE OF j""",
                (VERSION, MODEL),
            )
            job = cur.fetchone()
            if not job:
                emit("brief_queue_empty")
                return
            cur.execute(
                "UPDATE meeting_brief_jobs SET status='RUNNING',last_stage='STARTING',updated_at=now() WHERE id=%s",
                (str(job["id"]),),
            )
        connection.commit()
        try:
            ops_checkpoint(db, "BRIEF", str(job["id"]))
            if fingerprint(job["content"]) != job["source_hash"]:
                raise Invalid("SOURCE_CHANGED")
            work = brief_chunks(job["content"])
            if len(work) != job["total_chunks"] or not 0 <= job["next_chunk"] <= len(work):
                raise Invalid("CHUNK_PLAN_CHANGED")
            identity = model_identity()
            if job["model_digest"] and job["model_digest"] != identity:
                raise Invalid("MODEL_CHANGED")
            with connection.cursor() as cur:
                cur.execute("UPDATE meeting_brief_jobs SET model_digest=%s WHERE id=%s", (identity, str(job["id"])))
            connection.commit()

            if job["next_chunk"] < len(work):
                current_chunk = job["next_chunk"]
                emit("brief_chunk_started", job_id=str(job["id"]), chunk=current_chunk + 1, total=len(work))
                started = time.monotonic()
                raw = infer_map(work[current_chunk], attempts)
                evidence, rejected = validate_map(raw, work[current_chunk])
                ops_checkpoint(db, "BRIEF", str(job["id"]))
                if model_identity() != identity:
                    raise Invalid("MODEL_CHANGED")
                with connection.cursor() as cur:
                    save_chunk(cur, job, raw, evidence, rejected, time.monotonic() - started, identity, attempts)
                connection.commit()
                emit("brief_chunk_saved", job_id=str(job["id"]), evidence=len(evidence), rejected=rejected)
            else:
                emit("brief_final_started", job_id=str(job["id"]))
                final_started = time.monotonic()
                with connection.cursor() as cur:
                    selected, tasks = prepare_final(cur, job)
                connection.commit()
                if selected:
                    try:
                        raw = infer_final(selected, attempts)
                        content, rejected = validate_final(raw, selected)
                    except Invalid as exc:
                        reason = str(exc)
                        attempts.append(validation_attempt("FINAL_VALIDATION", reason))
                        content, rejected = fallback_final(selected, reason)
                    try:
                        narrative_raw = infer_narrative(selected, attempts)
                        narrative = validate_narrative(narrative_raw, selected)
                    except Invalid as exc:
                        reason = str(exc)
                        attempts.append(validation_attempt("NARRATIVE_VALIDATION", reason))
                        narrative = fallback_narrative(selected, reason)
                    content = attach_narrative(content, narrative, selected)
                else:
                    content = make_empty_brief()
                    content["narrative"] = empty_narrative()
                    rejected = 0
                ops_checkpoint(db, "BRIEF", str(job["id"]))
                if model_identity() != identity:
                    raise Invalid("MODEL_CHANGED")
                content.update(
                    version=VERSION,
                    reliability_version=RELIABILITY_VERSION,
                    model=MODEL,
                    model_digest=identity,
                    tasks=tasks,
                    generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    final_rejected=rejected,
                )
                elapsed = time.monotonic() - final_started
                with connection.cursor() as cur:
                    brief_id, count = save_final(cur, job, identity, content, attempts, elapsed)
                connection.commit()
                ops_finish(db, "BRIEF", str(job["id"]))
                emit("brief_saved", job_id=str(job["id"]), brief_id=brief_id, sources=count)
        except PauseRequested:
            connection.rollback()
            emit("brief_paused", job_id=str(job["id"]))
            return
        except CancelRequested:
            connection.rollback()
            with connection.cursor() as cur:
                save_attempts(cur, job, attempts, current_chunk)
                cur.execute(
                    """UPDATE meeting_brief_jobs SET status='CANCELLED',error_code='CANCELLED_BY_ADMIN',
                       last_stage='CANCELLED',updated_at=now() WHERE id=%s AND organization_id=%s""",
                    (str(job["id"]), job["organization_id"]),
                )
            connection.commit()
            emit("brief_cancelled", job_id=str(job["id"]))
            return
        except Exception as exc:
            connection.rollback()
            code = str(exc) if isinstance(exc, Invalid) else type(exc).__name__
            if not code.replace("_", "").isalnum():
                code = "MODEL_OR_DATABASE_ERROR"
            with connection.cursor() as cur:
                save_attempts(cur, job, attempts, current_chunk)
                cur.execute(
                    """UPDATE meeting_brief_jobs SET status='FAILED',error_code=%s,
                       last_stage=COALESCE(last_stage,'FAILED'),updated_at=now()
                       WHERE id=%s AND organization_id=%s""",
                    (code[:100], str(job["id"]), job["organization_id"]),
                )
            connection.commit()
            ops_fail(db, "BRIEF", str(job["id"]), code[:100])
            emit("brief_failed", job_id=str(job["id"]), error_code=code[:100])
            raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "once"))
    args = parser.parse_args()
    if args.command == "once":
        once()
        return
    with store().connection() as connection, connection.cursor() as cur:
        cur.execute("SELECT id,next_chunk,retry_count,last_stage FROM meeting_brief_jobs LIMIT 0")
        cur.execute("SELECT id,review_status FROM meeting_briefs LIMIT 0")
        cur.execute("SELECT job_id,stage,outcome FROM meeting_brief_attempts LIMIT 0")
        cur.execute("SELECT kind,entity_id FROM operation_controls LIMIT 0")
    emit("database_ok")
    emit("local_model_ok", model=MODEL, digest=model_identity(), reliability=RELIABILITY_VERSION)


if __name__ == "__main__":
    main()
