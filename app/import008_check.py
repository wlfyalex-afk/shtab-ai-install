from app import create_app
app=create_app()
with app.test_client() as client:
    assert client.get('/meetings').status_code==302
    res=client.get('/login');assert res.status_code==200
    assert res.headers['Referrer-Policy']=='same-origin'
    assert "script-src 'self'" in res.headers['Content-Security-Policy']
with app.store.connection() as c,c.cursor() as cur:
    cur.execute('SELECT id,source_kind,source_url,downloaded_bytes FROM meeting_imports LIMIT 0')
    cur.execute('SELECT import_id FROM meeting_import_chunks LIMIT 0')
    cur.execute('''SELECT u.id,u.session_version FROM secretary_users u JOIN people p ON p.id=u.person_id
      WHERE u.active AND p.active ORDER BY u.id LIMIT 1''')
    actor=cur.fetchone()
if actor:
    with app.test_client() as client:
        with client.session_transaction() as session:session.update(uid=str(actor['id']),sv=actor['session_version'],csrf='installation-check')
        assert client.get('/meetings').status_code==200
        assert client.get('/meetings/upload').status_code==200
        assert client.get('/meetings/cloud').status_code==200
print('Meeting routes, authentication, database: OK')
