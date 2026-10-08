import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/configure-storage.py'


class StorageTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('storage', SCRIPT)
        self.m=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.m.CANONICAL=self.base/'opt/shtab-ai-021'; self.m.CANONICAL.parent.mkdir()
        self.m.UNITS=self.base/'units'; self.m.UNITS.mkdir()

    def tearDown(self): self.temp.cleanup()

    def test_reject_unsafe_paths(self):
        for value in ('/', '/mnt', 'relative/path', '/mnt/a b/app', '/mnt/../data/app', '/mnt/a\napp', str(self.m.CANONICAL/'nested')):
            with self.assertRaises(ValueError): self.m.validate_path(value)
        self.assertEqual(self.m.validate_path('/mnt/data/shtab-ai-021'),Path('/mnt/data/shtab-ai-021'))

    def prepare(self, selected, fs=None):
        fs=fs or {'fstype':'ext4','target':'/','uuid':'test-disk'}
        with patch.object(self.m,'filesystem',return_value=fs), patch.object(self.m.shutil,'disk_usage',return_value=SimpleNamespace(free=64*1024**3)), patch.object(self.m.subprocess,'check_output',return_value='opt-shtab\\x2dai\\x2d021.mount\n'), patch.object(self.m.subprocess,'run') as run:
            self.m.prepare(str(selected))
        return run

    def test_default_storage_binds_all_named_volumes_and_backups_outside(self):
        self.prepare(self.m.CANONICAL)
        config=json.loads((self.m.CANONICAL/'compose.storage.yaml').read_text())
        for name in self.m.VOLUMES:
            self.assertEqual(config['volumes'][name]['driver_opts']['device'],str(self.m.CANONICAL/'storage'/name))
            self.assertTrue((self.m.CANONICAL/'storage'/name).is_dir())
        self.assertFalse((self.m.CANONICAL/'installation-created').exists())
        self.assertEqual((self.m.CANONICAL/'backup-directory').read_text().strip(),'/var/backups/shtab-ai-021')

    def test_custom_root_has_persistent_bind_unit_and_separate_backups(self):
        selected=self.base/'other-disk/app'
        selected.parent.mkdir()
        run=self.prepare(selected)
        record=json.loads((selected/'storage.json').read_text())
        self.assertEqual(record['root'],str(selected))
        self.assertEqual(record['backups'],str(selected)+'-backups')
        unit=(self.m.UNITS/record['mount_unit']).read_text()
        self.assertIn('What='+str(selected),unit)
        self.assertIn('Where='+str(self.m.CANONICAL),unit)
        self.assertIn('RequiresMountsFor='+str(selected),unit)
        self.assertIn('Options=bind',unit)
        run.assert_any_call(['systemctl','enable','--now',record['mount_unit']],check=True)

    def test_existing_root_and_symlink_are_not_modified(self):
        self.m.CANONICAL.mkdir(); marker=self.m.CANONICAL/'existing'; marker.write_text('preserve')
        with self.assertRaises(ValueError): self.prepare(self.m.CANONICAL)
        self.assertEqual(marker.read_text(),'preserve')
        (self.base/'link').symlink_to(self.base/'opt',target_is_directory=True)
        with self.assertRaises(ValueError): self.prepare(self.base/'link/app')

    def test_bad_filesystem_and_unmounted_partition_are_rejected(self):
        for kind in ('ntfs','overlay','tmpfs'):
            with self.assertRaisesRegex(ValueError,'filesystem'): self.prepare(self.m.CANONICAL,{'fstype':kind,'uuid':'id','target':'/'})
        with patch.object(self.m,'filesystem',return_value={'fstype':'ext4','uuid':'data','target':'/mnt/data'}), patch.object(self.m.subprocess,'check_output',return_value=json.dumps({'filesystems':[{'target':'/','uuid':'system'}]})):
            with self.assertRaisesRegex(ValueError,'fstab'): self.m.prepare(str(self.m.CANONICAL))
        self.assertFalse(self.m.CANONICAL.exists())

    def test_changed_disk_and_wrong_bind_are_rejected(self):
        self.prepare(self.m.CANONICAL)
        with patch.object(self.m,'filesystem',return_value={'uuid':'test-disk'}):
            self.m.verify(self.m.CANONICAL)
        with patch.object(self.m,'filesystem',return_value={'uuid':'other-disk'}):
            with self.assertRaisesRegex(ValueError,'disk'): self.m.verify(self.m.CANONICAL)
        with patch.object(self.m,'filesystem',return_value={'uuid':'test-disk'}), patch.object(self.m.os.path,'samefile',return_value=False):
            with self.assertRaisesRegex(ValueError,'mount mismatch'): self.m.verify(self.m.CANONICAL)
