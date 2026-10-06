import json,os,sys
from pathlib import Path
sys.path.insert(0,'/opt/shtab-ai/secretary-web-006')
from app import create_app
from store import Store

cfg=json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
app=create_app(cfg,Store(cfg))
rules=list(app.url_map.iter_rules())
paths={r.rule for r in rules}
required={'/calls','/calls/<uuid:task_id>/prepare','/calls/session/<uuid:session_id>',
          '/calls/session/<uuid:session_id>/finish','/admin/contacts'}
assert required <= paths, required - paths
with app.store.connection() as c,c.cursor() as cur:
    cur.execute('SELECT id,status,outcome,reported_status FROM manual_call_sessions LIMIT 0')
    cur.execute('SELECT bool_and(NOT allow_automated_calls) AS safe FROM contact_points')
    row=cur.fetchone()
    assert row['safe'] is not False
for name in ('calls016.html','call_ready016.html','contacts016.html'):
    app.jinja_env.get_template(name)
print('SECRETARY-CALL-ASSIST-016: manual calls, contacts, organization scope and autodial block OK')
