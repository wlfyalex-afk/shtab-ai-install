import contextlib
import io
import getpass
import zoneinfo
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class BootstrapInputTests(unittest.TestCase):
    def setUp(self):
        store = types.ModuleType('store')
        store.Store = None
        with patch.dict(sys.modules, store=store):
            self.module = runpy.run_path(str(ROOT / 'app/bootstrap.py'))

    def test_empty_long_and_control_inputs_retry_same_question(self):
        with patch('builtins.input', side_effect=['', 'x'*201, '\x1b[A', 'wlfy']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.module['required']('Name: '), 'wlfy')

    def test_invalid_login_timezone_and_password_retry_without_losing_names(self):
        with patch('builtins.input', side_effect=['Org', 'Admin', '12', 'WLFY', '/bad', '']), \
             patch.object(self.module['credentials'].__globals__['getpass'], 'getpass', side_effect=['short', 'a'*14, 'b'*14, 'c'*14, 'c'*14]), \
             contextlib.redirect_stdout(io.StringIO()):
            result = self.module['credentials']()
        self.assertEqual(result, ('Org', 'Admin', 'wlfy', 'Asia/Vladivostok', 'c'*14))

    def test_end_of_input_and_ctrl_c_stop_instead_of_repeating_forever(self):
        for error in (EOFError, KeyboardInterrupt):
            with patch('builtins.input', side_effect=error), self.assertRaises(error):
                self.module['required']('Name: ')
