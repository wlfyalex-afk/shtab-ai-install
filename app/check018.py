#!/usr/bin/env python3
import os
from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parent
app = create_app()
routes = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
required = {
    '/admin/operations': 'ops018.page',
    '/admin/operations/maintenance': 'ops018.maintenance',
    '/admin/operations/<kind>/<uuid:entity_id>/command': 'ops018.command',
    '/admin/operations/import/<uuid:entity_id>/discard': 'ops018.discard',
}
for route, endpoint in required.items():
    if routes.get(route) != endpoint:
        raise SystemExit(f'Missing route: {route}')

with app.store.connection() as connection, connection.cursor() as cur:
    cur.execute('SELECT organization_id,paused FROM organization_operation_state LIMIT 0')
    cur.execute('SELECT kind,entity_id,organization_id,desired_state,actual_state FROM operation_controls LIMIT 0')
    cur.execute("SELECT status FROM meeting_llm_jobs WHERE status='CANCELLED' LIMIT 0")
    cur.execute("SELECT status FROM meeting_brief_jobs WHERE status='CANCELLED' LIMIT 0")

web = (ROOT / 'ops_web018.py').read_text()
if "g.user.get('is_admin')" not in web or web.count("g.user['organization_id']") < 12:
    raise SystemExit('Administrator or organization scope check is missing')
if 'systemctl' in web or 'subprocess' in web or 'sudo' in web:
    raise SystemExit('Web process must not control system services')
if not (ROOT / 'templates/operations018.html').is_file():
    raise SystemExit('Operations template is missing')

print('OPS-CONTROL-018: admin-only routes, organization scope and cooperative controls OK')
