import json,os
from pathlib import Path
from store import Store
cfg=json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
with Store(cfg).connection() as c,c.cursor() as cur:
    cur.execute('SELECT is_admin,app_role FROM secretary_users LIMIT 0')
from app import create_app
app=create_app(cfg)
for name in ('users010.html','detail.html','meeting008.html','head_meetings0101.html','base.html'):
    app.jinja_env.get_template(name)
print('head role schema and templates: OK')
