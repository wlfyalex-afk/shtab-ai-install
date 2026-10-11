import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class DesktopShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base/'home'
        self.desktop = self.home/'Рабочий стол'
        self.desktop.mkdir(parents=True)
        self.app = self.base/'app'
        self.app.mkdir()
        (self.app/'shtab-ai-root.crt').write_text('public certificate')
        (self.app/'.env').write_text('SHTAB_HTTPS_HOST=192.168.10.115\n')
        self.module = runpy.run_path(str(ROOT/'scripts/create-desktop-shortcut.py'))

    def tearDown(self):
        self.temp.cleanup()

    def create(self, trusted=True):
        account = types.SimpleNamespace(pw_dir=str(self.home), pw_uid=1000, pw_gid=1000)
        with patch('pwd.getpwnam', return_value=account), patch('os.chown'), patch('os.fchown'), \
             patch('subprocess.check_output', return_value=str(self.desktop)), \
             patch('subprocess.run', return_value=types.SimpleNamespace(returncode=0 if trusted else 1)) as mark, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.module['create'](self.app, 'user')
        return output.getvalue(), mark

    def test_launches_browser_as_desktop_user_and_marks_trusted(self):
        self.create()
        p = self.desktop/'ShtabAI.desktop'
        text = p.read_text()
        self.assertIn('Type=Application\n', text)
        self.assertIn('Exec=xdg-open https://192.168.10.115/login\n', text)
        self.assertIn('Terminal=false\n', text)
        self.assertTrue(p.stat().st_mode & 0o111)
        _, mark = self.create()
        self.assertEqual(mark.call_args_list[0].args[0][:4], ['runuser','-u','user','--'])

    def test_upgrades_old_link_and_preserves_unrelated_files(self):
        p = self.desktop/'ShtabAI.desktop'
        p.write_text('[Desktop Entry]\nVersion=1.0\nType=Link\nName=Штаб.AI\nURL=https://192.168.10.115/login\nIcon=web-browser\n')
        self.create()
        self.assertIn('Type=Application', p.read_text())
        p.write_text('custom shortcut')
        self.create()
        self.assertEqual(p.read_text(), 'custom shortcut')

    def test_missing_session_explains_allow_launching(self):
        output, _ = self.create(trusted=False)
        self.assertIn('Разрешить запуск', output)
        record = json.loads((self.app/'desktop-shortcut.json').read_text())
        self.assertEqual(record['url'], 'https://192.168.10.115/login')

    def test_exports_public_certificate_without_changing_installation_permissions(self):
        self.app.chmod(0o700)
        self.create()
        certificate = self.home/'shtab-ai-root.crt'
        self.assertEqual(certificate.read_text(), 'public certificate')
        self.assertEqual(certificate.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.app.stat().st_mode & 0o777, 0o700)
        (self.app/'shtab-ai-root.crt').write_text('replacement certificate')
        self.create()
        self.assertEqual(certificate.read_text(), 'replacement certificate')
        self.assertEqual(list(self.home.glob('.shtab-certificate-*')), [])

    def test_certificate_symlink_does_not_modify_target(self):
        target = self.home/'other'
        target.write_text('keep this')
        (self.home/'shtab-ai-root.crt').symlink_to(target)
        self.create()
        self.assertEqual(target.read_text(), 'keep this')
