"""Single administrator; tenant scope is resolved server-side on every request."""
import secrets,uuid
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from flask import Blueprint,abort,current_app,g,request,session,redirect,url_for,render_template,flash
from psycopg2.extras import Json
bp=Blueprint('organizations',__name__)

def require_admin():
    if not g.get('user') or not g.user.get('is_admin'):abort(403)

def admin_lock(cur):
    require_admin()
    cur.execute('''SELECT u.id FROM secretary_users u JOIN people p ON p.id=u.person_id
      WHERE u.id=%s AND u.is_admin AND u.active AND p.active AND u.session_version=%s FOR UPDATE OF u''',
      (str(g.user['id']),g.user['session_version']))
    if not cur.fetchone():abort(403)

def event(cur,org,kind,entity,uid,payload):
    cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'USER',%s,%s,%s,%s,%s)''',(str(uuid.uuid4()),str(org),g.user['person_id'],kind,entity,str(uid),Json(payload)))

def apply_scope():
    if not g.get('user'):return
    actor=dict(g.user);home=str(actor['organization_id'])
    selected=session.get('active_organization') if actor.get('is_admin') else None
    try:org=str(uuid.UUID(selected)) if selected else home
    except (ValueError,TypeError,AttributeError):abort(403)
    with current_app.store.connection() as c,c.cursor() as cur:
        cur.execute('SELECT id,name,timezone FROM organizations WHERE id=%s',(org,));row=cur.fetchone()
    if not row:abort(403)
    actor.update(home_organization_id=home,organization_id=row['id'],organization_name=row['name'],timezone=row['timezone'])
    g.user=actor

@bp.get('/admin/organizations')
def index():
    require_admin()
    with current_app.store.connection() as c,c.cursor() as cur:
        cur.execute('''SELECT o.*, (SELECT count(*) FROM secretary_users u JOIN people p ON p.id=u.person_id WHERE p.organization_id=o.id) AS users
          FROM organizations o ORDER BY o.name,o.id''');rows=cur.fetchall()
    return render_template('organizations0102.html',rows=rows)

@bp.post('/admin/organizations')
def create():
    require_admin();name=request.form.get('name','').strip();tz=request.form.get('timezone','Asia/Vladivostok').strip()
    if not 1<=len(name)<=200 or len(tz)>100:abort(400)
    try:ZoneInfo(tz)
    except (ZoneInfoNotFoundError,ValueError):abort(400)
    with current_app.store.connection() as c,c.cursor() as cur:
        admin_lock(cur)
        cur.execute('SELECT id FROM organizations WHERE lower(btrim(name))=lower(%s)',(name,))
        if cur.fetchone():flash('Организация с таким названием уже существует.');return redirect(url_for('organizations.index'),303)
        uid=str(uuid.uuid4())
        cur.execute('INSERT INTO organizations(id,name,timezone) VALUES (%s,%s,%s)',(uid,name,tz))
        event(cur,uid,'ORGANIZATION_CREATED','ORGANIZATION',uid,{})
    flash('Организация создана. Выберите её, чтобы добавить пользователей.')
    return redirect(url_for('organizations.index'),303)

@bp.post('/admin/organizations/<uuid:oid>/select')
def select(oid):
    require_admin()
    with current_app.store.connection() as c,c.cursor() as cur:
        admin_lock(cur)
        cur.execute('SELECT id FROM organizations WHERE id=%s',(str(oid),))
        if not cur.fetchone():abort(404)
    session['active_organization']=str(oid)
    # Old tabs/forms must not accidentally modify the newly selected company.
    session['csrf']=secrets.token_urlsafe(32)
    return redirect(url_for('user_admin.users'),303)

@bp.get('/admin/users/<uuid:uid>/transfer')
def transfer_form(uid):
    require_admin()
    try:destination=str(uuid.UUID(request.args['destination'])) if request.args.get('destination') else None
    except ValueError:abort(400)
    with current_app.store.connection() as c,c.cursor() as cur:
        cur.execute('''SELECT u.id,u.username,u.session_version,u.is_admin,p.display_name FROM secretary_users u JOIN people p ON p.id=u.person_id
          WHERE u.id=%s AND p.organization_id=%s''',(str(uid),g.user['organization_id']));row=cur.fetchone()
        if not row:abort(404)
        if row['is_admin']:abort(403)
        cur.execute('SELECT id,name FROM organizations WHERE id<>%s ORDER BY name,id',(g.user['organization_id'],));organizations=cur.fetchall()
        if destination and destination not in [str(o['id']) for o in organizations]:abort(400)
        people=[]
        if destination:
            cur.execute('SELECT id,display_name FROM people WHERE organization_id=%s AND active ORDER BY display_name,id',(destination,));people=cur.fetchall()
    return render_template('transfer0102.html',row=row,organizations=organizations,destination=destination,people=people)

@bp.post('/admin/users/<uuid:uid>/transfer')
def transfer(uid):
    require_admin()
    try:dest=str(uuid.UUID(request.form['destination']));version=int(request.form['version'])
    except (KeyError,ValueError):abort(400)
    if dest==str(g.user['organization_id']) or request.form.get('confirmed')!='yes':abort(400)
    person=request.form.get('person','')
    if person:
        try:person=str(uuid.UUID(person))
        except ValueError:abort(400)
    with current_app.store.connection() as c,c.cursor() as cur:
        admin_lock(cur)
        cur.execute('''SELECT u.*,p.display_name FROM secretary_users u JOIN people p ON p.id=u.person_id
          WHERE u.id=%s AND p.organization_id=%s FOR UPDATE OF u''',(str(uid),g.user['organization_id']));row=cur.fetchone()
        if not row:abort(404)
        if row['is_admin']:abort(403)
        if row['session_version']!=version:abort(409)
        cur.execute('SELECT id,timezone FROM organizations WHERE id=%s',(dest,));organization=cur.fetchone()
        if not organization:abort(400)
        if person:
            cur.execute('SELECT id FROM people WHERE id=%s AND organization_id=%s AND active',(person,dest))
            if not cur.fetchone():abort(400)
        else:
            person=str(uuid.uuid4())
            cur.execute('INSERT INTO people(id,organization_id,display_name,timezone) VALUES (%s,%s,%s,%s)',(person,dest,row['display_name'],organization['timezone']))
        cur.execute('UPDATE secretary_users SET person_id=%s,session_version=session_version+1 WHERE id=%s',(person,str(uid)))
        payload=dict(from_organization=str(g.user['organization_id']),to_organization=dest,old_person=str(row['person_id']),new_person=person)
        for org in (g.user['organization_id'],dest):event(cur,org,'WEB_USER_TRANSFERRED','WEB_USER',uid,payload)
    flash('Учётная запись перенесена. Пароль и роль сохранены, прежние сеансы отозваны. История и звонки остались в исходной организации.')
    return redirect(url_for('user_admin.users'),303)

def register(app):app.register_blueprint(bp)
