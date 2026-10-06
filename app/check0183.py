#!/usr/bin/env python3
import json
import os
from pathlib import Path

from app import create_app
from store import Store


config = json.loads(Path(os.environ.get("SHTAB_WEB_CONFIG", "/etc/shtab-ai/secretary-web.json")).read_text())
app = create_app(settings=config)

if app.view_functions["workflow0182.status"].__module__ != "automation0183":
    raise SystemExit("Unified meeting overview is not active")
if app.view_functions["brief017.page"].__module__ != "automation0183":
    raise SystemExit("Legacy brief page does not redirect to overview")
# 0184 replaces the ZIP handler with the extended report archive.
# Both known handlers are valid; unrelated replacements must still fail.
export_view = app.view_functions["imports.export"]
export_handler = (export_view.__module__, export_view.__name__)
if export_handler not in {
    ("export0183", "export_with_package"),
    ("comments_reports0184", "export_report0184"),
}:
    raise SystemExit(f"Meeting ZIP export is not active: {export_handler}")
if "duration0183" not in app.jinja_env.filters:
    raise SystemExit("Duration formatter is not registered")
for name in ("meeting_status0183.html", "meeting008.html", "imports008.html", "_meeting_nav0182.html"):
    app.jinja_env.get_template(name)

with Store(config).connection() as connection, connection.cursor() as cur:
    cur.execute("SELECT final_elapsed_seconds FROM meeting_brief_jobs LIMIT 0")
    cur.execute("SELECT elapsed_seconds FROM meeting_llm_chunks LIMIT 0")
    cur.execute("SELECT elapsed_seconds,stage FROM meeting_import_metrics LIMIT 0")

print("AUTO-PIPELINE-0183: OK")
