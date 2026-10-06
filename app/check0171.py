#!/usr/bin/env python3
from pathlib import Path

from app import create_app


ROOT = Path("/opt/shtab-ai/secretary-web-006")
app = create_app()
paths = {rule.rule for rule in app.url_map.iter_rules()}
required = {
    "/admin/users",
    "/admin/contacts",
    "/admin/contacts/<uuid:person_id>/add",
    "/admin/contacts/<uuid:contact_id>/state",
}
if missing := required - paths:
    raise SystemExit("Missing routes: " + ", ".join(sorted(missing)))

with app.store.connection() as connection, connection.cursor() as cursor:
    cursor.execute(
        """SELECT p.id,u.id,cp.id FROM people p
        LEFT JOIN secretary_users u ON u.person_id=p.id
        LEFT JOIN contact_points cp
          ON cp.person_id=p.id AND cp.organization_id=p.organization_id
        WHERE p.organization_id=%s LIMIT 0""",
        ("00000000-0000-0000-0000-000000000000",),
    )

base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
users = (ROOT / "templates/users010.html").read_text(encoding="utf-8")
calls = (ROOT / "call_assist016.py").read_text(encoding="utf-8")
admin = (ROOT / "user_admin010.py").read_text(encoding="utf-8")
style = (ROOT / "static/style.css").read_text(encoding="utf-8")

if "url_for('call_assist.contacts')" in base or ">Телефоны</a>" in base:
    raise SystemExit("Separate phone navigation remains")
for token in ("Телефоны и SIP", "call_assist.contact_add", "call_assist.contact_state"):
    if token not in users:
        raise SystemExit("Missing unified user/contact marker: " + token)
for token in (
    "p.organization_id=%s",
    "cp.organization_id=p.organization_id",
    "render_template('users010.html'",
):
    if token not in admin:
        raise SystemExit("Missing organization scope marker: " + token)
if "redirect(url_for('user_admin.users')" not in calls:
    raise SystemExit("Legacy contacts page does not redirect to users")
for token in (
    "UI-FIX-017.1: compact native date/time controls",
    'input[type="datetime-local"]',
    "width: 20rem !important",
    "@media (max-width: 480px)",
):
    if token not in style:
        raise SystemExit("Missing compact date/time CSS marker: " + token)

app.jinja_env.get_template("users010.html")
app.jinja_env.get_template("group_new0082.html")
print("UI-FIX-017.1: compact calendar and unified users/phones OK")
