import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]

class InstallerTests(unittest.TestCase):
    def test_compose_runtime_invariants(self):
        c = yaml.safe_load((ROOT/'compose.yaml').read_text())
        s = c['services']
        self.assertFalse('ports' in s['db'] or 'ports' in s['ollama'])
        for name in ('web','meeting-worker','llm-worker','brief-worker'):
            self.assertEqual(s[name]['restart'], 'unless-stopped')
            self.assertEqual(s[name]['image'], s['web']['image'])
            self.assertIn('app_data:/srv/shtab-ai', s[name]['volumes'])
            self.assertIn('asr_models:/srv/shtab-ai/response-models:ro', s[name]['volumes'])
            self.assertEqual(s[name]['environment']['SHTAB_OLLAMA_ENDPOINT'], '${SHTAB_OLLAMA_ENDPOINT:-http://ollama:11434}')
        self.assertEqual(s['asr-download']['environment']['HF_HUB_OFFLINE'], '0')
        self.assertIn('asr_models:/srv/shtab-ai/response-models', s['asr-download']['volumes'])
        self.assertNotIn('ollama-bridge', s)

    def test_https_host_validation_and_ca_preservation(self):
        module = runpy.run_path(str(ROOT/'scripts/configure-https.py'))
        validate = module['validate_host']
        for value in ('https://server', 'server:443', '{$BAD}', 'a\nlocalhost', '999.1.1.1', '::1'):
            with self.assertRaises(ValueError): validate(value)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'.env').write_text('SHTAB_TRUSTED_HOSTS=localhost\nOTHER=value\n')
            (root/'tls-data').mkdir()
            (root/'tls-data/key').write_text('existing-ca')
            module['configure'](root, '192.168.10.134')
            module['configure'](root, 'shtab.office.example')
            self.assertEqual((root/'tls-data/key').read_text(), 'existing-ca')
            env = (root/'.env').read_text()
            self.assertEqual(env.count('SHTAB_HTTPS_HOST='), 1)
            self.assertIn('OTHER=value', env)
            self.assertIn('SHTAB_HTTPS_HOST=shtab.office.example', env)
            self.assertEqual((root/'.env').stat().st_mode & 0o777, 0o600)

    def test_https_forwarded_scheme_and_secure_session(self):
        import ast
        from datetime import timedelta
        from flask import Flask, request, session
        from werkzeug.middleware.proxy_fix import ProxyFix
        tree = ast.parse((ROOT/'app/app.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'create_app')
        setup = []
        for statement in function.body:
            if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == 'store' for t in statement.targets): break
            setup.append(statement)
        function.body = setup + [ast.Return(value=ast.Name(id='app',ctx=ast.Load()))]
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        ns = dict(Flask=Flask, ProxyFix=ProxyFix, os=os, timedelta=timedelta, __name__=__name__)
        exec(compile(module,'actual-app-setup','exec'), ns)
        with patch.dict(os.environ, SHTAB_SESSION_COOKIE_SECURE='1', SHTAB_TRUSTED_HOSTS='shtab.office.example'):
            app = ns['create_app'](settings={'secret_key':'x'*64})
        @app.route('/test',methods=['POST'])
        def view():
            if request.headers['Origin'] != request.host_url.rstrip('/'): return '',403
            session['test'] = 'ok'
            return 'ok'
        client = app.test_client()
        response = client.post('/test',base_url='http://shtab.office.example',headers={'X-Forwarded-Proto':'https','Origin':'https://shtab.office.example'})
        self.assertEqual(response.status_code,200)
        self.assertIn('Secure',response.headers['Set-Cookie'])
        bad = client.post('/test',base_url='http://shtab.office.example',headers={'X-Forwarded-Proto':'https','Origin':'https://other.example'})
        self.assertEqual(bad.status_code,403)

    def test_https_network_and_storage(self):
        services = yaml.safe_load((ROOT/'compose.yaml').read_text())['services']
        self.assertTrue(services['web']['ports'][0].startswith('127.0.0.1:'))
        self.assertEqual(services['web']['environment']['SHTAB_SESSION_COOKIE_SECURE'], '1')
        self.assertIn('./tls-data:/data', services['proxy']['volumes'])
        self.assertEqual(services['proxy']['depends_on']['web']['condition'], 'service_healthy')

    def test_entrypoint_secrets_and_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            temp = Path(d)
            (temp/'run/secrets').mkdir(parents=True)
            (temp/'etc/shtab-ai').mkdir(parents=True)
            (temp/'run/secrets/db_password').write_text('test-db-password\n')
            (temp/'run/secrets/flask_secret').write_text('x'*64)
            def redirected(value):
                return temp/str(value).lstrip('/')
            old_umask = os.umask(0o022)
            try:
                with patch('pathlib.Path', redirected), patch.object(sys,'argv',['entry','python','manage.py','check']), patch('os.execvp') as execute:
                    runpy.run_path(str(ROOT/'app/installer-entrypoint.py'),run_name='__main__')
                cfg = json.loads((temp/'etc/shtab-ai/secretary-web.json').read_text())
                self.assertNotIn('password', cfg['database'])
                self.assertEqual((temp/'run/shtab-ai/pgpass').stat().st_mode & 0o777, 0o600)
                self.assertEqual(cfg['database']['host'], 'db')
                execute.assert_called_once_with('python',['python','manage.py','check'])
            finally:
                os.umask(old_umask)

    def test_asr_resume_and_no_redownload(self):
        with tempfile.TemporaryDirectory() as d:
            temp = Path(d)
            def redirected(value):
                return temp/str(value).lstrip('/')
            loads = []
            downloads = []
            api_calls = []
            class Api:
                def model_info(self, repo, **kwargs):
                    api_calls.append(kwargs.get('revision'))
                    return types.SimpleNamespace(sha='fixed-commit', siblings=[types.SimpleNamespace(rfilename='model.bin', size=5, lfs=types.SimpleNamespace(sha256=hashlib.sha256(b'model').hexdigest()))])
            def snapshot(**kw):
                downloads.append(kw)
                if len(downloads) == 1:
                    raise RuntimeError('network interrupted')
                (type(temp)(kw['local_dir'])/'model.bin').write_bytes(b'model')
                return kw['local_dir']
            def model(*a, **kw):
                loads.append((a,kw))
                return types.SimpleNamespace(transcribe=lambda *a, **k: (iter([]), None))
            sys.path.insert(0, str(ROOT/'scripts'))
            modules = {'huggingface_hub': types.SimpleNamespace(HfApi=Api,snapshot_download=snapshot),
                       'faster_whisper': types.SimpleNamespace(WhisperModel=model)}
            with patch('pathlib.Path',redirected), patch.dict(sys.modules,modules), patch.dict(os.environ, {'SHTAB_PROGRESS_FILE': str(temp/'progress.json')}), patch('model_progress.Path', lambda value: temp/'progress.json'):
                with self.assertRaisesRegex(RuntimeError,'network interrupted'):
                    runpy.run_path(str(ROOT/'scripts/download-asr.py'))
                runpy.run_path(str(ROOT/'scripts/download-asr.py'))
                runpy.run_path(str(ROOT/'scripts/download-asr.py'))
                (temp/'srv/shtab-ai/response-models/turbo/model.bin').write_bytes(b'wrong')
                runpy.run_path(str(ROOT/'scripts/download-asr.py'))
            self.assertEqual(api_calls, [None, 'fixed-commit', 'fixed-commit', 'fixed-commit'])
            self.assertEqual(len(downloads),3)
            self.assertEqual(downloads[0]['revision'],downloads[1]['revision'])
            self.assertEqual(len(loads),3)

    def test_asr_bad_existing_model_blocks_ready(self):
        with tempfile.TemporaryDirectory() as d:
            temp = Path(d)
            model_root = temp/'srv/shtab-ai/response-models/turbo'
            model_root.mkdir(parents=True)
            (model_root/'shtab-model.json').write_text('{}')
            def broken(*a,**kw): raise RuntimeError('corrupt model')
            sys.path.insert(0, str(ROOT/'scripts'))
            (model_root/'model.bin').write_bytes(b'model')
            class Api:
                def model_info(self, repo, **kwargs):
                    return types.SimpleNamespace(sha='fixed-commit', siblings=[types.SimpleNamespace(rfilename='model.bin', size=5, lfs=types.SimpleNamespace(sha256=hashlib.sha256(b'model').hexdigest()))])
            modules = {'huggingface_hub': types.SimpleNamespace(HfApi=Api,snapshot_download=None),
                       'faster_whisper': types.SimpleNamespace(WhisperModel=broken)}
            with patch('pathlib.Path',lambda value: temp/str(value).lstrip('/')), patch.dict(sys.modules,modules), patch.dict(os.environ, {'SHTAB_PROGRESS_FILE': str(temp/'progress.json')}), patch('model_progress.Path', lambda value: temp/'progress.json'):
                with self.assertRaisesRegex(RuntimeError,'corrupt model'):
                    runpy.run_path(str(ROOT/'scripts/download-asr.py'))

    def test_workers_serialize_llm_jobs_and_stop(self):
        with tempfile.TemporaryDirectory() as d:
            temp = Path(d)
            body = "import os,time\nfrom pathlib import Path\np=Path('events')\nwith p.open('a') as f:f.write('START '+__file__+'\\n')\ntime.sleep(.2)\nwith p.open('a') as f:f.write('END '+__file__+'\\n')\n"
            for name in ('llm_worker013.py','brief_worker017.py'):
                (temp/name).write_text(body)
            env = dict(os.environ,SHTAB_LLM_LOCK=str(temp/'llm.lock'))
            processes = [subprocess.Popen([sys.executable,str(ROOT/'scripts/worker-loop.py'),n],cwd=d,env=env) for n in ('llm_worker013.py','brief_worker017.py')]
            try:
                deadline = time.monotonic()+8
                while time.monotonic()<deadline:
                    if (temp/'events').exists() and len((temp/'events').read_text().splitlines())>=4:
                        break
                    time.sleep(.05)
                lines = (temp/'events').read_text().splitlines()
                self.assertEqual([x.split()[0] for x in lines[:4]],['START','END','START','END'])
                self.assertNotEqual(lines[0].split()[1],lines[2].split()[1])
            finally:
                for p in processes:
                    p.terminate()
                for p in processes:
                    p.wait(timeout=5)
            self.assertTrue(all(p.returncode==0 for p in processes))

if __name__ == '__main__': unittest.main()

