import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LinuxPlatformTests(unittest.TestCase):
    def detect(self, identifier, version, arch='x86_64', selected='auto'):
        with tempfile.TemporaryDirectory() as folder:
            release = Path(folder) / 'os-release'
            release.write_text(f'ID={identifier}\nVERSION_ID={version}\nPRETTY_NAME="Test Linux"\n')
            return subprocess.run([
                'bash', '-c', 'set -e; source "$1"; shtab_detect_linux "$2" "$3"; shtab_select_linux "$4"; echo "$SHTAB_LINUX_PROFILE"',
                'test', str(ROOT/'scripts/linux-platform.sh'), str(release), arch, selected,
            ], capture_output=True, text=True)

    def test_supported_versions_and_manual_selection(self):
        for identifier, version, expected in [('ubuntu', '24.04', 'ubuntu'), ('astra', '1.8_x86-64', 'astra'), ('astra', '1.8.4', 'astra')]:
            for selected in ('auto', expected):
                result = self.detect(identifier, version, selected=selected)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_wrong_version_architecture_and_mismatched_choice_stop(self):
        cases = [('ubuntu', '22.04', 'x86_64', 'auto'), ('astra', '1.7', 'x86_64', 'auto'),
                 ('debian', '12', 'x86_64', 'auto'), ('ubuntu', '24.04', 'aarch64', 'auto'),
                 ('astra', '1.8_x86-64', 'x86_64', 'ubuntu'), ('ubuntu', '24.04', 'x86_64', 'astra')]
        for args in cases:
            result = self.detect(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')

    def install(self, profile, missing=''):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base/'apt/sources.list.d').mkdir(parents=True)
            helper = base/'helper.sh'
            helper.write_text((ROOT/'scripts/linux-platform.sh').read_text().replace('/etc/apt/', str(base/'apt')+'/'))
            script = r'''
set -e
source "$1"
shtab_detect_linux() { SHTAB_LINUX_PROFILE=$PROFILE; SHTAB_LINUX_NAME=$PROFILE; }
docker() { [[ -f "$WORK/installed" ]]; }
dpkg-query() { return 1; }
apt-cache() { if [[ $2 == "$MISSING" ]]; then echo 'Candidate: (none)'; else echo 'Candidate: 1.0'; fi; }
apt-get() { echo "apt-get $*" >> "$WORK/calls"; if [[ $1 == install ]]; then touch "$WORK/installed"; fi; }
curl() { echo "curl $*" >> "$WORK/calls"; while (($#)); do if [[ $1 == -o ]]; then printf key > "$2"; break; fi; shift; done; }
systemctl() { echo "systemctl $*" >> "$WORK/calls"; }
shtab_install_docker
'''
            env = dict(os.environ, WORK=folder, PROFILE=profile, MISSING=missing)
            result = subprocess.run(['bash', '-c', script, 'test', str(helper)], env=env, capture_output=True, text=True)
            calls = (base/'calls').read_text() if (base/'calls').exists() else ''
            repository = base/'apt/sources.list.d/docker.sources'
            return result, calls, repository.read_text() if repository.exists() else ''

    def test_astra_uses_only_vendor_packages(self):
        result, calls, repo = self.install('astra')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('install -y docker.io docker-compose-v2 docker-buildx', calls)
        self.assertNotIn('curl', calls)
        self.assertNotIn('docker-ce', calls)
        self.assertEqual(repo, '')

    def test_missing_astra_package_stops_before_installation(self):
        result, calls, repo = self.install('astra', missing='docker-buildx')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')
        self.assertEqual(repo, '')

    def test_ubuntu_uses_ubuntu_repository(self):
        # Redirections are redirected to the temporary tree; no host APT changes.
        result, calls, repo = self.install('ubuntu')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('install -y docker-ce docker-ce-cli containerd.io', calls)
        self.assertIn('https://download.docker.com/linux/ubuntu', repo)
        self.assertIn('Suites: noble', repo)

    def test_standalone_bootstraps_embed_same_platform_checks(self):
        helper = (ROOT/'scripts/linux-platform.sh').read_text()
        for filename in ('Install-ShtabAI-Linux.sh', 'Install-ShtabAI-Ubuntu.sh'):
            self.assertIn(helper, (ROOT/filename).read_text())


if __name__ == '__main__':
    unittest.main()
