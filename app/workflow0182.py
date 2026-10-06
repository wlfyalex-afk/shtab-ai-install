"""Meeting navigation and bounded pagination for the 018 working baseline."""
import math

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for


bp = Blueprint("workflow0182", __name__)
TRANSCRIPT_PAGE_SIZE = 50
DRAFT_PAGE_SIZE = 20
MEETING_PAGE_SIZE = 25


def page_arg(name="page"):
    try:
        value = int(request.args.get(name, "0"))
    except (TypeError, ValueError):
        abort(400)
    if not 0 <= value <= 10000:
        abort(400)
    return value


def page_count(total, size):
    return max(1, math.ceil(total / size))


def owned_meeting(cur, mid):
    cur.execute(
        "SELECT * FROM meetings WHERE id=%s AND organization_id=%s",
        (str(mid), g.user["organization_id"]),
    )
    row = cur.fetchone()
    if not row:
        abort(404)
    return row


@bp.get("/meetings/<uuid:mid>/status")
def status(mid):
    with current_app.store.connection() as connection, connection.cursor() as cur:
        owned_meeting(cur, mid)
        cur.execute(
            """SELECT id FROM meeting_imports
               WHERE meeting_id=%s AND organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (str(mid), g.user["organization_id"]),
        )
        row = cur.fetchone()
    if not row:
        abort(404)
    return redirect(url_for("imports.job", uid=row["id"]), 302)


def meetings_index():
    page = page_arg()
    with current_app.store.connection() as connection, connection.cursor() as cur:
        cur.execute(
            """SELECT m.*,
                 (SELECT count(*) FROM transcripts t
                  WHERE t.meeting_id=m.id AND t.organization_id=m.organization_id) AS transcript_count,
                 (SELECT mi.id FROM meeting_imports mi
                  WHERE mi.meeting_id=m.id AND mi.organization_id=m.organization_id
                  ORDER BY mi.created_at DESC,mi.id DESC LIMIT 1) AS import_id
               FROM meetings m WHERE m.organization_id=%s
               ORDER BY m.created_at DESC,m.id LIMIT %s OFFSET %s""",
            (g.user["organization_id"], MEETING_PAGE_SIZE + 1, page * MEETING_PAGE_SIZE),
        )
        meetings = cur.fetchall()
        cur.execute(
            "SELECT * FROM meeting_imports WHERE organization_id=%s ORDER BY created_at DESC,id LIMIT 30",
            (g.user["organization_id"],),
        )
        jobs = cur.fetchall()
        cur.execute(
            "SELECT * FROM meeting_import_groups WHERE organization_id=%s ORDER BY created_at DESC LIMIT 30",
            (g.user["organization_id"],),
        )
        groups = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM meetings WHERE organization_id=%s", (g.user["organization_id"],))
        total = cur.fetchone()["n"]
    from import_common import LABELS

    return render_template(
        "imports008.html",
        meetings=meetings[:MEETING_PAGE_SIZE],
        more=len(meetings) > MEETING_PAGE_SIZE,
        page=page,
        total_pages=page_count(total, MEETING_PAGE_SIZE),
        jobs=jobs,
        labels=LABELS,
        groups=groups,
    )


def meeting_transcript(mid):
    page = page_arg()
    with current_app.store.connection() as connection, connection.cursor() as cur:
        row = owned_meeting(cur, mid)
        cur.execute(
            """SELECT id,engine,model,language,content->'parts' AS parts,
                      jsonb_array_length(content->'segments') AS count
               FROM transcripts WHERE meeting_id=%s AND organization_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (str(mid), g.user["organization_id"]),
        )
        transcript = cur.fetchone()
        segments = []
        total_pages = 1
        if transcript:
            total_pages = page_count(transcript["count"], TRANSCRIPT_PAGE_SIZE)
            if page >= total_pages and transcript["count"]:
                abort(404)
            cur.execute(
                """SELECT value FROM transcripts t,
                     jsonb_array_elements(t.content->'segments') WITH ORDINALITY AS s(value,n)
                   WHERE t.id=%s AND t.organization_id=%s ORDER BY n LIMIT %s OFFSET %s""",
                (
                    str(transcript["id"]),
                    g.user["organization_id"],
                    TRANSCRIPT_PAGE_SIZE,
                    page * TRANSCRIPT_PAGE_SIZE,
                ),
            )
            segments = [item["value"] for item in cur.fetchall()]
    return render_template(
        "meeting008.html",
        row=row,
        transcript=transcript,
        segments=segments,
        page=page,
        page_size=TRANSCRIPT_PAGE_SIZE,
        total_pages=total_pages,
        more=page + 1 < total_pages,
    )


def meeting_drafts(mid):
    page = page_arg()
    with current_app.store.connection() as connection, connection.cursor() as cur:
        meeting = owned_meeting(cur, mid)
        cur.execute(
            "SELECT count(*) AS n FROM meeting_task_drafts WHERE meeting_id=%s AND organization_id=%s",
            (str(mid), g.user["organization_id"]),
        )
        total = cur.fetchone()["n"]
        total_pages = page_count(total, DRAFT_PAGE_SIZE)
        if page >= total_pages and total:
            abort(404)
        cur.execute(
            """SELECT * FROM meeting_task_drafts
               WHERE meeting_id=%s AND organization_id=%s
               ORDER BY start_seconds,id LIMIT %s OFFSET %s""",
            (str(mid), g.user["organization_id"], DRAFT_PAGE_SIZE, page * DRAFT_PAGE_SIZE),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT id,display_name FROM people WHERE organization_id=%s AND active ORDER BY display_name",
            (g.user["organization_id"],),
        )
        people = cur.fetchall()
    return render_template(
        "drafts009.html",
        meeting=meeting,
        rows=rows,
        people=people,
        page=page,
        page_size=DRAFT_PAGE_SIZE,
        total=total,
        total_pages=total_pages,
        more=page + 1 < total_pages,
    )


def register(app):
    app.register_blueprint(bp)
    # Keep the public endpoint names stable: existing links and access rules continue to work.
    app.view_functions["imports.index"] = meetings_index
    app.view_functions["imports.meeting"] = meeting_transcript
    app.view_functions["meeting_review.drafts"] = meeting_drafts
