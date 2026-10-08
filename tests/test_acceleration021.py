import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class AccelerationTests(unittest.TestCase):
    def test_nvidia_whisper_has_cuda_and_shared_image(self):
        module = runpy.run_path(str(ROOT/'scripts/configure-acceleration.py'))
        services = module['configuration']('nvidia')['services']
        for name in ('meeting-worker', 'asr-download'):
            service = services[name]
            self.assertEqual(service['environment']['SHTAB_ASR_DEVICE'], 'cuda')
            self.assertEqual(service['deploy']['resources']['reservations']['devices'][0]['capabilities'], ['gpu'])
        for name in ('web', 'meeting-worker', 'llm-worker', 'brief-worker', 'asr-download'):
            self.assertEqual(services[name]['image'], services['web']['image'])
            self.assertEqual(services[name]['build']['args']['INSTALL_CUDA'], '1')

    def test_amd_does_not_apply_cuda_to_whisper_and_cpu_clears_override(self):
        module = runpy.run_path(str(ROOT/'scripts/configure-acceleration.py'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module['configure'](root, 'amd')
            services = json.loads((root/'compose.gpu.yaml').read_text())['services']
            self.assertEqual(set(services), {'ollama'})
            self.assertIn('/dev/kfd:/dev/kfd', services['ollama']['devices'])
            module['configure'](root, 'cpu')
            self.assertFalse((root/'compose.gpu.yaml').exists())
            self.assertEqual((root/'acceleration').read_text(), 'cpu\n')
            with self.assertRaises(ValueError): module['configure'](root, 'unknown')

    def probe(self, acceleration, vram, loaded=True):
        module = runpy.run_path(str(ROOT/'scripts/ollama-api.py'))
        check = module['check']
        calls = Mock(side_effect=[{'done': True}, {'models': [{'name': 'qwen3:4b', 'size_vram': vram}] if loaded else []}, {'done': True}])
        with patch.dict(check.__globals__, call=calls):
            check('http://172.20.0.1:11435', acceleration)
        return calls

    def test_gpu_selection_rejects_silent_cpu_fallback(self):
        for mode in ('nvidia', 'amd'):
            with self.assertRaisesRegex(RuntimeError, 'entirely on CPU'): self.probe(mode, 0)
            calls = self.probe(mode, 1024)
            self.assertEqual(calls.call_args.args[1], '/api/generate')
            self.assertEqual(calls.call_args.args[2]['keep_alive'], 0)

    def test_cpu_probe_and_missing_model(self):
        self.probe('cpu', 0)
        with self.assertRaisesRegex(RuntimeError, 'not loaded'): self.probe('cpu', 0, loaded=False)

    def test_endpoint_uses_native_config_and_container_environment(self):
        module = runpy.run_path(str(ROOT/'scripts/ollama-api.py'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict('os.environ', SHTAB_OLLAMA_ENDPOINT='http://ollama:11434'):
                self.assertEqual(module['endpoint'](root), 'http://ollama:11434')
                (root/'.env').write_text('SHTAB_OLLAMA_ENDPOINT=http://172.20.0.1:11435\n')
                self.assertEqual(module['endpoint'](root), 'http://172.20.0.1:11435')


if __name__ == '__main__': unittest.main()
