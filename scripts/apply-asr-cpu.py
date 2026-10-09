#!/usr/bin/env python3
"""Keep the built image, but persist CPU-only Whisper service settings."""
import json
from pathlib import Path
import sys


def apply(root):
    override = root / 'compose.gpu.yaml'
    data = json.loads(override.read_text()) if override.exists() else {'services': {}}
    for name in ('meeting-worker', 'asr-download'):
        service = data['services'].setdefault(name, {})
        service.pop('deploy', None)
        service['environment'] = {'SHTAB_ASR_DEVICE': 'cpu', 'SHTAB_ASR_COMPUTE_TYPE': 'int8'}
    override.write_text(json.dumps(data, indent=2) + '\n')
    (root / 'asr-acceleration').write_text('cpu\n')


if __name__ == '__main__':
    apply(Path(sys.argv[1]))
