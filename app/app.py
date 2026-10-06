import hmac
import io
import json
import logging
import os
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Flask, abort, g, redirect, render_template, request, send_file, session, url_for, flash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from psycopg2 import Error as DatabaseError
from domain import LABELS, TASK_LABELS, Conflict, Invalid, validate_form, verified_audio
from store import Store

def create_app(settings=None, store=None):
    if settings is None:
        settings = json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
    app = Flask(__name__)
    if os.environ.get('SHTAB_SESSION_COOKIE_SECURE','0') == '1':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)
    if len(settings['secret_key']) < 32: raise ValueError('Session secret too short')
    app.config.update(SECRET_KEY=settings['secret_key'], MAX_CONTENT_LENGTH=64000,
        MAX_FORM_MEMORY_SIZE=64000, MAX_FORM_PARTS=30, SESSION_COOKIE_NAME='shtab_secretary',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_SECURE=os.environ.get('SHTAB_SESSION_COOKIE_SECURE','0') == '1', PERMANENT_SESSION_LIFETIME=timedelta(hours=4),
        TRUSTED_HOSTS=[h.strip() for h in os.environ.get('SHTAB_TRUSTED_HOSTS','127.0.0.1,localhost').split(',') if h.strip()], SESSION_REFRESH_EACH_REQUEST=False)
    app.store = store or Store(settings)
    attempts = {}; mutex = threading.Lock()
    dummy_hash = generate_password_hash(secrets.token_urlsafe(24))
    review_labels = dict(PENDING='На проверке', CONFIRMED='Подтверждено', CORRECTED='Исправлено', REJECTED='Отклонено', NOT_REQUIRED='Проверка не требуется')

    def csrf():
        if 'csrf' not in session: session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    @app.context_processor
    def common():
        return dict(csrf=csrf, labels=LABELS, task_labels=TASK_LABELS, review_labels=review_labels)

    @app.template_filter('localtime')
    def localtime(value, tz=None):
        if not value: return 'Не указано'
        if isinstance(value,str):
            from datetime import datetime
            value = datetime.fromisoformat(value)
        return value.astimezone(ZoneInfo(tz or (g.user['timezone'] if g.get('user') else 'Asia/Vladivostok'))).strftime('%d.%m.%Y %H:%M')

    @app.before_request
    def guard():
        if request.endpoint == 'imports.chunk':
            request.max_content_length = 2 * 1024 * 1024
        if request.routing_exception is not None:
            raise request.routing_exception
        if request.method == 'POST':
            expected = session.get('csrf', '')
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf','')
            if not expected or not hmac.compare_digest(expected, token): abort(400)
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'): abort(403)
        g.user = None
        if session.get('uid'):
            actor = app.store.user(user_id=session['uid'])
            if actor and actor['session_version'] == session.get('sv'): g.user = actor
            else: session.clear()
        if g.user:
            from organizations0102 import apply_scope
            apply_scope()
        if request.endpoint not in ('login','static') and not g.user:
            return redirect(url_for('login'))

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; img-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        return response

    @app.route('/login', methods=['GET','POST'])
    def login():
        error = None
        if request.method == 'POST':
            now = time.monotonic(); key = request.remote_addr or 'local'
            with mutex:
                old = [t for t in attempts.get(key, []) if now-t < 300]
                attempts[key] = old
                if len(old) >= 10:
                    return render_template('login.html', error='Слишком много попыток. Повторите через 5 минут.'),429
                old.append(now)
            actor = app.store.user(username=request.form.get('username','').strip().lower()[:100])
            valid = check_password_hash(actor['password_hash'] if actor else dummy_hash, request.form.get('password',''))
            if actor and valid:
                with mutex: attempts.pop(key, None)
                session.clear(); session.permanent = True
                session.update(uid=str(actor['id']),sv=actor['session_version'],csrf=secrets.token_urlsafe(32))
                return redirect(url_for('queue'))
            error = 'Неверный логин или пароль.'
        return render_template('login.html',error=error)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    def queue():
        mode = request.args.get('view','all')
        if mode not in ('pending','reviewed','all'): abort(400)
        try:
            page = int(request.args.get('page','0'))
            if not 0 <= page <= 10000: raise ValueError()
        except ValueError: abort(400)
        counts, rows, more = app.store.queue(g.user['organization_id'],mode,page)
        return render_template('queue.html',counts=counts,rows=rows,mode=mode,page=page,more=more)

    def get_row(rid):
        row = app.store.detail(str(rid),g.user['organization_id'])
        if not row: abort(404)
        return row

    @app.get('/responses/<uuid:rid>')
    def detail(rid):
        row = get_row(rid)
        result = row['reviewed_result'] or dict(classification=row['classification'],
            corrected_transcript=row['transcript'],reason=row['reason_transcript'] or '',
            due_text=row['promised_due_text'] or '',due_at=row['promised_due_at'],comment='')
        due = result.get('due_at')
        if isinstance(due,str):
            from datetime import datetime
            due = datetime.fromisoformat(due)
        due_input = due.astimezone(ZoneInfo(row['org_timezone'])).strftime('%Y-%m-%dT%H:%M') if due else ''
        audio_error = None
        try: verified_audio(row, settings['audio_roots'])
        except Invalid as exc: audio_error = str(exc)
        return render_template('detail.html',row=row,result=result,due_input=due_input,audio_error=audio_error,
            history=app.store.history(str(rid),g.user['organization_id']))

    @app.get('/responses/<uuid:rid>/audio')
    def audio(rid):
        row = get_row(rid)
        data = verified_audio(row,settings['audio_roots'])
        return send_file(io.BytesIO(data), mimetype='audio/wav',download_name='answer.wav',conditional=True,
                         etag=row['audio_sha256'].strip(),max_age=0)

    @app.post('/responses/<uuid:rid>/review')
    def review(rid):
        row = get_row(rid)
        decision, version = validate_form(request.form,row['org_timezone'])
        if decision['action'] != 'REJECT': verified_audio(row,settings['audio_roots'])
        app.store.review(str(rid),g.user,decision,version,request.form.get('task_updated_at',''))
        flash('Решение сохранено. Исходная запись и расшифровка сохранены без изменений.')
        return redirect(url_for('detail',rid=rid),code=303)

    @app.errorhandler(Invalid)
    def invalid(exc): return render_template('error.html',message=str(exc)),400

    @app.errorhandler(Conflict)
    def conflict(exc): return render_template('error.html',message=str(exc)),409

    @app.errorhandler(DatabaseError)
    def db_error(exc):
        logging.error('Secretary database error: %s',type(exc).__name__)
        return render_template('error.html',message='Операция с БД не выполнена. Обновите страницу; если ошибка повторится, проверьте журнал службы.'),503

    @app.errorhandler(404)
    @app.errorhandler(403)
    @app.errorhandler(400)
    @app.errorhandler(413)
    def http_error(exc):
        return 'Страница недоступна или запрос некорректен. Обновите страницу.',exc.code,{'Content-Type':'text/plain; charset=utf-8'}

    from head_dashboard import register_head_dashboard
    register_head_dashboard(app)
    from meeting_import import register_meeting_import
    register_meeting_import(app)
    from user_admin010 import register
    register(app)
    from head_access0101 import register as register_roles
    register_roles(app)
    from organizations0102 import register as register_organizations
    register_organizations(app)
    from llm_web013 import register as register_llm
    register_llm(app)
    from call_assist016 import register as register_calls
    register_calls(app)
    from brief_web017 import register as register_brief
    register_brief(app)
    from ops_web018 import register as register_ops
    register_ops(app)
    from response_integration0181 import register as register_response_integration
    register_response_integration(app)
    from workflow0182 import register as register_workflow0182
    register_workflow0182(app)
    from automation0183 import register as register_automation0183
    register_automation0183(app)
    from export0183 import register as register_export0183
    register_export0183(app)
    from comments_reports0184 import register as register_comments_reports0184
    register_comments_reports0184(app)
    return app
