import re
import uuid

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from psycopg2 import IntegrityError
from psycopg2.extras import Json
from werkzeug.security import generate_password_hash

from call_assist_core016 import KINDS

bp = Blueprint('user_admin', __name__)


def admin():
    if not g.user or not g.user.get('is_admin'):
        abort(403)


def password():
    value = request.form.get('password', '')
    if not 14 <= len(value) <= 200 or value != request.form.get('confirm', ''):
        raise ValueError('Пароли должны совпадать и содержать от 14 до 200 символов.')
    return generate_password_hash(value)


def audit(cur, event, target, payload):
    cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'USER',%s,%s,'WEB_USER',%s,%s)''',
      (str(uuid.uuid4()), g.user['organization_id'], g.user['person_id'], event, target, Json(payload)))


def locked_admin(cur):
    from organizations0102 import admin_lock
    admin_lock(cur)
    cur.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE', (g.user['organization_id'],))


@bp.get('/admin/users')
def users():
    admin()
    with current_app.store.connection() as c, c.cursor() as cur:
        cur.execute('''SELECT p.id AS person_id,p.display_name,p.active AS person_active,p.role_title,
          u.id AS user_id,u.username,u.active,u.is_admin,u.app_role,u.session_version,u.created_at,
          COALESCE(jsonb_agg(jsonb_build_object('id',cp.id,'kind',cp.kind,'label',cp.label,
            'value',cp.value,'priority',cp.priority,'enabled',cp.enabled)
            ORDER BY cp.priority,cp.created_at) FILTER (WHERE cp.id IS NOT NULL),'[]'::jsonb) AS contacts
          FROM people p
          LEFT JOIN secretary_users u ON u.person_id=p.id
          LEFT JOIN contact_points cp ON cp.person_id=p.id AND cp.organization_id=p.organization_id
          WHERE p.organization_id=%s
          GROUP BY p.id,u.id
          ORDER BY (u.id IS NULL),p.display_name,p.id''', (g.user['organization_id'],))
        rows = cur.fetchall()
        cur.execute('''SELECT p.id,p.display_name FROM people p WHERE p.organization_id=%s AND p.active
          AND NOT EXISTS (SELECT 1 FROM secretary_users u WHERE u.person_id=p.id)
          ORDER BY p.display_name,p.id''', (g.user['organization_id'],))
        people = cur.fetchall()
    return render_template('users010.html', rows=rows, people=people, kinds=KINDS)


@bp.post('/admin/users/create')
def create():
    admin()
    username = request.form.get('username', '').strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9_-]{2,39}', username):
        flash('Логин: 3–40 символов, первая буква латинская; далее буквы, цифры, _ или -.')
        return redirect(url_for('user_admin.users'), 303)
    try:
        hashed = password()
    except ValueError as exc:
        flash(str(exc)); return redirect(url_for('user_admin.users'), 303)
    role = request.form.get('role', 'secretary')
    if role not in ('secretary', 'head'):
        abort(400)
    person = request.form.get('person', '')
    name = request.form.get('display_name', '').strip()
    role_title = request.form.get('role_title', '').strip()
    if len(role_title) > 200:
        flash('Должность: не более 200 символов.')
        return redirect(url_for('user_admin.users'), 303)
    if not person and not 1 <= len(name) <= 200:
        flash('Выберите сотрудника или введите имя нового.')
        return redirect(url_for('user_admin.users'), 303)
    try:
        with current_app.store.connection() as c, c.cursor() as cur:
            locked_admin(cur)
            if person:
                try:
                    person = str(uuid.UUID(person))
                except ValueError:
                    abort(400)
                cur.execute('''SELECT p.id FROM people p WHERE p.id=%s AND p.organization_id=%s AND p.active
                  AND NOT EXISTS (SELECT 1 FROM secretary_users u WHERE u.person_id=p.id)''',
                  (person, g.user['organization_id']))
                if not cur.fetchone():
                    abort(400)
            else:
                person = str(uuid.uuid4())
                cur.execute('INSERT INTO people(id,organization_id,display_name,timezone,role_title) VALUES (%s,%s,%s,%s,%s)',
                            (person, g.user['organization_id'], name, g.user['timezone'], role_title or None))
            uid = str(uuid.uuid4())
            cur.execute('''INSERT INTO secretary_users(id,username,person_id,password_hash,is_admin,app_role)
              VALUES (%s,%s,%s,%s,false,%s)''',
              (uid, username, person, hashed, 'head' if role == 'head' else 'secretary'))
            audit(cur, 'WEB_USER_CREATED', uid, {'role': role})
    except IntegrityError:
        flash('Учётная запись не создана: проверьте, не занят ли логин.')
        return redirect(url_for('user_admin.users'), 303)
    flash('Учётная запись создана. Телефоны добавляются в этой же карточке пользователя.')
    return redirect(url_for('user_admin.users') + '#person-' + person, 303)


@bp.post('/admin/people/<uuid:person_id>/position')
def position(person_id):
    admin()
    title = request.form.get('role_title', '').strip()
    previous = request.form.get('previous_role_title')
    anchor = '#person-' + str(person_id)
    if previous is None:
        abort(400)
    if len(title) > 200:
        flash('Должность: не более 200 символов.')
        return redirect(url_for('user_admin.users') + anchor, 303)
    with current_app.store.connection() as c, c.cursor() as cur:
        locked_admin(cur)
        cur.execute('SELECT role_title FROM people WHERE id=%s AND organization_id=%s FOR UPDATE',
                    (str(person_id), g.user['organization_id']))
        person = cur.fetchone()
        if not person:
            abort(404)
        if (person['role_title'] or '') != previous:
            flash('Должность уже изменена. Обновите список и повторите действие.')
            return redirect(url_for('user_admin.users') + anchor, 303)
        if (person['role_title'] or '') != title:
            cur.execute('UPDATE people SET role_title=%s WHERE id=%s AND organization_id=%s',
                        (title or None, str(person_id), g.user['organization_id']))
            from organizations0102 import event
            event(cur, g.user['organization_id'], 'PERSON_POSITION_CHANGED', 'PERSON', person_id,
                  {'previous_role_title': person['role_title'], 'role_title': title or None})
    flash('Должность сохранена.')
    return redirect(url_for('user_admin.users') + anchor, 303)


@bp.post('/admin/users/<uuid:uid>')
def change(uid):
    admin()
    action = request.form.get('action')
    if action not in ('enable', 'disable', 'secretary', 'head', 'password'):
        abort(400)
    try:
        version = int(request.form['version'])
    except (KeyError, ValueError):
        abort(400)
    hashed = None
    if action == 'password':
        try:
            hashed = password()
        except ValueError as exc:
            flash(str(exc)); return redirect(url_for('user_admin.users'), 303)
    with current_app.store.connection() as c, c.cursor() as cur:
        locked_admin(cur)
        cur.execute('''SELECT u.id,u.active,u.is_admin,u.app_role,u.session_version,p.active AS person_active,p.id AS person_id
          FROM secretary_users u JOIN people p ON p.id=u.person_id
          WHERE u.id=%s AND p.organization_id=%s FOR UPDATE OF u''',
          (str(uid), g.user['organization_id']))
        target = cur.fetchone()
        if not target:
            abort(404)
        anchor = '#person-' + str(target['person_id'])
        if target['session_version'] != version:
            flash('Данные уже изменены. Обновите список и повторите действие.')
            return redirect(url_for('user_admin.users') + anchor, 303)
        if action == 'disable' and str(uid) == str(g.user['id']):
            flash('Собственную учётную запись отключить нельзя.')
            return redirect(url_for('user_admin.users') + anchor, 303)
        if target['is_admin'] and action != 'password':
            abort(403)
        if action == 'enable' and not target['person_active']:
            flash('Сотрудник неактивен в справочнике.')
            return redirect(url_for('user_admin.users') + anchor, 303)
        if action == 'password':
            cur.execute('UPDATE secretary_users SET password_hash=%s,session_version=session_version+1 WHERE id=%s',
                        (hashed, str(uid)))
        elif action in ('secretary', 'head'):
            cur.execute('''UPDATE secretary_users SET is_admin=false,app_role=%s,
              session_version=session_version+1 WHERE id=%s''', (action, str(uid)))
        else:
            cur.execute('UPDATE secretary_users SET active=%s,session_version=session_version+1 WHERE id=%s',
                        (action == 'enable', str(uid)))
        audit(cur, 'WEB_USER_CHANGED', str(uid), {'action': action})
    if str(uid) == str(g.user['id']):
        session.clear(); return redirect(url_for('login'), 303)
    flash('Изменения сохранены. Ранее открытые сеансы пользователя отозваны.')
    return redirect(url_for('user_admin.users') + anchor, 303)


def register(app):
    app.register_blueprint(bp)
