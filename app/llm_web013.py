from flask import Blueprint,current_app,g,abort,request,redirect,url_for,render_template,flash
from meeting_review009 import owned,latest
from llm_queue013 import enqueue
from llm_core013 import Invalid,VERSION
bp=Blueprint('llm013',__name__)
def allowed():
    if not g.get('user') or (not g.user.get('is_admin') and g.user.get('app_role')!='secretary'):abort(403)
@bp.get('/meetings/<uuid:mid>/llm')
def page(mid):
    allowed()
    with current_app.store.connection() as c,c.cursor() as cur:
        meeting=owned(cur,mid)
        cur.execute('SELECT * FROM meeting_llm_jobs WHERE meeting_id=%s AND organization_id=%s ORDER BY created_at DESC LIMIT 20',(str(mid),g.user['organization_id']));jobs=cur.fetchall()
    return render_template('llm013.html',meeting=meeting,jobs=jobs)
@bp.post('/meetings/<uuid:mid>/llm')
def queue(mid):
    allowed()
    with current_app.store.connection() as c,c.cursor() as cur:
        owned(cur,mid);t=latest(cur,mid)
        try:enqueue(cur,g.user['organization_id'],str(mid),str(t['id']),t['content'])
        except Invalid:flash('Формат стенограммы требует проверки.');return redirect(url_for('llm013.page',mid=mid),303)
    flash('Задание поставлено в очередь или уже существует. Готовые черновики проверьте перед утверждением.')
    return redirect(url_for('llm013.page',mid=mid),303)
@bp.post('/meetings/<uuid:mid>/llm/<uuid:jid>/retry')
def retry(mid,jid):
    allowed()
    with current_app.store.connection() as c,c.cursor() as cur:
        owned(cur,mid)
        cur.execute("""UPDATE meeting_llm_jobs SET status='QUEUED',error_code=NULL,updated_at=now(),
          model_digest=CASE WHEN error_code='MODEL_CHANGED' AND next_chunk=0 THEN NULL ELSE model_digest END
          WHERE id=%s AND meeting_id=%s AND organization_id=%s AND status='FAILED'
          AND (error_code IS DISTINCT FROM 'MODEL_CHANGED' OR next_chunk=0) RETURNING id""",(str(jid),str(mid),g.user['organization_id']))
        if not cur.fetchone():abort(409)
    return redirect(url_for('llm013.page',mid=mid),303)
def register(app):app.register_blueprint(bp)
