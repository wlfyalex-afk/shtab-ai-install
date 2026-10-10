import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('support_report', Path(__file__).parents[1] / 'scripts/support-report.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class SupportReportTests(unittest.TestCase):
    def test_service_failure_does_not_stop_collection_or_expose_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('PRIVATE_CONFIG_SENTINEL')
            (root / 'secrets').mkdir()
            (root / 'secrets/db_password').write_text('PRIVATE_DATABASE_SENTINEL')
            def run(args, **kwargs):
                self.assertEqual(kwargs['timeout'], 8)
                if args[0] == 'free':
                    raise FileNotFoundError('missing free')
                if args[0] == 'df':
                    raise subprocess.TimeoutExpired(args, 8)
                return types.SimpleNamespace(returncode=1, stdout='service unavailable\npassword=PRIVATE_LOG_SENTINEL\nBearer PRIVATE_TOKEN_SENTINEL\n')
            with patch.object(support.subprocess, 'run', side_effect=run):
                text = support.report(root)
            for secret in ('PRIVATE_CONFIG_SENTINEL', 'PRIVATE_DATABASE_SENTINEL', 'PRIVATE_LOG_SENTINEL', 'PRIVATE_TOKEN_SENTINEL'):
                self.assertNotIn(secret, text)
            self.assertIn('missing free', text)
            self.assertIn('код: timeout', text)
            self.assertIn('Журнал приложения', text)
            self.assertIn('[скрыто]', text)

    def test_save_is_private_and_survives_multiple_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            user = types.SimpleNamespace(pw_dir=directory, pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(support.pwd, 'getpwuid', return_value=user), patch.dict(os.environ, {}, clear=True):
                one = support.save('first report')
                two = support.save('second report')
            self.assertNotEqual(one, two)
            self.assertEqual(one.read_text(), 'first report')
            self.assertEqual(two.read_text(), 'second report')
            self.assertEqual(one.stat().st_mode & 0o777, 0o600)

    def test_native_windows_ollama_not_queried_as_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'windows-acceleration').write_text('cpu')
            with patch.object(support, 'capture', return_value='ok') as capture:
                support.report(root)
            commands = [call.args[1] for call in capture.call_args_list]
            self.assertFalse(any(cmd[-1] == 'ollama' for cmd in commands))
