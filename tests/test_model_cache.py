import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('model_cache', Path(__file__).parents[1] / 'scripts/model_cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class ModelCacheTests(unittest.TestCase):
    def test_hashes_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.bin'
            payload = b'model-data'
            path.write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            git_sha = hashlib.sha1(b'blob 10\0' + payload).hexdigest()
            self.assertTrue(cache.matches(path, sha, len(payload)))
            self.assertTrue(cache.matches(path, git_sha, len(payload)))
            path.write_bytes(b'broken-data')
            self.assertFalse(cache.matches(path, sha, len(payload)))
            self.assertFalse(cache.matches(path, git_sha))

    def test_bind_mounts_survive_application_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'app'
            root.mkdir()
            shared = Path(directory) / 'cache'
            cache.configure(root, shared)
            volumes = json.loads((root / 'compose.cache.yaml').read_text())['volumes']
            self.assertEqual(volumes['asr_models']['driver_opts']['device'], str(shared / 'whisper'))
            (root / 'compose.cache.yaml').unlink()
            root.rmdir()
            self.assertTrue((shared / 'whisper').is_dir())

    def test_only_bad_ollama_layers_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory)
            blobs = models / 'blobs'
            blobs.mkdir()
            digest = hashlib.sha256(b'good').hexdigest()
            good = blobs / ('sha256-' + digest)
            good.write_bytes(b'good')
            bad = blobs / ('sha256-' + '0' * 64)
            bad.write_bytes(b'bad')
            partial = blobs / ('sha256-' + digest + '-partial')
            partial.write_bytes(b'partial')
            cache.verify_ollama(models)
            self.assertTrue(good.exists())
            self.assertFalse(bad.exists())
            self.assertTrue(partial.exists())

    def test_reject_nested_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'app'
            root.mkdir()
            with self.assertRaises(ValueError):
                cache.configure(root, root / 'cache')
