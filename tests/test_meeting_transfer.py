import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import wave
import zipfile
import warnings

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import meeting_transfer as transfer
from store import Store


def identifier():
    return str(uuid.uuid4())


def basic_data():
    org, mid, tid = identifier(), identifier(), identifier()
    records = {table: [] for table in transfer.FIELDS}
    meeting = dict.fromkeys(transfer.FIELDS['meetings'])
    meeting.update(id=mid, organization_id=org, title='Совещание — перенос', source_sha256='a'*64, processing_status='REVIEW')
    transcript = dict.fromkeys(transfer.FIELDS['transcripts'])
    transcript.update(id=tid, organization_id=org, meeting_id=mid, content={'segments': [{'text': 'Готовые результаты'}]})
    records['meetings'] = [meeting]; records['transcripts'] = [transcript]
    return {'organization_id': org, 'records': records}


def package(data):
    output = io.BytesIO(); transfer.write_package(output, data)
    return output


class MeetingPackageTests(unittest.TestCase):
    def test_results_zip_round_trip_and_original_untouched(self):
        data = basic_data(); original = copy.deepcopy(data)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(transfer.read_package(package(data), Path(folder)), data)
        self.assertEqual(data, original)

    def test_audio_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); audio = root / 'audio.wav'
            with wave.open(str(audio), 'wb') as media:
                media.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                media.writeframes(b'\0\0'*1000)
            output = io.BytesIO(); transfer.write_package(output, basic_data(), audio)
            staging = root / 'stage'; staging.mkdir()
            transfer.read_package(output, staging)
            self.assertEqual((staging / 'audio-16k.wav').read_bytes(), audio.read_bytes())

    def test_tampering_and_unknown_format_rejected(self):
        original = package(basic_data())
        with zipfile.ZipFile(original) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        contents['meeting.json'] = contents['meeting.json'].replace(b'REVIEW', b'REVIEZ')
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, 'w') as archive:
            for name, content in contents.items(): archive.writestr(name, content)
        bad.seek(0)
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(transfer.PackageError, 'сумма'):
            transfer.read_package(bad, Path(folder))
        report = io.BytesIO()
        with zipfile.ZipFile(report, 'w') as archive: archive.writestr('report.txt', 'Report')
        report.seek(0)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(transfer.PackageError):
            transfer.read_package(report, Path(folder))

    def test_path_traversal_and_duplicate_entries_rejected(self):
        for names in [('manifest.json', 'meeting.json', '../outside'), ('manifest.json', 'meeting.json', 'meeting.json')]:
            output = io.BytesIO()
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(output, 'w') as archive:
                    for name in names: archive.writestr(name, '{}')
            output.seek(0)
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(transfer.PackageError):
                transfer.read_package(output, Path(folder))

    def test_cross_tenant_extra_tables_and_broken_references_rejected(self):
        for mutation in ('tenant', 'accounts', 'reference', 'duplicate'):
            data = basic_data()
            if mutation == 'tenant': data['records']['transcripts'][0]['organization_id'] = identifier()
            if mutation == 'accounts': data['records']['secretary_users'] = []
            if mutation == 'reference': data['records']['transcripts'][0]['meeting_id'] = identifier()
            if mutation == 'duplicate': data['records']['transcripts'][0]['id'] = data['records']['meetings'][0]['id']
            with self.subTest(mutation=mutation), self.assertRaises(transfer.PackageError): transfer.validate_records(data)

    def test_future_format_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'product': 'ShtabAI-Meeting', 'version': 99}))
            archive.writestr('meeting.json', '{}')
        output.seek(0)
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(transfer.PackageError, 'Версия'):
            transfer.read_package(output, Path(folder))


