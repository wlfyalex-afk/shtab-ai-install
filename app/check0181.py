#!/usr/bin/env python3
from pathlib import Path

from app import create_app


root = Path(__file__).resolve().parent
app = create_app()
routes = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
required = {
    '/manual-responses/<uuid:session_id>': 'response_integration0181.manual_detail',
    '/tasks/<uuid:task_id>': 'response_integration0181.task_detail',
}
for route, endpoint in required.items():
    if routes.get(route) != endpoint:
        raise SystemExit('Missing route: ' + route)

for name in ('queue.html', 'head007.html', 'manual_response0181.html', 'task_detail0181.html'):
    app.jinja_env.get_template(name)

with app.store.connection() as c, c.cursor() as cur:
    cur.execute('''SELECT s.id,s.organization_id,s.task_id,s.status,s.outcome,s.reported_status,
      s.note,s.promised_due_text,s.finished_at FROM manual_call_sessions s LIMIT 0''')
    cur.execute('''SELECT r.id,r.organization_id,r.task_id,r.review_status
      FROM task_responses r LIMIT 0''')

module = (root / 'response_integration0181.py').read_text()
if "s.organization_id=%s" not in module or "t.organization_id=%s" not in module:
    raise SystemExit('Organization scope check is missing')
if 'manual_call_sessions' not in module or 'task_responses' not in module:
    raise SystemExit('Both result sources must be present')

print('RESPONSE-INTEGRATION-0181: consolidated results and read-only task cards OK')
