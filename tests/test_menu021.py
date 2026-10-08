import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.copy = self.base/'backup with space'
        self.copy.mkdir()
        (self.copy/'manifest.json').write_text('{}')
        self.calls = self.base/'calls'
        self.env = dict(os.environ, COPY=str(self.copy), CALLS=str(self.calls))
        self.ctl = self.base/'ctl'
        self.ctl.write_text('#!/bin/bash\nif [[ $1 == backups ]]; then printf "%s\\n" "$COPY"; else printf "%s|%s\\n" "$1" "${2:-}" >> "$CALLS"; fi\n')
        self.ctl.chmod(0o755)
        self.menu = (ROOT/'scripts/menu.sh').read_text().replace('ctl=/opt/shtab-ai-021/shtabctl', f'ctl="{self.ctl}"')

    def tearDown(self): self.temp.cleanup()

    def run_menu(self, answer):
        result = subprocess.run(['bash', '-c', self.menu], input=answer, env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_restore_by_number_and_full_path(self):
        for selection in ('1', str(self.copy)):
            self.run_menu('7\n'+selection+'\n\n0\n')
            self.assertEqual(self.calls.read_text(), 'restore|'+str(self.copy)+'\n')
            self.calls.unlink()

    def test_cancel_and_invalid_selection_never_restore(self):
        for selection in ('0', '', '9999999999999999999', '/opt/shtab-ai-021', 'abc'):
            self.run_menu('7\n'+selection+'\n0\n')
            self.assertFalse(self.calls.exists())

    def test_https_stopped_and_success_and_failure(self):
        (self.base/'scripts').mkdir()
        (self.base/'scripts/dc.sh').write_text((ROOT/'scripts/dc.sh').read_text())
        scripts = self.base/'bin'; scripts.mkdir()
        env = dict(self.env, PATH=str(scripts)+':'+os.environ['PATH'])
        (self.base/'.env').write_text('SHTAB_HTTPS_HOST=192.168.10.134\nSHTAB_HTTPS_BIND_IP=0.0.0.0\n')
        ca = self.base/'tls-data/caddy/pki/authorities/local/root.crt'
        ca.parent.mkdir(parents=True); ca.write_text('mock CA')
        script = (ROOT/'scripts/check-https.sh').read_text().replace('cd /opt/shtab-ai-021', f'cd "{self.base}"')
        def stub(name, body):
            path = scripts/name; path.write_text('#!/bin/bash\n'+body+'\n'); path.chmod(0o755)
        stub('sleep', 'exit 0'); stub('openssl', 'exit 0')
        stub('docker', 'exit 0')
        stub('curl', 'echo called >> "$CALLS"; exit 0')
        result = subprocess.run(['bash','-c',script], env=env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('proxy не запущен', result.stdout)
        self.assertFalse(self.calls.exists())
        stub('docker', 'echo proxy')
        stub('curl', 'printf "%s\\n" "$@" > "$CALLS"; exit 0')
        result = subprocess.run(['bash','-c',script], env=env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('HTTPS исправен', result.stdout)
        self.assertIn('--connect-to\n192.168.10.134:443:127.0.0.1:443', self.calls.read_text())
        stub('curl', 'echo connection refused >&2; exit 7')
        result = subprocess.run(['bash','-c',script], env=env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count('connection refused'), 1)
        self.assertIn('Попытка 5/5', result.stdout)
        self.assertIn('Нет TCP-соединения', result.stdout)


if __name__ == '__main__': unittest.main()
