#!/usr/bin/env python3
"""Provision and check either container Ollama or the Windows native server."""
import json
import os
from pathlib import Path
import sys
import time
from gpu_fallback import request_native_cpu
import urllib.request
from model_progress import Reporter

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
            progress = Reporter('qwen')
            progress.update(detail='Получение списка файлов', force=True)
            success = False
            for line in response:
                event = json.loads(line)
                if event.get('error'):
                    raise RuntimeError(event['error'])
                success = event.get('status') == 'success'
                total = event.get('total', 0)
                done = event.get('completed', 0)
                progress.update(done, total, ('Слой ' + event.get('digest', '').removeprefix('sha256:')[:12]) if total else 'Подготовка и проверка файлов')
                print(event.get('status', ''), f'{done}/{total}' if total else '', flush=True)
            if not success:
                progress.update(phase='error', force=True)
                raise RuntimeError('Ollama download ended without confirmation')
            progress.update(phase='verify', force=True)
            return
        return json.load(response)


def probe(base, cpu=False):
    body = {'model': MODEL, 'prompt': 'Ответь одним словом: готово',
            'stream': False, 'keep_alive': '30s',
            'options': {'num_predict': 2, 'num_ctx': 2048}}
    if cpu:
        body['options']['num_gpu'] = 0
    result = call(base, '/api/generate', body)
    if not result.get('done'):
        raise RuntimeError('Qwen не завершил пробный запрос')
    models = call(base, '/api/ps').get('models', [])
    model = next((item for item in models if item.get('name', '').split(':')[0] == 'qwen3'), None)
    if not model:
        raise RuntimeError('Qwen не загружен после пробного запроса')
    return int(model.get('size_vram', 0))


def check(base, acceleration, root=None):
    root = root or Path(os.environ.get('SHTAB_GPU_RESULT_ROOT', '/opt/shtab-ai-021'))
    reason = ''
    try:
        vram = probe(base, cpu=(acceleration == 'cpu'))
        if acceleration != 'cpu' and vram <= 0:
            reason = 'Qwen успешно работает на CPU, но GPU-ускорение не подтверждено'
    except Exception as error:
        if acceleration == 'cpu':
            raise
        reason = str(error)
        vram = 0
    if reason:
        if (root / 'windows-acceleration').exists():
            request_native_cpu(root, reason)
            # Wait for native Ollama to restart with GPUs disabled.
            applied = root / 'native-cpu-applied'
            for attempt in range(60):
                if applied.exists():
                    break
                time.sleep(2)
            else:
                raise RuntimeError('Не подтверждён переход службы Ollama на CPU')
        else:
            # Container installations are handled by the host provisioner.
            request_native_cpu(root, reason)
            return 20
        vram = probe(base, cpu=True)
        print('GPU для Qwen не подошла. Проверка CPU пройдена; установка продолжается.', flush=True)
    actual = 'gpu' if vram > 0 else 'cpu'
    (root / 'qwen-compute.json').write_text(json.dumps({'device': actual, 'vram_bytes': vram, 'fallback_reason': reason}, ensure_ascii=False))
    print(f'Qwen: {actual}; видеопамять модели: {vram} Б', flush=True)
    call(base, '/api/generate', {'model': MODEL, 'stream': False, 'keep_alive': 0})
    return 0


if __name__ == '__main__':
    root = Path('/opt/shtab-ai-021')
    base = endpoint(root)
    if not base:
        raise SystemExit('Ollama endpoint is not configured')
    action = sys.argv[1]
    if action == 'pull':
        call(base, '/api/pull', {'model': MODEL, 'stream': True}, stream=True)
    elif action == 'check':
        raise SystemExit(check(base, sys.argv[2]))
    elif action == 'list':
        print(json.dumps(call(base, '/api/tags'), ensure_ascii=False))
    else:
        raise SystemExit('Unknown Ollama action')

