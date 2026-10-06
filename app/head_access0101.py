from flask import Blueprint,abort,g,current_app,render_template,request,redirect,url_for
bp=Blueprint('head_access',__name__)
def is_head():
    return bool(g.get('user') and not g.user.get('is_admin') and g.user.get('app_role','secretary')=='head')
@bp.get('/head/meetings')
def meetings():
    try:page=int(request.args.get('page','0'))
    except ValueError:abort(400)
    if not 0<=page<=10000:abort(400)
    with current_app.store.connection() as c,c.cursor() as cur:
        cur.execute('SELECT id,title,meeting_at,duration_seconds FROM meetings WHERE organization_id=%s ORDER BY meeting_at DESC NULLS LAST,id LIMIT 51 OFFSET %s',(g.user['organization_id'],page*50))
        rows=cur.fetchall()
    return render_template('head_meetings0101.html',rows=rows[:50],more=len(rows)>50,page=page)
def register(app):
    app.register_blueprint(bp)
    app.context_processor(lambda:dict(is_head=is_head))
    @app.before_request
    def enforce_role():
        if not g.get('user'):return
        if not g.user.get('is_admin') and g.user.get('app_role','secretary') not in ('head','secretary'):abort(403)
        if not is_head():return
        if request.endpoint=='queue' and request.method in ('GET','HEAD'):
            return redirect(url_for('head.dashboard'))
        allowed={'head.dashboard','head_access.meetings','reports0184.period','detail','audio','response_integration0181.task_detail','response_integration0181.manual_detail','workflow0182.status','brief017.page','meeting_review.drafts','imports.meeting','imports.export','meeting_review.audio','static','login'}
        if request.endpoint=='reports0184.add_comment' and request.method=='POST':return
        if request.endpoint=='logout' and request.method=='POST':return
        if request.method not in ('GET','HEAD') or request.endpoint not in allowed:abort(403)
