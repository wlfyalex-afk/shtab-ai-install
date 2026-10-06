import json,os
from pathlib import Path
from store import Store
cfg=json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
with Store(cfg).connection() as c,c.cursor() as cur:
    cur.execute('SELECT count(*) AS n FROM secretary_users WHERE is_admin')
    if cur.fetchone()['n']!=1:raise SystemExit('Expected exactly one administrator')
    cur.execute('SELECT id,name,timezone FROM organizations LIMIT 0')
from app import create_app
app=create_app(cfg)
for name in ('organizations0102.html','transfer0102.html','users010.html','base.html'):app.jinja_env.get_template(name)
print('organizations schema and templates: OK')
