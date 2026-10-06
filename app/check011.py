import json,os
from pathlib import Path
from store import Store
cfg=json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
with Store(cfg).connection() as c,c.cursor() as cur:
    cur.execute('SELECT upload_started_at,upload_observed_bytes FROM meeting_imports LIMIT 0')
    cur.execute('SELECT run_id,elapsed_seconds FROM meeting_import_metrics LIMIT 0')
from app import create_app
app=create_app(cfg)
for name in ('meeting008.html','drafts009.html','job008.html'):app.jinja_env.get_template(name)
print('metrics schema and templates: OK')
