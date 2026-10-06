"""Local operator commands; never exposed as HTTP endpoints."""
import argparse,json,os,uuid
from pathlib import Path
from store import Store

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['check','grant-admin'])
    parser.add_argument('--username')
    args=parser.parse_args()
    cfg=json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
    store=Store(cfg)
    with store.connection() as c,c.cursor() as cur:
        if args.command=='check':
            cur.execute('SELECT is_admin FROM secretary_users LIMIT 0')
            print('user administration database: OK');return
        if not args.username:parser.error('--username required')
        cur.execute('''SELECT u.id,p.organization_id FROM secretary_users u JOIN people p ON p.id=u.person_id
          WHERE u.username=%s AND u.active AND p.active''',(args.username.strip().lower(),))
        row=cur.fetchone()
        if not row:raise SystemExit('Active account not found')
        cur.execute('SELECT id FROM organizations WHERE id=%s FOR UPDATE',(row['organization_id'],))
        cur.execute('''UPDATE secretary_users u SET is_admin=true,session_version=session_version+1
          FROM people p WHERE u.id=%s AND p.id=u.person_id AND p.organization_id=%s
          AND u.active AND p.active AND NOT u.is_admin RETURNING u.id''',(row['id'],row['organization_id']))
        changed=cur.fetchone()
        if changed:
            cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
              VALUES (%s,%s,'SYSTEM','WEB_ADMIN_GRANTED_LOCAL','WEB_USER',%s,'{}'::jsonb)''',
              (str(uuid.uuid4()),row['organization_id'],row['id']))
    print('Administrator access set. Sign in again with the existing password.')
if __name__=='__main__':main()
