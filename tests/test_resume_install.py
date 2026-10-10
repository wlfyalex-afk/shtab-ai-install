import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ResumeTests(unittest.TestCase):
    def run_resume(self, active='failed', status='FAILED line=69', missing=False):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            app = base / 'app'
            state = base / 'state'
            app.mkdir()
            state.mkdir()
            for name in ('installation-created', '.env', 'compose.yaml',
                         'secrets/db_password', 'secrets/flask_secret', 'scripts/provision.sh'):
                file = app / name
                file.parent.mkdir(exist_ok=True)
                file.write_text('preserved')
            if missing:
                (app / 'secrets/db_password').unlink()
            (state / 'status').write_text(status)
            bin_dir = base / 'bin'
            bin_dir.mkdir()
            command = bin_dir / 'systemctl'
            command.write_text('#!/bin/bash\necho "$*" >> "$CALLS"\n'
                               'if [[ $1 == show ]]; then echo "$ACTIVE"; fi\n')
            command.chmod(0o755)
            script = (ROOT / 'scripts/resume-install.sh').read_text()
            script = script.replace('[[ $EUID -eq 0 ]]', '[[ 1 -eq 1 ]]')
            script = script.replace('/opt/shtab-ai-021', str(app))
            script = script.replace('/var/lib/shtab-ai-021', str(state))
            before = {str(p): p.read_bytes() for p in app.rglob('*') if p.is_file()}
            result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                                    env=dict(os.environ, PATH=str(bin_dir)+':'+os.environ['PATH'],
                                             CALLS=str(base/'calls'), ACTIVE=active))
            after = {str(p): p.read_bytes() for p in app.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            calls = (base/'calls').read_text() if (base/'calls').exists() else ''
            return result, calls, (state/'status').read_text()

    def test_failed_restarts_without_changing_configuration(self):
        result, calls, status = self.run_resume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('start --no-block', calls)
        self.assertEqual(status, 'RESUMING\n')

    def test_running_does_not_start_duplicate_or_reset_status(self):
        result, calls, status = self.run_resume(active='activating', status='DOWNLOADING_WHISPER')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('start --no-block', calls)
        self.assertNotIn('reset-failed', calls)
        self.assertEqual(status, 'DOWNLOADING_WHISPER')

    def test_ready_does_not_restart_provisioning(self):
        result, calls, status = self.run_resume(active='inactive', status='READY_FOR_ADMIN')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('start --no-block', calls)
        self.assertEqual(status, 'READY_FOR_ADMIN')

    def test_lost_secret_blocks_restart(self):
        result, calls, _ = self.run_resume(missing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('start --no-block', calls)

    def test_standalone_bootstraps_embed_current_resume_helper(self):
        helper = (ROOT/'scripts/resume-install.sh').read_text()
        for name in ('Install-ShtabAI-Ubuntu.sh', 'windows/Install-WSL.ps1'):
            self.assertIn(helper, (ROOT/name).read_text(encoding='utf-8-sig'))
