"""Exercise real foreign keys, role checks, files and multipart/cloud workflows."""
import io
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg2
from psycopg2.extras import Json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from app import create_app
from meeting_delete import DeleteBusy, delete_recording
import test_meeting_transfer as fixtures
from test_meeting_transfer import identifier
import meeting_transfer


@unittest.skipUnless(os.environ.get('SHTAB_TEST_POSTGRES'), 'Requires SHTAB_TEST_POSTGRES test database server')
class RecordingDeletionTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.MeetingTransferDatabaseTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.MeetingTransferDatabaseTests.tearDownClass.__func__)
    setUp = fixtures.MeetingTransferDatabaseTests.setUp
    tearDown = fixtures.MeetingTransferDatabaseTests.tearDown

    def sql(self, statement, args=()):
        with self.store.connection() as c, c.cursor() as cur:
            cur.execute(statement, args)
            return cur.fetchall() if cur.description else []

    def client(self, admin=True):
        self.sql('UPDATE secretary_users SET person_id=%s,is_admin=%s WHERE id=%s', (self.person, admin, self.uid))
        app = create_app({'secret_key': 'test-secret'*4}, self.store)
        app.config.update(TESTING=True, IMPORT_ROOT=self.root)
        client = app.test_client()
        with client.session_transaction() as s:
            s.update(uid=self.uid, sv=1, csrf='test-csrf')
        return client

    def remove(self, kind='meeting', uid=None):
        delete_recording(self.store, self.org, self.person, kind, uid or self.mid, self.root)

    def assembly(self, duplicate=False, draft=False):
        gid = identifier(); parts = [identifier(), identifier()]
        self.sql("INSERT INTO meeting_import_groups(id,organization_id,created_by,title,meeting_at,status) VALUES (%s,%s,%s,'Сборка',now(),%s)", (gid, self.org, self.person, 'DRAFT' if draft else 'DONE'))
        for position, uid in enumerate(parts, 1):
            self.sql("""INSERT INTO meeting_imports(id,organization_id,created_by,title,meeting_at,original_name,total_bytes,uploaded_bytes,status,group_id,group_position,meeting_id,transcript_id)
                VALUES (%s,%s,%s,'Сборка',now(),'part.mp3',3,3,%s,%s,%s,%s,%s)""",
                (uid, self.org, self.person, 'QUEUED' if draft else ('DUPLICATE' if duplicate and position == 1 else 'REVIEW'), gid, position,
                 self.mid if position == 1 and not draft else None, self.tid if position == 1 and not draft else None))
            (self.root / uid).mkdir(); (self.root / uid / 'source.mp3').write_bytes(b'abc')
        if not draft:
            self.sql('UPDATE meeting_import_groups SET primary_import_id=%s WHERE id=%s', (parts[0], gid))
        if not duplicate and not draft:
            shutil.move(str(self.root / self.mid), str(self.root / parts[0] / 'bundle'))
            self.sql('UPDATE meetings SET source_path=%s WHERE id=%s', (str(self.root / parts[0] / 'bundle' / 'combined.wav'), self.mid))
        return gid, parts

    def test_only_selected_meeting_dependencies_and_files_removed(self):
        neighbor = identifier(); (self.root / neighbor).mkdir(); (self.root / neighbor / 'keep.txt').write_text('keep')
        self.sql("INSERT INTO meetings(id,organization_id,title,source_path,source_sha256) VALUES (%s,%s,'Соседняя запись',%s,%s)", (neighbor, self.org, str(self.root / neighbor / 'source.mp3'), 'd'*64))
        self.remove()
        for table in ('transcripts', 'meeting_task_drafts', 'meeting_briefs', 'meeting_brief_jobs', 'meeting_llm_jobs', 'tasks'):
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table}')[0]['n'], 0)
        self.assertEqual(str(self.sql('SELECT id FROM meetings')[0]['id']), neighbor)
        self.assertFalse((self.root / self.mid).exists())
        self.assertEqual((self.root / neighbor / 'keep.txt').read_text(), 'keep')
        self.assertEqual(self.sql("SELECT count(*) AS n FROM audit_events WHERE event_type='MEETING_DELETED'")[0]['n'], 1)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM people')[0]['n'], 2)

    def test_admin_only_confirmation_csrf_and_single_uuid_routes(self):
        client = self.client(admin=False)
        url = f'/meetings/delete/meeting/{self.mid}'
        self.assertEqual(client.get(url).status_code, 403)
        self.assertEqual(client.post(url, data={'csrf': 'test-csrf', 'confirm': self.mid}).status_code, 403)
        self.assertNotIn('Удалить запись', client.get('/meetings').get_data(as_text=True))
        client = self.client()
        self.assertEqual(client.get(url).status_code, 200)
        self.assertEqual(client.post(url, data={'confirm': self.mid}).status_code, 400)
        self.assertEqual(client.post(url, data={'csrf': 'test-csrf', 'confirm': identifier()}).status_code, 400)
        self.assertEqual(client.post('/meetings/delete/all', data={'csrf': 'test-csrf'}).status_code, 404)
        self.assertEqual(client.post(url, data={'csrf': 'test-csrf', 'confirm': self.mid}).status_code, 303)
        self.assertEqual(client.get(url).status_code, 404)

    def test_cross_organization_and_unknown_ids_rejected(self):
        self.sql('UPDATE secretary_users SET person_id=%s WHERE id=%s', (self.target_person, self.uid))
        app = create_app({'secret_key': 'test-secret'*4}, self.store); app.config['TESTING'] = True
        client = app.test_client()
        with client.session_transaction() as s: s.update(uid=self.uid, sv=1, csrf='test-csrf')
        self.assertEqual(client.get(f'/meetings/delete/meeting/{self.mid}').status_code, 404)
        self.assertEqual(client.post(f'/meetings/delete/meeting/{self.mid}', data={'csrf': 'test-csrf', 'confirm': self.mid}).status_code, 404)
        self.assertTrue((self.root / self.mid).exists())

    def test_assembly_deletes_all_parts_and_result_as_one_record(self):
        gid, parts = self.assembly()
        for uid in parts:
            self.sql("INSERT INTO meeting_import_metrics(id,import_id,run_id,stage,state,started_at) VALUES (%s,%s,%s,'PROBING','DONE',now())", (identifier(),uid,identifier()))
        self.remove('group', gid)
        for table in ('meetings', 'meeting_imports', 'meeting_import_groups', 'meeting_import_metrics'):
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table}')[0]['n'], 0)
        self.assertTrue(all(not (self.root / uid).exists() for uid in parts))

    def test_fragment_route_redirects_to_whole_assembly_confirmation(self):
        gid, parts = self.assembly(draft=True)
        client = self.client()
        response = client.get(f'/meetings/delete/import/{parts[1]}')
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.location.endswith('/group/' + gid))
        self.remove('group', gid)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meetings')[0]['n'], 1)

    def test_meeting_route_removes_owning_assembly_too(self):
        gid, parts = self.assembly()
        self.remove()
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meeting_import_groups')[0]['n'], 0)
        self.assertTrue(all(not (self.root / uid).exists() for uid in parts))

    def test_deleting_duplicate_assembly_preserves_existing_meeting(self):
        gid, parts = self.assembly(duplicate=True)
        self.remove('group', gid)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meetings')[0]['n'], 1)
        self.assertTrue((self.root / self.mid / 'audio-16k.wav').exists())

    def test_deleting_original_preserves_unrelated_duplicate_assembly(self):
        gid, parts = self.assembly(duplicate=True)
        self.remove()
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meeting_import_groups')[0]['n'], 1)
        self.assertTrue(all((self.root / uid).exists() for uid in parts))
        self.assertIsNone(self.sql('SELECT meeting_id FROM meeting_imports WHERE id=%s', (parts[0],))[0]['meeting_id'])

    def test_unfinished_standalone_upload_removes_chunks_but_keeps_meetings(self):
        uid = identifier()
        self.sql("INSERT INTO meeting_imports(id,organization_id,created_by,title,meeting_at,original_name,total_bytes) VALUES (%s,%s,%s,'Незавершённая запись',now(),'part.mp3',3)", (uid, self.org, self.person))
        self.sql("INSERT INTO meeting_import_chunks(import_id,part_no,size_bytes,sha256) VALUES (%s,0,3,%s)", (uid, 'f'*64))
        (self.root / uid).mkdir(); (self.root / uid / '0.chunk').write_bytes(b'abc')
        self.remove('import', uid)
        self.assertFalse((self.root / uid).exists())
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meetings')[0]['n'], 1)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meeting_import_chunks')[0]['n'], 0)

    def test_foreign_or_symlink_source_paths_never_deleted(self):
        outside = self.root / 'outside'; outside.mkdir(); (outside / 'keep').write_text('keep')
        self.sql('UPDATE meetings SET source_path=%s WHERE id=%s', (str(outside / 'keep'), self.mid))
        with self.assertRaises(DeleteBusy): self.remove()
        self.assertEqual((outside / 'keep').read_text(), 'keep')
        shutil.rmtree(self.root / self.mid)
        (self.root / self.mid).symlink_to(outside, target_is_directory=True)
        self.sql('UPDATE meetings SET source_path=%s WHERE id=%s', (str(self.root / self.mid / 'keep'), self.mid))
        with self.assertRaises(DeleteBusy): self.remove()
        self.assertEqual((outside / 'keep').read_text(), 'keep')

    def test_call_jobs_shared_with_other_meetings_are_preserved(self):
        contact, call, attempt, neighbor, other_task = [identifier() for _ in range(5)]
        self.sql("INSERT INTO contact_points(id,organization_id,person_id,kind,label,value,normalized_value) VALUES (%s,%s,%s,'MOBILE','Телефон','+79990000000','+79990000000')", (contact,self.org,self.person))
        self.sql("INSERT INTO call_jobs(id,organization_id,person_id,contact_point_id,scheduled_at,status,idempotency_key) VALUES (%s,%s,%s,%s,now(),'COMPLETED',%s)", (call,self.org,self.person,contact,call))
        self.sql("INSERT INTO call_attempts(id,organization_id,call_job_id,attempt_no,status) VALUES (%s,%s,%s,1,'COMPLETED')", (attempt,self.org,call))
        self.sql("INSERT INTO response_worker_runs(attempt_id) VALUES (%s)", (attempt,))
        self.sql("INSERT INTO call_job_tasks(call_job_id,task_id,sequence_no) VALUES (%s,%s,1)", (call,self.task))
        self.sql("INSERT INTO meetings(id,organization_id,title,source_path,source_sha256) VALUES (%s,%s,'Сосед',%s,%s)", (neighbor,self.org,str(self.root / neighbor / 'source.mp3'),'e'*64))
        self.sql("INSERT INTO tasks(id,organization_id,meeting_id,instruction,source_spans) VALUES (%s,%s,%s,'Соседнее поручение',%s)", (other_task,self.org,neighbor,Json([{'text':'source'}])))
        self.sql("INSERT INTO call_job_tasks(call_job_id,task_id,sequence_no) VALUES (%s,%s,2)", (call,other_task))
        self.remove()
        self.assertEqual(self.sql('SELECT count(*) AS n FROM call_jobs')[0]['n'], 1)
        self.assertEqual(str(self.sql('SELECT task_id FROM call_job_tasks')[0]['task_id']), other_task)
        delete_recording(self.store,self.org,self.person,'meeting',neighbor,self.root)
        for table in ('call_jobs','call_attempts','response_worker_runs'):
            self.assertEqual(self.sql(f'SELECT count(*) AS n FROM {table}')[0]['n'], 0)

    def test_worker_lock_prevents_deletion_and_late_results(self):
        with self.store.connection() as c, c.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(8008002)')
            with self.assertRaises(DeleteBusy): self.remove()
        self.assertTrue((self.root / self.mid).exists())
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meetings')[0]['n'], 1)

    def test_rename_failure_rolls_back_database_and_previously_moved_files(self):
        gid, parts = self.assembly()
        original = Path.rename; calls = []
        def fail_second(path, target):
            if str(target).split('/')[-1].startswith('.deleted-'):
                calls.append(path)
                if len(calls) == 2: raise OSError('Test disk error')
            return original(path, target)
        with patch.object(Path, 'rename', fail_second), self.assertRaises(OSError): self.remove('group', gid)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meetings')[0]['n'], 1)
        self.assertTrue(all((self.root / uid).exists() for uid in parts))
        self.assertEqual(list(self.root.glob('.deleted-*')), [])

    def test_saved_record_can_be_restored_after_deletion(self):
        data = meeting_transfer.snapshot(self.store, self.mid, self.org)
        backup = io.BytesIO(); meeting_transfer.write_package(backup, data, self.root / self.mid / 'audio-16k.wav')
        self.remove()
        mid = meeting_transfer.import_package(self.store, backup, {'organization_id': self.org, 'person_id': self.person}, self.root)
        self.assertTrue((self.root / mid / 'audio-16k.wav').exists())
        self.assertEqual(self.sql('SELECT count(*) AS n FROM meeting_briefs')[0]['n'], 1)
        self.assertEqual(self.sql('SELECT count(*) AS n FROM tasks')[0]['n'], 1)

    def test_cloud_links_inherit_assembly_metadata_and_order(self):
        gid, parts = self.assembly(draft=True)
        client = self.client()
        page = client.get(f'/meetings/groups/{gid}').get_data(as_text=True)
        self.assertIn('name="url"', page); self.assertIn('Яндекс Диска', page)
        with patch('meeting_import.shutil.disk_usage', return_value=shutil._ntuple_diskusage(200*1024**3, 0, 200*1024**3)):
            response = client.post('/meetings/cloud', data={'csrf': 'test-csrf', 'group_id': gid, 'kind': 'YANDEX', 'url': 'https://disk.yandex.ru/d/test'})
        self.assertEqual(response.status_code, 303)
        row = self.sql('SELECT * FROM meeting_imports WHERE group_id=%s ORDER BY group_position DESC LIMIT 1', (gid,))[0]
        self.assertEqual(row['source_kind'], 'YANDEX'); self.assertEqual(row['title'], 'Сборка')
        self.assertEqual(row['group_position'], 3)
        self.assertEqual(row['meeting_at'], self.sql('SELECT meeting_at FROM meeting_import_groups WHERE id=%s', (gid,))[0]['meeting_at'])
        self.assertEqual(client.post('/meetings/cloud', data={'csrf': 'test-csrf', 'group_id': gid, 'kind': 'YANDEX', 'url': 'https://example.com/file'}).status_code, 400)
        self.assertEqual(client.post(f'/meetings/groups/{gid}', data={'csrf': 'test-csrf', 'action': 'start'}).status_code, 303)
        self.assertEqual(client.post('/meetings/cloud', data={'csrf': 'test-csrf', 'group_id': gid, 'kind': 'YANDEX', 'url': 'https://disk.yandex.ru/d/another'}).status_code, 409)
