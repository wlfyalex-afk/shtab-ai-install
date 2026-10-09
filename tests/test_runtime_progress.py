import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from model_progress import Reporter, read_progress, describe
spec = importlib.util.spec_from_file_location('runtime_config', ROOT / 'scripts/runtime-config.py')
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class RuntimeTests(unittest.TestCase):
    def test_ip_change_preserves_ca_and_custom_hosts_and_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'tls-data').mkdir()
            ca = root / 'tls-data/root.crt'
            ca.write_bytes(b'existing trusted CA')
            env = root / '.env'
            env.write_text('SHTAB_HTTPS_HOST=192.168.1.10\nSHTAB_TRUSTED_HOSTS=localhost,192.168.1.10,office.example\nSECRET=unchanged\n')
            self.assertEqual(config.reconcile(root, 'http://172.20.0.1:11435', '192.168.1.20', 'BUILDING_APP'), 'DEFERRED')
            self.assertNotIn('192.168.1.20', env.read_text())
            self.assertEqual(config.reconcile(root, 'http://172.20.0.1:11435', '192.168.1.20', 'READY_FOR_ADMIN'), 'CHANGED')
            text = env.read_text()
            self.assertNotIn('192.168.1.10', text)
            self.assertIn('office.example', text)
            self.assertIn('SECRET=unchanged', text)
            self.assertEqual(ca.read_bytes(), b'existing trusted CA')
            self.assertEqual(config.reconcile(root, 'http://172.20.0.1:11435', '192.168.1.20', 'READY_FOR_ADMIN'), 'CHANGED')
            (root / '.runtime-config-pending').unlink()
            self.assertEqual(config.reconcile(root, 'http://172.20.0.1:11435', '192.168.1.20', 'READY_FOR_ADMIN'), 'SAME')
            config.reconcile(root, 'http://172.20.0.1:11435', '', 'READY_FOR_ADMIN')
            self.assertIn('SHTAB_HTTPS_SITES=https://localhost\n', env.read_text())
            self.assertNotIn('192.168.1.20', env.read_text())

    def test_progress_resumed_bytes_do_not_count_as_download_speed(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            reporter = Reporter('qwen', state / 'qwen-progress.json')
            with patch('model_progress.time.monotonic', side_effect=[10, 12, 14]):
                reporter.update(500, 1000, 'слой A', force=True)
                self.assertEqual(read_progress(state, 'DOWNLOADING_QWEN')['bytes_per_second'], 0)
                reporter.update(700, 1000, 'слой A', force=True)
                data = read_progress(state, 'DOWNLOADING_QWEN')
                self.assertEqual(data['bytes_per_second'], 100)
                self.assertEqual(data['eta_seconds'], 3)
                reporter.update(200, 2000, 'слой B', force=True)
                self.assertEqual(read_progress(state, 'DOWNLOADING_QWEN')['bytes_per_second'], 0)
            self.assertIsNone(read_progress(state, 'STARTING_SERVICES'))
            data = json.loads(reporter.path.read_text())
            data.update(updated_at=time.time()-30, bytes_per_second=100, eta_seconds=18)
            reporter.path.write_text(json.dumps(data))
            stale = read_progress(state, 'DOWNLOADING_QWEN')
            self.assertTrue(stale['stale'])
            self.assertIsNone(stale['eta_seconds'])
            self.assertIn('ожидаем новые данные', describe(stale))


if __name__ == '__main__':
    unittest.main()
