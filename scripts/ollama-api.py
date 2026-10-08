#!/usr/bin/env python3
"""Provision and check either container Ollama or the Windows native server."""
import json
import os
from pathlib import Path
import sys
import urllib.request

MODEL = 'qwen3:4b'


def endpoint(root):
    if not (root / '.env').is_file():
        return os.environ.get('SHTAB_OLLAMA_ENDPOINT', '').rstrip('/')
    values = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines()
                  if '=' in line and not line.startswith('#'))
    return values.get('SHTAB_OLLAMA_ENDPOINT', '').rstrip('/')


def call(base, path, body=None, stream=False):
    request = urllib.request.Request(base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    # Private local service: no environment proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3600) as response:
        if stream:
            for line in response:
                event = json.loads(line)
                if event.get('error'):
                    raise RuntimeError(event['error'])
                total = event.get('total', 0)
                done = event.get('completed', 0)
                print(event.get('status', ''), f'{done}/{total}' if total else '', flush=True)
            return
        return json.load(response)


def check(base, acceleration):
    # Keeping the model briefly loaded allows an actual GPU placement check.
    result = call(base, '/api/generate', {'model': MODEL, 'prompt': 'Ответь одним словом: готово',
                                         'stream': False, 'keep_alive': '30s',
                                         'options': {'num_predict': 2, 'num_ctx': 2048}})
    if not result.get('done'):
        raise RuntimeError('Ollama did not finish the model probe')
    models = call(base, '/api/ps').get('models', [])
    model = next((item for item in models if item.get('name', '').split(':')[0] == 'qwen3'), None)
    if not model:
        raise RuntimeError('Qwen model is not loaded')
    vram = int(model.get('size_vram', 0))
    if acceleration != 'cpu' and vram <= 0:
        raise RuntimeError('GPU was selected, but Qwen is running entirely on CPU. Check driver/card support or explicitly select CPU.')
    print(f'Qwen probe passed: GPU model memory {vram} bytes', flush=True)
    call(base, '/api/generate', {'model': MODEL, 'stream': False, 'keep_alive': 0})


if __name__ == '__main__':
    root = Path('/opt/shtab-ai-021')
    base = endpoint(root)
    if not base:
        raise SystemExit('Ollama endpoint is not configured')
    action = sys.argv[1]
    if action == 'pull':
        call(base, '/api/pull', {'model': MODEL, 'stream': True}, stream=True)
    elif action == 'check':
        check(base, sys.argv[2])
    elif action == 'list':
        print(json.dumps(call(base, '/api/tags'), ensure_ascii=False))
    else:
        raise SystemExit('Unknown Ollama action')
