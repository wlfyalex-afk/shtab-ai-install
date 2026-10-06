import json
from pathlib import Path
from store import Store
from head_dashboard import fetch_dashboard
settings=json.loads(Path('/etc/shtab-ai/secretary-web.json').read_text())
s=Store(settings)
with s.connection() as c,c.cursor() as cur:
    cur.execute('SELECT id FROM organizations ORDER BY id')
    orgs=cur.fetchall()
for org in orgs:
    fetch_dashboard(s,org['id'],None,None,'all',0)
print('dashboard database queries: OK; organisations:',len(orgs))
from app import create_app
app=create_app(settings)
with app.test_client() as client:
    assert client.get('/head').status_code==302
    login=client.get('/login')
    assert login.status_code==200
    assert login.headers['Referrer-Policy']=='same-origin'
print('login and dashboard authentication: OK')
