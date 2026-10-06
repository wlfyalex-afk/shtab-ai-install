"""Idempotent transitions for the automatic meeting-processing pipeline."""
import uuid

from psycopg2.extras import Json

from brief_core017 import MODEL, VERSION, brief_chunks, fingerprint


def enqueue_brief(cur, extraction_job):
    """Queue one automatic brief after extraction completes.

    Any existing brief job for the same transcript and method wins. A human can
    still explicitly create a later version from the status page.
    """
    work = brief_chunks(extraction_job["content"])
    if not work:
        return None
    job_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO meeting_brief_jobs
             (id,organization_id,meeting_id,transcript_id,method,model,source_hash,total_chunks,created_by)
           SELECT %s,%s,%s,%s,%s,%s,%s,%s,NULL
           WHERE NOT EXISTS (
             SELECT 1 FROM meeting_brief_jobs
             WHERE transcript_id=%s AND method=%s
           )
           RETURNING id""",
        (
            job_id,
            extraction_job["organization_id"],
            extraction_job["meeting_id"],
            extraction_job["transcript_id"],
            VERSION,
            MODEL,
            fingerprint(extraction_job["content"]),
            len(work),
            extraction_job["transcript_id"],
            VERSION,
        ),
    )
    created = cur.fetchone()
    if not created:
        return None
    cur.execute(
        """INSERT INTO audit_events
             (id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
           VALUES (%s,%s,'WORKER','MEETING_BRIEF_AUTO_QUEUED','MEETING_BRIEF_JOB',%s,%s)""",
        (
            str(uuid.uuid4()),
            extraction_job["organization_id"],
            job_id,
            Json(
                {
                    "meeting_id": str(extraction_job["meeting_id"]),
                    "transcript_id": str(extraction_job["transcript_id"]),
                    "after_extraction_job": str(extraction_job["id"]),
                }
            ),
        ),
    )
    return job_id
