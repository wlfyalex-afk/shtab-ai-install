import ipaddress
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT/'scripts/network-config.py'))


class NetworkAccessTests(unittest.TestCase):
    def setup_root(self, root):
        (root/'lan-address').write_text('192.168.10.171\n')
        (root/'lan-subnet').write_text('192.168.10.0/24\n')

    def test_normalization_multiple_ranges_and_host_routes(self):
        self.assertEqual(MODULE['networks']('10.20.30.8/24, 10.20.30.0/24 10.40.50.2/32'), ['10.20.30.0/24', '10.40.50.2/32'])
        for invalid in ('0.0.0.0/0', '::/0', '224.0.0.0/4', '10.0.0.2', 'not-a-network', '10.0.0.0/33'):
            with self.assertRaises(ValueError):
                MODULE['networks'](invalid)

    def test_legacy_lan_only_and_vpn_replacement_preserve_lan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.setup_root(root)
            self.assertEqual(MODULE['allowed'](root), ['192.168.10.0/24'])
            MODULE['configure'](root, '10.20.30.0/24')
            MODULE['configure'](root)
            self.assertEqual(MODULE['allowed'](root), ['192.168.10.0/24', '10.20.30.0/24'])
            MODULE['configure'](root, '10.40.50.2/32', replace=True)
            self.assertEqual(MODULE['allowed'](root), ['192.168.10.0/24', '10.40.50.2/32'])
            MODULE['configure'](root, '', replace=True)
            self.assertEqual(MODULE['allowed'](root), ['192.168.10.0/24'])
            self.assertEqual((root/'allowed-subnets').stat().st_mode & 0o777, 0o600)

    def test_invalid_vpn_does_not_overwrite_saved_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.setup_root(root)
            MODULE['configure'](root, '10.20.30.0/24')
            before = (root/'allowed-subnets').read_bytes()
            with self.assertRaises(ValueError):
                MODULE['configure'](root, '10.40.50.0/24,invalid', replace=True)
            self.assertEqual((root/'allowed-subnets').read_bytes(), before)

    def firewall(self, extra, action='apply'):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            app = base/'app'
            (app/'scripts').mkdir(parents=True)
            self.setup_root(app)
            (app/'allowed-subnets').write_text(extra)
            (app/'scripts/network-config.py').write_text((ROOT/'scripts/network-config.py').read_text())
            bin_dir = base/'bin'
            bin_dir.mkdir()
            iptables = bin_dir/'iptables'
            iptables.write_text('#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ["CALLS"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nsys.exit(1 if "-C" in sys.argv else 0)\n')
            iptables.chmod(0o755)
            script = (ROOT/'scripts/network-firewall.sh').read_text().replace('root=/opt/shtab-ai', 'root='+shlex.quote(str(app)))
            env = dict(os.environ, PATH=str(bin_dir)+':'+os.environ['PATH'], CALLS=str(base/'calls'))
            result = subprocess.run(['bash', '-c', script, 'firewall', action], env=env, capture_output=True, text=True)
            import json
            calls = [json.loads(line) for line in (base/'calls').read_text().splitlines()] if (base/'calls').exists() else []
            return result, calls

    def test_actual_firewall_compiles_lan_vpn_and_final_deny(self):
        result, calls = self.firewall('192.168.10.0/24\n10.20.30.0/24\n10.40.50.2/32\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        rules = [call for call in calls if '-A' in call]
        self.assertIn('ESTABLISHED,RELATED', rules[0])
        sources = [rule[rule.index('-s')+1] for rule in rules if '-s' in rule]
        self.assertEqual(sources, ['192.168.10.0/24', '10.20.30.0/24', '10.40.50.2/32'])
        self.assertEqual(rules[-1][-2:], ['-j', 'DROP'])
        # Evaluate the emitted source exceptions, including a VPN client and an outsider.
        for client, expected in [('192.168.10.22', True), ('10.20.30.9', True), ('10.40.50.2', True), ('10.40.50.3', False), ('203.0.113.9', False)]:
            self.assertEqual(any(ipaddress.ip_address(client) in ipaddress.ip_network(source) for source in sources), expected)

    def test_invalid_configuration_never_touches_firewall(self):
        result, calls = self.firewall('0.0.0.0/0\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_remove_works_even_when_saved_subnets_are_damaged(self):
        result, calls = self.firewall('invalid', action='remove')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('-X' in call for call in calls))


if __name__ == '__main__':
    unittest.main()
