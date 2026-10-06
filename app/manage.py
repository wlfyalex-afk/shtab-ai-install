import argparse
import getpass
import json
import os
from pathlib import Path
import re
import uuid
from werkzeug.security import generate_password_hash
from store import Store

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['check','create-user','reset-password'])
    args = parser.parse_args()
    cfg = json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
    store = Store(cfg)
    if args.command == 'check':
        with store.connection() as c, c.cursor() as cur:
            cur.execute('SELECT reviewed_result,review_version,response_analysis FROM task_responses LIMIT 0')
            cur.execute('SELECT count(*) AS n FROM secretary_users WHERE active')
            n = cur.fetchone()['n']
        print('database: OK; active web users:',n)
        for root in cfg['audio_roots']:
            print('audio directory:',root,'accessible' if os.access(root,os.R_OK|os.X_OK) else 'not accessible')
        return
    username = input('Логин (латиница, цифры, _, -): ').strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9_-]{2,39}',username): raise SystemExit('Недопустимый логин')
    with store.connection() as c, c.cursor() as cur:
        cur.execute('SELECT id FROM secretary_users WHERE username=%s',(username,))
        existing = cur.fetchone()
        if args.command == 'create-user':
            if existing: raise SystemExit('Логин существует. Для смены пароля: reset-password')
            cur.execute('''SELECT p.id,p.display_name,o.name FROM people p
                JOIN organizations o ON o.id=p.organization_id WHERE p.active ORDER BY p.display_name,p.id''')
            people = cur.fetchall()
            if not people: raise SystemExit('Нет активных людей в справочнике')
            for i,p in enumerate(people,1): print(f"{i}. {p['display_name']} — {p['name']}")
            try:
                index=int(input('Номер проверяющего из списка: '))-1
                if not 0 <= index < len(people): raise ValueError()
            except ValueError: raise SystemExit('Некорректный номер')
            person=people[index]
        elif not existing: raise SystemExit('Логин не найден')
        password=getpass.getpass('Новый пароль (не менее 14 символов): ')
        if not 14 <= len(password) <= 200: raise SystemExit('Нужен пароль от 14 до 200 символов')
        if password != getpass.getpass('Повторите пароль: '): raise SystemExit('Пароли не совпали')
        password_hash=generate_password_hash(password)
        if args.command == 'create-user':
            cur.execute('''INSERT INTO secretary_users(id,username,person_id,password_hash)
                VALUES (%s,%s,%s,%s)''',(str(uuid.uuid4()),username,person['id'],password_hash))
        else:
            cur.execute('UPDATE secretary_users SET password_hash=%s,session_version=session_version+1 WHERE id=%s',
                        (password_hash,existing['id']))
    print('Учётная запись сохранена. Пароль не выводится и не записывается в журнал.')

if __name__=='__main__': main()
