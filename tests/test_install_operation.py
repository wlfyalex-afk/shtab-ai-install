import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from install_operation import operation

spec = importlib.util.spec_from_file_location('install_progress_display', ROOT / 'scripts/install-progress.py')
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


class InstallOperationTests(unittest.TestCase):
    def test_build_layer_bytes_and_percentage_then_package_install(self):
        layer = '#6 sha256:f9b274ee 18.32MB / 183.2MB 0.2s'
        data = operation('BUILDING_APP', layer)
        self.assertEqual(data['percent'], 10)
        self.assertIn('18.32 MB', data['text'])
        self.assertNotIn('sha256', data['text'])
        self.assertEqual(operation('BUILDING_APP', '#6 sha256:f9b274ee 250kB / 1MB')['percent'], 25)
        data = operation('BUILDING_APP', layer + '\n#7 1.2 Downloading faster_whisper-1.whl (1.2 MB)')
        self.assertIn('faster_whisper', data['text'])
        self.assertEqual(data['percent'], -1)
        data = operation('BUILDING_APP', '#14 exporting layers')
        self.assertIn('Сохранение', data['title'])

    def test_unknown_volume_has_no_invented_percentage(self):
        data = operation('BUILDING_APP', '')
        self.assertEqual(data['percent'], -1)
        self.assertIn('ffmpeg', data['text'])
        self.assertIsNone(operation('DOWNLOADING_QWEN'))

    def test_live_display_reuses_lines_and_clips_wrapping(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), patch('shutil.get_terminal_size', return_value=__import__('os').terminal_size((40, 24))):
            progress.display('Текущий слой\n' + 'x' * 100, live=True)
        output = stream.getvalue()
        self.assertTrue(output.startswith('\033[H'))
        self.assertNotIn('\033[2J', output)
        self.assertIn('x' * 39, output)
        self.assertNotIn('x' * 40, output)
