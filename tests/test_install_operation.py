import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from install_operation import operation

spec = importlib.util.spec_from_file_location('install_progress_display', ROOT / 'scripts/install-progress.py')
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


class InstallOperationTests(unittest.TestCase):
    def test_journal_is_scoped_to_current_invocation(self):
        invocation = 'a' * 32
        with patch.object(progress, 'command', side_effect=[invocation, 'current attempt']) as run:
            self.assertEqual(progress.current_journal(), 'current attempt')
        self.assertIn('_SYSTEMD_INVOCATION_ID=' + invocation, run.call_args.args[0])
        with patch.object(progress, 'command', return_value='') as run:
            self.assertEqual(progress.current_journal(), '')
            self.assertEqual(run.call_count, 1)

    def test_normal_snapshot_omits_service_diagnostics_and_old_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'status'
            state.write_text('INSTALLING_DEPENDENCIES')
            stream = io.StringIO()
            with patch.object(sys, 'argv', ['install-progress.py', '--once', '--state-file', str(state)]), contextlib.redirect_stdout(stream), patch.object(progress, 'command') as run:
                progress.main()
            run.assert_not_called()
            self.assertIn('Устанавливаем Docker', stream.getvalue())
            self.assertNotIn('Последние сообщения', stream.getvalue())
            self.assertLessEqual(len(stream.getvalue().splitlines()), 7)

    def test_json_preserves_russian_through_windows_console_code_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'status'
            state.write_text('INSTALLING_DEPENDENCIES')
            stream = io.StringIO()
            with patch.object(sys, 'argv', ['install-progress.py', '--json', '--state-file', str(state)]), contextlib.redirect_stdout(stream):
                progress.main()
            raw = stream.getvalue().encode('ascii')
            for codepage in ('cp866', 'cp1251', 'utf-8'):
                data = json.loads(raw.decode(codepage))
                self.assertEqual(data['operation']['title'], 'Установка зависимостей')
                self.assertIn('Ubuntu', data['operation']['text'])

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
