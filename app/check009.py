from app import create_app
app=create_app()
with app.store.connection() as c,c.cursor() as cur:
    cur.execute('SELECT id,version FROM meeting_task_drafts LIMIT 0')
with app.test_client() as client:
    r=client.get('/login');assert r.status_code==200
    assert b'author-avatar.jpg' in r.data
    assert r.headers['Referrer-Policy']=='same-origin'
    assert client.get('/meetings/11111111-1111-4111-8111-111111111111/drafts').status_code==302
print('Review 009: database, routes, authentication and branding OK')
