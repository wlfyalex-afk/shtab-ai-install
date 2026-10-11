import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UbuntuUninstallTests(unittest.TestCase):
    def run_uninstall(self, partial=False, answer='DELETE-SHTAB-021'):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            app, state, bin_dir, cache = (base / name for name in ('app', 'state', 'bin', 'cache'))
            for path in (app / 'scripts', state, bin_dir, cache):
                path.mkdir(parents=True)
            (app / 'installation-created').touch()
            (app / 'compose.yaml').write_text('name: shtab-ai-021\n')
            (cache / 'model.bin').write_text('cached-model')
            (base / 'ollama-running').touch()
            volumes = ['ollama_models'] if partial else ['db_data', 'app_data', 'asr_models', 'ollama_models']
            for name in volumes:
                (base / name).touch()
            dc = app / 'scripts/dc.sh'
            dc.write_text('''#!/bin/bash
printf 'compose %s\\n' "$*" >> "$CALLS"
if [[ "$*" == *down* && "$*" == *"--profile *"* ]]; then
    rm -f "$FIXTURE/ollama-running"
fi
''')
            docker = bin_dir / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
base=Path(os.environ['FIXTURE']); args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f: f.write('docker '+' '.join(args)+'\\n')
if args[0] == 'info': sys.exit(0)
if args[:2] == ['volume','inspect']:
    key=args[2].removeprefix('shtab-ai-021_')
    if not (base/key).exists(): sys.exit(1)
    if '--format' in args: print('shtab-ai-021:'+key)
    sys.exit(0)
if args[:2] == ['volume','rm']:
    for name in args[2:]:
        key=name.removeprefix('shtab-ai-021_')
        if key=='ollama_models' and (base/'ollama-running').exists():
            print('volume is in use',file=sys.stderr); sys.exit(1)
        (base/key).unlink()
    sys.exit(0)
if args[:2] == ['image','inspect']: sys.exit(1)
sys.exit('Unexpected Docker command')
''')
            docker.chmod(0o755)
            for name in ('systemctl', 'update-ca-certificates'):
                p = bin_dir / name
                p.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n')
                p.chmod(0o755)
            (base / 'units').mkdir()
            script = (ROOT / 'Uninstall-ShtabAI-Ubuntu.sh').read_text()
            script = script.replace('/opt/shtab-ai-021', str(app)).replace('/var/lib/shtab-ai-021', str(state))
            script = script.replace('/etc/systemd/system', str(base / 'units'))
            script = script.replace('/usr/local/share/ca-certificates/shtab-ai-021.crt', str(base / 'system.crt'))
            script = script.replace('[[ $EUID -ne 0 ]]', '[[ 1 -ne 1 ]]')
            entry = base / 'uninstall.sh'
            entry.write_text(script)
            result = subprocess.run(['bash', str(entry)], input=answer+'\n', text=True,
                                    capture_output=True, timeout=10,
                                    env=dict(os.environ, PATH=str(bin_dir)+':'+os.environ['PATH'],
                                             FIXTURE=str(base), CALLS=str(base/'calls')))
            calls = (base / 'calls').read_text() if (base / 'calls').exists() else ''
            return result, calls, app.exists(), (cache/'model.bin').read_text(), (base/'ollama-running').exists()

    def test_all_profiles_removed_before_volume_deletion(self):
        result, calls, exists, cache, running = self.run_uninstall()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('compose --profile * down --remove-orphans', calls)
        self.assertLess(calls.index('down --remove-orphans'), calls.index('docker volume rm'))
        self.assertFalse(exists)
        self.assertFalse(running)
        self.assertEqual(cache, 'cached-model')
        self.assertNotIn('prune', calls)

    def test_repeat_after_partial_removal_cleans_remaining_ollama(self):
        result, calls, exists, cache, running = self.run_uninstall(partial=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('docker volume rm shtab-ai-021_ollama_models', calls)
        self.assertFalse(exists)
        self.assertFalse(running)
        self.assertEqual(cache, 'cached-model')

    def test_cancellation_keeps_running_container_and_data(self):
        result, calls, exists, _, running = self.run_uninstall(answer='cancel')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(exists)
        self.assertTrue(running)
        self.assertNotIn('down --remove-orphans', calls)
        self.assertNotIn('docker volume rm', calls)
