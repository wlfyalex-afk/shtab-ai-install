import os
from pathlib import Path
import subprocess
import unittest


class GuestMemoryTests(unittest.TestCase):
    def test_host_ollama_allows_smaller_guest(self):
        script = (Path(__file__).resolve().parents[1] / 'install.sh').read_text()
        policy = script[script.index('minimum_memory='):script.index('space_target=')]
        for mem, external, accepted in (
            (8388608, 'http://host:11435', True),
            (7900000, 'http://host:11435', False),
            (8388608, '', False),
            (10485760, '', True),
        ):
            with self.subTest(mem=mem, external=external):
                result = subprocess.run(
                    ['bash', '-c', 'set -eu; mem=' + str(mem) + '\n' + policy],
                    env={**os.environ, 'SHTAB_EXTERNAL_OLLAMA_ENDPOINT': external},
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)
