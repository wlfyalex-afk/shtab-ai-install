"""Exercise the guest readiness gate without a Hyper-V host."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class HyperVReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'windows/Native-HyperV.ps1').read_text()
        cls.probe = re.search(r"\$readinessProbe = @'\n(.*?)\n'@", source, re.S).group(1)
        cls.seed = yaml.safe_load(re.search(r'\$userData = @"\n(.*?)\n"@', source, re.S).group(1))

    def test_seed_reboots_after_recording_first_boot(self):
        self.assertEqual(self.seed['power_state']['mode'], 'reboot')
        self.assertIs(self.seed['power_state']['condition'], True)
        self.assertIn('linux-cloud-tools-virtual', self.seed['packages'])
        self.assertIn('linux-tools-virtual', self.seed['packages'])
        marker_command = self.seed['runcmd'][-1]
        self.assertEqual(marker_command[:2], ['bash', '-ec'])
        subprocess.run(['bash', '-n', '-c', marker_command[2]], check=True)
        subprocess.run(['bash', '-n', '-c', self.probe], check=True)

    def run_probe(self, first_boot=None, current_boot='second', cloud_status='done', cloud_exit=0, kvp_exit=0):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'first-boot'
            boot = Path(directory) / 'boot-id'
            boot.write_text(current_boot)
            if first_boot is not None:
                marker.write_text(first_boot)
            probe = self.probe.replace('/var/lib/shtab-hyperv-first-boot', str(marker)).replace('/proc/sys/kernel/random/boot_id', str(boot))
            mocks = f'''cloud-init() {{ printf 'status: {cloud_status}\\ndetail: DataSourceNoCloud\\n'; return {cloud_exit}; }}
systemctl() {{ return {kvp_exit}; }}
'''
            return subprocess.run(['bash', '-c', mocks + probe], capture_output=True, text=True)

    def test_no_marker_means_not_ready(self):
        self.assertEqual(self.run_probe().returncode, 75)

    def test_early_ssh_on_initial_boot_means_not_ready(self):
        self.assertEqual(self.run_probe(first_boot='first', current_boot='first').returncode, 75)

    def test_completed_second_boot_is_ready(self):
        result = self.run_probe(first_boot='first')
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r'(?m)^status: done\s*$')

    def test_cloud_init_failure_is_preserved(self):
        self.assertEqual(self.run_probe(first_boot='first', cloud_exit=1).returncode, 1)
        self.assertEqual(self.run_probe(first_boot='first', cloud_exit=2).returncode, 2)

    def test_stopped_kvp_means_not_ready(self):
        self.assertEqual(self.run_probe(first_boot='first', kvp_exit=3).returncode, 76)

    def test_cloud_init_still_running_does_not_pass_host_status_gate(self):
        result = self.run_probe(first_boot='first', cloud_status='running')
        self.assertNotRegex(result.stdout, r'(?m)^status: done\s*$')


if __name__ == '__main__':
    unittest.main()
