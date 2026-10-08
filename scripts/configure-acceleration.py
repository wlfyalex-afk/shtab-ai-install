#!/usr/bin/env python3
"""Write a Compose override without changing the base installation or its secrets."""
import json
from pathlib import Path
import sys


def configuration(mode):
    if mode not in ('cpu', 'nvidia', 'amd'):
        raise ValueError('Acceleration must be cpu, nvidia or amd')
    services = {}
    if mode == 'nvidia':
        gpu = {'resources': {'reservations': {'devices': [
            {'driver': 'nvidia', 'count': 'all', 'capabilities': ['gpu']}
        ]}}}
        services['ollama'] = {'deploy': gpu}
        for name in ('web', 'meeting-worker', 'llm-worker', 'brief-worker', 'asr-download'):
            services[name] = {'image': 'shtab-ai-021-app:0.21.0-rc3-nvidia',
                              'build': {'args': {'INSTALL_CUDA': '1'}}}
        for name in ('meeting-worker', 'asr-download'):
            services[name].update(deploy=gpu, environment={
                'SHTAB_ASR_DEVICE': 'cuda', 'SHTAB_ASR_COMPUTE_TYPE': 'int8_float16'})
    elif mode == 'amd':
        services['ollama'] = {'image': 'ollama/ollama:0.34.1-rocm',
                              'devices': ['/dev/kfd:/dev/kfd', '/dev/dri:/dev/dri']}
    return {'services': services}


def configure(root, mode):
    result = configuration(mode)
    target = root / 'compose.gpu.yaml'
    if mode == 'cpu':
        target.unlink(missing_ok=True)
    else:
        target.write_text(json.dumps(result, indent=2) + '\n')
        target.chmod(0o644)
    (root / 'acceleration').write_text(mode + '\n')


if __name__ == '__main__':
    configure(Path(sys.argv[1]), sys.argv[2])
