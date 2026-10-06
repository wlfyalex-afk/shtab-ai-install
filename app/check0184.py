#!/usr/bin/env python3
import json
import os
from pathlib import Path

from app import create_app
from comments_reports0184 import report_zip
from store import Store


config = json.loads(Path(os.environ.get("SHTAB_WEB_CONFIG", "/etc/shtab-ai/secretary-web.json")).read_text())
app = create_app(settings=config)
if app.view_functions["response_integration0181.task_detail"].__module__ != "comments_reports0184":
    raise SystemExit("Task comments are not active")
if app.view_functions["imports.export"].__module__ != "comments_reports0184":
    raise SystemExit("PDF/XLSX meeting export is not active")
for endpoint in ("reports0184.add_comment", "reports0184.period"):
    if endpoint not in app.view_functions:
        raise SystemExit("Missing endpoint: " + endpoint)
for name in ("task_detail0184.html", "report_period0184.html", "imports008.html", "head_meetings0101.html", "operations018.html"):
    app.jinja_env.get_template(name)
if "i.meeting_id,i.processed_seconds" not in Path(__file__).with_name("ops_web018.py").read_text():
    raise SystemExit("Duplicate operation link is not active")
if "    recognition_status = (" not in Path(__file__).with_name("automation0183.py").read_text():
    raise SystemExit("Duplicate recognition status is not active")
with Store(config).connection() as connection, connection.cursor() as cur:
    cur.execute("SELECT author_role,body FROM task_comments LIMIT 0")
sample = {"title": "Проверка", "subtitle": "Установщик", "timezone": "UTC", "generated_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc), "meetings": [], "tasks": [], "responses": [], "comments": []}
blob = report_zip(sample)
if not blob.startswith(b"PK") or len(blob) < 1000:
    raise SystemExit("Report ZIP self-test failed")
print("COMMENTS-REPORTS-0184: OK")
