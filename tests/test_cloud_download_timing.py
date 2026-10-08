"""Exercise the real status handler with tenant-scoped, in-memory DB responses."""
import ast
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]

class TimingTests(unittest.TestCase):
    def test_final_table_cloud_download_counted_once(self):
        tree=ast.parse((ROOT/'app/automation0183.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='status_page')
        start=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='metric' for t in n.targets))
        end=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='recognition' for t in n.targets))
        code=ast.fix_missing_locations(ast.Module(body=fn.body[start:end],type_ignores=[]))
        for browser_seconds in (0,15):
            ns=dict(metric_rows=[dict(stage='FETCHING',seconds=90),dict(stage='CONVERTING',seconds=58)],
              upload_seconds=browser_seconds,_seconds=lambda v:max(0,float(v or 0)))
            exec(compile(code,'actual-summary-calculation','exec'),ns)
            self.assertEqual(ns['upload_seconds'],browser_seconds+90)
            self.assertEqual(ns['preparation'],58)
            self.assertEqual(ns['upload_seconds']+ns['preparation'],browser_seconds+148)

    def call_state(self, status='FETCHING', event=None):
        tree = ast.parse((ROOT/'app/meeting_import.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'state')
        fn.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        queries = []
        cursor = SimpleNamespace(execute=lambda sql, args: queries.append((sql,args)),
          fetchall=lambda: [], fetchone=lambda: event)
        connection = SimpleNamespace(cursor=lambda: nullcontext(cursor))
        ns = dict(store=lambda: SimpleNamespace(connection=lambda: nullcontext(connection)),
          get_row=lambda cur, uid: dict(id=uid, status=status), public=lambda row: row,
          jsonify=lambda **kw: kw, datetime=datetime, timezone=timezone)
        exec(compile(module,'actual-state-handler','exec'),ns)
        return ns['state']('test-import'),queries

    def test_elapsed_before_first_download_tick(self):
        now = datetime.now(timezone.utc)
        result,queries = self.call_state(event=dict(id='run',started_at=now-timedelta(seconds=65),
          heartbeat_at=now,state='RUNNING',elapsed_seconds=0))
        self.assertGreaterEqual(result['download_elapsed_seconds'],65)
        self.assertFalse(result['download_stale'])
        self.assertEqual(queries[-1][1],('test-import',))

    def test_stalled_download_and_completed_attempt(self):
        now = datetime.now(timezone.utc)
        event=dict(id='run',started_at=now-timedelta(seconds=100),
          heartbeat_at=now-timedelta(seconds=40),state='RUNNING',elapsed_seconds=60)
        result,_=self.call_state(event=event)
        self.assertTrue(result['download_stale'])
        event.update(state='FAILED',elapsed_seconds=72)
        result,_=self.call_state(event=event)
        self.assertEqual(result['download_elapsed_seconds'],72)

    def test_missing_metrics_and_non_download_status(self):
        result,_=self.call_state()
        self.assertNotIn('download_elapsed_seconds',result)
        result,queries=self.call_state(status='TRANSCRIBING')
        self.assertEqual(len(queries),1)
        self.assertNotIn('download_elapsed_seconds',result)

if __name__ == '__main__': unittest.main()
