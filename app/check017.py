#!/usr/bin/env python3
from pathlib import Path

from app import create_app
from brief_core017 import MODEL, model_identity

ROOT = Path('/opt/shtab-ai/secretary-web-006')
app = create_app()
rules = {r.rule for r in app.url_map.iter_rules()}
required = {
    '/meetings/<uuid:mid>/brief',
    '/meetings/<uuid:mid>/brief/queue',
    '/meetings/<uuid:mid>/brief/jobs/<uuid:jid>/retry',
    '/meetings/<uuid:mid>/brief/<uuid:bid>/approve',
}
missing = required - rules
if missing:
    raise SystemExit('Missing routes: ' + ', '.join(sorted(missing)))
with app.store.connection() as c, c.cursor() as cur:
    cur.execute('SELECT id,organization_id,status,next_chunk,total_chunks FROM meeting_brief_jobs LIMIT 0')
    cur.execute('SELECT id,organization_id,review_status,approved_by,approved_at FROM meeting_briefs LIMIT 0')
web = (ROOT/'brief_web017.py').read_text()
template = (ROOT/'templates/brief017.html').read_text()
meeting = (ROOT/'templates/meeting008.html').read_text()
for token in ('organization_id=%s', "review_status='APPROVED'", "MEETING_BRIEF_APPROVED"):
    if token not in web:
        raise SystemExit('Missing web safety marker: ' + token)
for token in ('data-audio-start', 'Я проверил пункты', 'Утверждённые поручения'):
    if token not in template:
        raise SystemExit('Missing template marker: ' + token)
if "url_for('brief017.page'" not in meeting:
    raise SystemExit('Meeting page has no brief link')
digest = model_identity()
print(f'MEETING-BRIEF-017: database, {MODEL} ({digest[:12]}), organization scope, evidence links and human approval OK')
