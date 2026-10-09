import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from gpu_fallback import verify_whisper, request_native_cpu
import importlib.util
spec = importlib.util.spec_from_file_location('ollama_api', Path(__file__).resolve().parents[1] / 'scripts/ollama-api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class GPUFallbackTests(unittest.TestCase):
    def test_whisper_runs_lazy_inference_and_retries_cpu(self):
        calls = []
        class Model:
            def __init__(self, path, **kwargs):
                self.device = kwargs['device']
            def transcribe(self, audio, **kwargs):
                def segments():
                    calls.append(self.device)
                    if self.device == 'cuda':
                        raise RuntimeError('unsupported CUDA kernel')
                    yield 'CPU result'
                return segments(), None
        device, reason = verify_whisper('model', 'cuda', 'int8_float16', Model, 'sample.wav')
        self.assertEqual(device, 'cpu')
        self.assertIn('CUDA', reason)
        self.assertEqual(calls, ['cuda', 'cpu'])

    def test_corrupt_whisper_still_blocks_installation(self):
        class Broken:
            def __init__(self, *args, **kwargs):
                raise RuntimeError('corrupt model')
        with self.assertRaisesRegex(RuntimeError, 'corrupt model'):
            verify_whisper('model', 'cuda', 'int8_float16', Broken, 'sample.wav')

    def test_qwen_cpu_success_is_not_a_gpu_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'windows-acceleration').write_text('amd')
            def applied(folder, reason):
                request_native_cpu(folder, reason)
                (folder / 'native-cpu-applied').touch()
            with patch.object(api, 'probe', return_value=0) as probe, patch.object(api, 'call', return_value={}), patch.object(api, 'request_native_cpu', side_effect=applied):
                self.assertEqual(api.check('http://native', 'amd', root), 0)
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(probe.call_args.kwargs, {'cpu': True})
            self.assertEqual(json.loads((root / 'qwen-compute.json').read_text())['device'], 'cpu')
            self.assertEqual(json.loads((root / 'native-cpu-fallback.json').read_text())['mode'], 'cpu')

    def test_failed_cpu_probe_still_blocks_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(api, 'probe', side_effect=RuntimeError('broken model')):
                with self.assertRaisesRegex(RuntimeError, 'broken model'):
                    api.check('http://native', 'cpu', Path(directory))
