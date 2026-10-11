import os
from pathlib import Path
import pty
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FinishTests(unittest.TestCase):
    def run_finish(self, count='0', status='READY_FOR_ADMIN', https_code=0,
                   interactive=True, collision=False, bootstrap_code=0, wait=False):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            app, state, bin_dir = (base / name for name in ('app', 'state', 'bin'))
            for path in (app / 'scripts', state, bin_dir):
                path.mkdir(parents=True)
            (state / 'status').write_text(status)
            (app / '.env').write_text('SHTAB_HTTPS_HOST=localhost\n')
            (app / 'acceleration').write_text('cpu\n')
            (app / 'shtab-ai-root.crt').write_text('our-ca')
            certificate = base / 'system.crt'
            if collision:
                certificate.write_text('foreign-ca')
            commands = {
                app / 'scripts/check-https.sh': 'echo https >> "$CALLS"; exit "$HTTPS_CODE"',
                app / 'scripts/dc.sh': 'echo "$*" >> "$CALLS"; if [[ "$*" == *psql* ]]; then echo "$COUNT"; else exit "$BOOTSTRAP_CODE"; fi',
                bin_dir / 'update-ca-certificates': 'echo trust >> "$CALLS"',
            }
            for path, body in commands.items():
                path.write_text('#!/bin/bash\n' + body + '\n')
                path.chmod(0o755)
            (app / 'scripts/install-progress.py').write_text(
                'import os, sys\nwith open(os.environ["CALLS"], "a") as f: f.write("progress " + " ".join(sys.argv[1:]) + "\\n")\n'
                f'from pathlib import Path\nPath({str(state / "status")!r}).write_text("READY_FOR_ADMIN")\n')
            (app / 'scripts/create-desktop-shortcut.py').write_text(
                'import os\nwith open(os.environ["CALLS"], "a") as f: f.write("shortcut\\n")\n')
            script = (ROOT / 'scripts/finish-install.sh').read_text()
            script = script.replace('/opt/shtab-ai-021', str(app)).replace('/var/lib/shtab-ai-021', str(state))
            script = script.replace('/usr/local/share/ca-certificates/shtab-ai-021.crt', str(certificate))
            script = script.replace('[[ $EUID -eq 0 ]]', '[[ 1 -eq 1 ]]')
            env = dict(os.environ, PATH=str(bin_dir)+':'+os.environ['PATH'], CALLS=str(base/'calls'),
                       COUNT=count, HTTPS_CODE=str(https_code), BOOTSTRAP_CODE=str(bootstrap_code))
            master, slave = pty.openpty()
            try:
                result = subprocess.run(['bash', '-c', script, 'finish'] + (['--wait'] if wait else []), env=env,
                                        stdin=slave if interactive else subprocess.DEVNULL,
                                        stdout=slave if interactive else subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=10)
            finally:
                os.close(slave)
                os.close(master)
            calls = (base/'calls').read_text() if (base/'calls').exists() else ''
            cert = certificate.read_text() if certificate.exists() else None
            return result.returncode, calls, cert

    def test_ready_prompts_for_admin_and_creates_shortcut(self):
        code, calls, cert = self.run_finish()
        self.assertEqual(code, 0)
        self.assertEqual(cert, 'our-ca')
        self.assertLess(calls.index('trust'), calls.index('bootstrap021.py'))
        self.assertLess(calls.index('bootstrap021.py'), calls.index('shortcut'))

    def test_wait_continues_from_provisioning_to_setup(self):
        code, calls, _ = self.run_finish(status='DOWNLOADING_WHISPER', wait=True)
        self.assertEqual(code, 0)
        self.assertLess(calls.index('progress --timeout 10800'), calls.index('https'))
        self.assertIn('bootstrap021.py', calls)

    def test_existing_admin_is_preserved(self):
        code, calls, _ = self.run_finish(count='1', interactive=False)
        self.assertEqual(code, 0)
        self.assertNotIn('bootstrap021.py', calls)
        self.assertIn('shortcut', calls)

    def test_failed_https_does_not_create_user_or_trust_ca(self):
        code, calls, cert = self.run_finish(https_code=1)
        self.assertNotEqual(code, 0)
        self.assertNotIn('trust', calls)
        self.assertNotIn('bootstrap021.py', calls)
        self.assertIsNone(cert)

    def test_not_ready_does_not_modify_system(self):
        code, calls, cert = self.run_finish(status='FAILED line=100')
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, '')
        self.assertIsNone(cert)

    def test_foreign_certificate_is_preserved(self):
        code, calls, cert = self.run_finish(collision=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(cert, 'foreign-ca')
        self.assertNotIn('trust', calls)
        self.assertNotIn('bootstrap021.py', calls)

    def test_no_terminal_requires_interactive_completion(self):
        code, calls, _ = self.run_finish(interactive=False)
        self.assertNotEqual(code, 0)
        self.assertNotIn('bootstrap021.py', calls)
        self.assertNotIn('shortcut', calls)

    def test_bootstrap_error_never_reports_completion(self):
        code, calls, _ = self.run_finish(bootstrap_code=1)
        self.assertNotEqual(code, 0)
        self.assertNotIn('shortcut', calls)

    def test_resume_waits_and_runs_finalizer(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder)
            (app/'scripts').mkdir()
            for name in ('resume-install.sh', 'finish-install.sh'):
                (app/'scripts'/name).write_text(f'echo {name} "$@"\n')
            script = (ROOT/'shtabctl').read_text().replace('/opt/shtab-ai-021', str(app))
            script = script.replace('[[ $EUID -eq 0 ]]', '[[ 1 -eq 1 ]]')
            result = subprocess.run(['bash', '-c', script, 'shtabctl', 'resume'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.splitlines(), ['resume-install.sh', 'finish-install.sh --wait'])

    def test_old_installation_bootstrap_embeds_current_finalizer(self):
        self.assertIn((ROOT/'scripts/finish-install.sh').read_text(),
                      (ROOT/'Install-ShtabAI-Ubuntu.sh').read_text())
