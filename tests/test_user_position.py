import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from flask import Flask, g, render_template
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
ORG = '11111111-1111-1111-1111-111111111111'
PERSON = '22222222-2222-2222-2222-222222222222'


class Cursor:
    def __init__(self, person): self.person, self.calls = person, []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, args): self.calls.append((sql, args))
    def fetchone(self): return self.person


class Connection:
    def __init__(self, cursor): self.value = cursor
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self.value


class Store:
    def __init__(self, cursor): self.cursor = cursor
    def connection(self): return Connection(self.cursor)


class UserPositionTests(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module('user_admin010')
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only'
        self.app.register_blueprint(self.m.bp)
        self.app.add_url_rule('/contact/<person_id>', endpoint='call_assist.contact_add', view_func=lambda person_id: '')
        self.cursor = Cursor({'id': PERSON, 'role_title': 'Инженер'})
        self.app.store = Store(self.cursor)
        self.actor = {'id': PERSON, 'person_id': PERSON, 'is_admin': True, 'organization_id': ORG, 'timezone': 'Asia/Vladivostok'}
        @self.app.before_request
        def actor(): g.user = self.actor
        self.lock = patch.object(self.m, 'locked_admin')
        self.lock.start(); self.addCleanup(self.lock.stop)
        self.client = self.app.test_client()

    def position(self, title, previous='Инженер'):
        return self.client.post('/admin/people/'+PERSON+'/position', data={'role_title': title, 'previous_role_title': previous})

    def test_save_scoped_to_organization_and_audited_without_revoking_sessions(self):
        response = self.position('  Главный инженер  ')
        self.assertEqual(response.status_code, 303)
        select, update, audit = self.cursor.calls
        self.assertIn('organization_id=%s', select[0]); self.assertEqual(select[1], (PERSON, ORG))
        self.assertEqual(update[1], ('Главный инженер', PERSON, ORG))
        self.assertIn('PERSON_POSITION_CHANGED', audit[1])
        self.assertFalse(any('UPDATE secretary_users' in sql for sql, _ in self.cursor.calls))

    def test_blank_clears_position(self):
        self.assertEqual(self.position(' ').status_code, 303)
        self.assertEqual(self.cursor.calls[1][1], (None, PERSON, ORG))

    def test_conflict_or_foreign_person_does_not_update(self):
        response = self.position('Директор', previous='Старая должность')
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.cursor.calls), 1)
        self.cursor.calls.clear(); self.cursor.person = None
        self.assertEqual(self.position('Директор').status_code, 404)
        self.assertEqual(len(self.cursor.calls), 1)

    def test_nonadmin_long_title_and_missing_version_are_rejected(self):
        self.actor['is_admin'] = False
        self.assertEqual(self.position('Директор').status_code, 403)
        self.actor['is_admin'] = True
        self.assertEqual(self.position('x'*201).status_code, 303)
        self.assertEqual(self.client.post('/admin/people/'+PERSON+'/position', data={'role_title':'Директор'}).status_code, 400)
        self.assertEqual(self.cursor.calls, [])

    def test_creation_sets_new_position_but_preserves_selected_employee(self):
        data = {'username': 'engineer', 'display_name':'Иван', 'role_title':'Главный инженер', 'password':'long-password-123', 'confirm':'long-password-123', 'role':'secretary'}
        with patch.object(self.m, 'password', return_value='hash'):
            self.assertEqual(self.client.post('/admin/users/create', data=data).status_code, 303)
            insert = next(args for sql, args in self.cursor.calls if sql.startswith('INSERT INTO people'))
            self.assertEqual(insert[-1], 'Главный инженер')
            self.cursor.calls.clear(); data['person'] = PERSON
            self.assertEqual(self.client.post('/admin/users/create', data=data).status_code, 303)
            self.assertFalse(any('UPDATE people' in sql or 'INSERT INTO people' in sql for sql, args in self.cursor.calls))

    def test_template_includes_forms_for_admin_and_contacts_and_escapes_title(self):
        self.app.jinja_loader = ChoiceLoader([DictLoader({'base.html':'{% block content %}{% endblock %}'}), FileSystemLoader(ROOT/'app/templates')])
        self.app.jinja_env.globals['csrf'] = lambda: 'test-token'
        for uid, is_admin in ((PERSON, True), (None, False)):
            with self.app.test_request_context():
                g.user = dict(self.actor, organization_name='Организация')
                row = {'person_id':PERSON,'display_name':'Иван','role_title':'<script>alert(1)</script>', 'user_id':uid,'is_admin':is_admin,'contacts':[], 'session_version':1}
                html = render_template('users010.html', rows=[row], people=[], kinds={})
            self.assertIn('Сохранить должность', html)
            self.assertIn('name="previous_role_title"', html)
            self.assertNotIn('<script>alert(1)</script>', html)
            self.assertIn('&lt;script&gt;', html)


if __name__ == '__main__': unittest.main()
