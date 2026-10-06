#!/usr/bin/env python3
import json
import os
from pathlib import Path

from app import create_app
from store import Store


config_path = Path(os.environ.get("SHTAB_WEB_CONFIG", "/etc/shtab-ai/secretary-web.json"))
settings = json.loads(config_path.read_text())
app = create_app(settings=settings)

required = {
    "workflow0182.status",
    "imports.index",
    "imports.meeting",
    "meeting_review.drafts",
    "brief017.page",
}
missing = required - set(app.view_functions)
if missing:
    raise SystemExit("Missing endpoints: " + ", ".join(sorted(missing)))
if app.view_functions["imports.meeting"].__module__ != "workflow0182":
    raise SystemExit("Transcript pagination override is not active")
if app.view_functions["meeting_review.drafts"].__module__ != "workflow0182":
    raise SystemExit("Task pagination override is not active")

for name in (
    "imports008.html",
    "meeting008.html",
    "job008.html",
    "drafts009.html",
    "brief017.html",
    "head007.html",
):
    app.jinja_env.get_template(name)

db = Store(settings)
with db.connection() as connection, connection.cursor() as cur:
    cur.execute("SELECT id,meeting_id,status FROM meeting_imports LIMIT 0")
    cur.execute("SELECT id,meeting_id,status FROM meeting_task_drafts LIMIT 0")
    cur.execute("SELECT id,meeting_id,content FROM meeting_briefs LIMIT 0")

print("WORKFLOW-POLISH-0182: OK")