@unittest.skipUnless(os.environ.get('SHTAB_TEST_POSTGRES'), 'Requires SHTAB_TEST_POSTGRES test database server')
class MeetingTransferDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_dsn = os.environ['SHTAB_TEST_POSTGRES']
        cls.database = 'shtab_transfer_' + uuid.uuid4().hex
        connection = psycopg2.connect(cls.base_dsn); connection.autocommit = True
        with connection.cursor() as cur: cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.database)))
        connection.close()
        params = psycopg2.extensions.parse_dsn(cls.base_dsn); params['dbname'] = cls.database
        cls.store = Store({'database': params})
        with cls.store.connection() as c, c.cursor() as cur:
            cur.execute((ROOT / 'database/schema-ubuntu.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        connection = psycopg2.connect(cls.base_dsn); connection.autocommit = True
        with connection.cursor() as cur: cur.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.database)))
        connection.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.org, self.target, self.person, self.target_person, self.mid, self.tid, self.task, self.job = [identifier() for _ in range(8)]
        self.uid = identifier()
        self.actor = {'organization_id': self.target, 'person_id': self.target_person}
        with self.store.connection() as c, c.cursor() as cur:
            cur.execute('TRUNCATE organizations CASCADE')
            cur.execute('INSERT INTO organizations(id,name) VALUES (%s,%s),(%s,%s)', (self.org, 'Источник', self.target, 'Приёмник'))
            for person, org, name in [(self.person, self.org, 'Исполнитель'), (self.target_person, self.target, 'Администратор')]:
                cur.execute('INSERT INTO people(id,organization_id,display_name,timezone) VALUES (%s,%s,%s,%s)', (person, org, name, 'Asia/Vladivostok'))
            cur.execute('INSERT INTO secretary_users(id,username,person_id,password_hash,is_admin) VALUES (%s,%s,%s,%s,true)', (self.uid, 'wlfy', self.target_person, 'keep-password'))
            cur.execute("INSERT INTO meetings(id,organization_id,title,source_path,source_sha256,duration_seconds,processing_status) VALUES (%s,%s,%s,%s,%s,60,'REVIEW')", (self.mid, self.org, 'Уже распознано', str(self.root / self.mid / 'source.mp3'), 'a'*64))
            cur.execute("INSERT INTO transcripts(id,organization_id,meeting_id,engine,model,language,content) VALUES (%s,%s,%s,'whisper','turbo','ru',%s)", (self.tid, self.org, self.mid, Json({'segments': [{'start': 0, 'end': 5, 'text': 'Сделать отчёт'}]})))
            cur.execute("INSERT INTO tasks(id,organization_id,meeting_id,instruction,source_spans,primary_assignee_id,lifecycle,review_status,approved_by,approved_at) VALUES (%s,%s,%s,'Сделать отчёт',%s,%s,'APPROVED','CONFIRMED',%s,now())", (self.task, self.org, self.mid, Json([{'transcript_id': self.tid, 'start': 0, 'end': 5}]), self.person, self.person))
            cur.execute("INSERT INTO task_assignees(task_id,person_id,role) VALUES (%s,%s,'PRIMARY')", (self.task, self.person))
            cur.execute("INSERT INTO task_comments(id,organization_id,task_id,author_id,author_role,body) VALUES (%s,%s,%s,%s,'secretary','Проверено')", (identifier(), self.org, self.task, self.person))
            cur.execute("INSERT INTO meeting_task_drafts(id,organization_id,meeting_id,transcript_id,segment_index,instruction,source_quote,start_seconds,end_seconds,method,status,task_id,reviewed_by) VALUES (%s,%s,%s,%s,0,'Сделать отчёт','Сделать отчёт',0,5,'local','APPROVED',%s,%s)", (identifier(), self.org, self.mid, self.tid, self.task, self.person))
            llm_job = identifier()
            cur.execute("INSERT INTO meeting_llm_jobs(id,organization_id,meeting_id,transcript_id,method,model,source_hash,status,total_chunks) VALUES (%s,%s,%s,%s,'local','qwen',%s,'DONE',1)", (llm_job, self.org, self.mid, self.tid, 'c'*64))
            cur.execute("INSERT INTO meeting_llm_chunks(job_id,chunk_no,response,elapsed_seconds,created_count,rejected_count) VALUES (%s,0,%s,1,1,0)", (llm_job, Json({})))
            cur.execute("INSERT INTO meeting_brief_jobs(id,organization_id,meeting_id,transcript_id,method,model,source_hash,status,total_chunks,created_by) VALUES (%s,%s,%s,%s,'local','qwen',%s,'DONE',1,%s)", (self.job, self.org, self.mid, self.tid, 'b'*64, self.person))
            cur.execute("INSERT INTO meeting_brief_chunks(job_id,chunk_no,response,evidence,elapsed_seconds,rejected_count) VALUES (%s,0,%s,%s,1,0)", (self.job, Json({}), Json([])))
            cur.execute("INSERT INTO meeting_briefs(id,organization_id,meeting_id,transcript_id,job_id,method,model,model_digest,content,review_status,approved_by,approved_at) VALUES (%s,%s,%s,%s,%s,'local','qwen','digest',%s,'APPROVED',%s,now())", (identifier(), self.org, self.mid, self.tid, self.job, Json({'summary': 'Готовый бриф'}), self.person))
        (self.root / self.mid).mkdir()
        with wave.open(str(self.root / self.mid / 'audio-16k.wav'), 'wb') as media:
            media.setparams((1, 2, 16000, 0, 'NONE', 'not compressed')); media.writeframes(b'\0\0'*50000)

    def tearDown(self): self.temp.cleanup()

    def snapshot(self): return transfer.snapshot(self.store, self.mid, self.org)

    def test_complete_additive_cross_platform_round_trip(self):
        data = self.snapshot()
        output = io.BytesIO(); transfer.write_package(output, data, self.root / self.mid / 'audio-16k.wav')
        imported = transfer.import_package(self.store, output, self.actor, self.root)
        self.assertNotEqual(imported, self.mid)
        self.assertTrue((self.root / imported / 'audio-16k.wav').exists())
        with self.store.connection() as c, c.cursor() as cur:
            cur.execute('SELECT count(*) AS n FROM meetings'); self.assertEqual(cur.fetchone()['n'], 2)
            cur.execute('SELECT * FROM tasks WHERE organization_id=%s', (self.target,)); task = cur.fetchone()
            self.assertEqual(task['instruction'], 'Сделать отчёт'); self.assertNotEqual(str(task['primary_assignee_id']), self.person)
            cur.execute('SELECT id FROM transcripts WHERE meeting_id=%s', (imported,)); tid = str(cur.fetchone()['id'])
            self.assertEqual(task['source_spans'][0]['transcript_id'], tid)
            cur.execute('SELECT content,review_status FROM meeting_briefs WHERE meeting_id=%s', (imported,)); brief = cur.fetchone()
            self.assertEqual(brief['content']['summary'], 'Готовый бриф'); self.assertEqual(brief['review_status'], 'APPROVED')
            cur.execute('SELECT password_hash FROM secretary_users'); self.assertEqual(cur.fetchone()['password_hash'], 'keep-password')
            cur.execute('SELECT count(*) AS n FROM secretary_users'); self.assertEqual(cur.fetchone()['n'], 1)
            cur.execute('SELECT status FROM meeting_llm_jobs WHERE organization_id=%s', (self.target,)); self.assertEqual(cur.fetchone()['status'], 'DONE')
            cur.execute('SELECT count(*) AS n FROM meeting_imports'); self.assertEqual(cur.fetchone()['n'], 0)
            cur.execute('SELECT count(*) AS n FROM task_comments WHERE organization_id=%s', (self.target,)); self.assertEqual(cur.fetchone()['n'], 1)
        # A second export from the receiving installation is still portable.
        second = transfer.snapshot(self.store, imported, self.target)
        self.assertEqual(second['records']['transcripts'][0]['content'], data['records']['transcripts'][0]['content'])

    def test_duplicate_import_keeps_existing_records_and_files(self):
        data = self.snapshot(); imported = transfer.import_package(self.store, package(data), self.actor, self.root)
        before = sorted(p.name for p in self.root.iterdir())
        with self.assertRaisesRegex(transfer.PackageError, 'уже есть'):
            transfer.import_package(self.store, package(data), self.actor, self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        self.assertTrue((self.root / imported).exists())

    def test_database_failure_rolls_back_people_meeting_and_audio(self):
        data = self.snapshot(); data['records']['tasks'][0]['lifecycle'] = 'INVALID'
        before = sorted(p.name for p in self.root.iterdir())
        with self.assertRaises(psycopg2.Error): transfer.import_package(self.store, package(data), self.actor, self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        with self.store.connection() as c, c.cursor() as cur:
            cur.execute('SELECT count(*) AS n FROM meetings WHERE organization_id=%s', (self.target,)); self.assertEqual(cur.fetchone()['n'], 0)
            cur.execute('SELECT count(*) AS n FROM people WHERE organization_id=%s', (self.target,)); self.assertEqual(cur.fetchone()['n'], 1)

    def test_other_organization_cannot_export_and_running_brief_is_rejected(self):
        self.assertIsNone(transfer.snapshot(self.store, self.mid, self.target))
        with self.store.connection() as c, c.cursor() as cur: cur.execute("UPDATE meeting_brief_jobs SET status='RUNNING' WHERE id=%s", (self.job,))
        with self.assertRaisesRegex(transfer.PackageError, 'Дождитесь'): self.snapshot()

    def test_browser_import_csrf_permissions_and_preserved_source(self):
        from app import create_app
        app = create_app({'secret_key': 'test-secret'*8}, self.store)
        app.config.update(TESTING=True, IMPORT_ROOT=self.root)
        client = app.test_client()
        with client.session_transaction() as session: session.update(uid=self.uid, sv=1, csrf='test-token')
        self.assertEqual(client.get('/meetings/import-package').status_code, 200)
        self.assertEqual(client.get(f'/meetings/{self.mid}/transfer').status_code, 404)
        upload = io.BytesIO()
        transfer.write_package(upload, self.snapshot(), self.root / self.mid / 'audio-16k.wav')
        self.assertGreater(len(upload.getvalue()), 64000)
        response = client.post('/meetings/import-package', data={'csrf': 'test-token', 'confirm': 'yes', 'package': (upload, 'transfer.zip')})
        self.assertEqual(response.status_code, 303)
        self.assertIn('/meetings/', response.location)
        self.assertEqual(client.get(response.location).status_code, 200)
        self.assertEqual(client.get('/meetings').status_code, 200)
        mid = response.location.split('/')[2]
        self.assertEqual(client.get(f'/meetings/{mid}/transfer').status_code, 200)
        exported = client.post(f'/meetings/{mid}/transfer', data={'csrf': 'test-token', 'include_audio': 'yes'})
        self.assertEqual(exported.status_code, 200)
        self.assertIn('audio-16k.wav', zipfile.ZipFile(io.BytesIO(exported.data)).namelist())
        exported.close()
        self.assertEqual(client.post('/meetings/import-package', data={'confirm': 'yes'}).status_code, 400)
        with self.store.connection() as c, c.cursor() as cur: cur.execute("UPDATE secretary_users SET is_admin=false,app_role='head' WHERE id=%s", (self.uid,))
        self.assertEqual(client.get('/meetings/import-package').status_code, 403)
        with client.session_transaction() as session: session.clear()
        self.assertEqual(client.get('/meetings/import-package').status_code, 302)
