#!/usr/bin/env python3
"""Persistent model mounts and content hash verification; no user data is cached."""
import hashlib
import json
from pathlib import Path
import sys


def default_cache(root):
    # Keep an existing installation's model mounts when setup is repeated.
    existing = root / 'compose.cache.yaml'
    if existing.is_file():
        volumes = json.loads(existing.read_text())['volumes']
        whisper = Path(volumes['asr_models']['driver_opts']['device'])
        ollama = Path(volumes['ollama_models']['driver_opts']['device'])
        if whisper.name != 'whisper' or ollama.name != 'ollama' or whisper.parent != ollama.parent:
            raise ValueError('Existing model cache paths disagree; automatic relocation is disabled')
        return whisper.parent
    metadata = root / 'storage.json'
    if metadata.is_file():
        selected = Path(json.loads(metadata.read_text())['root'])
        if selected != Path('/opt/shtab-ai-021'):
            return selected.with_name(selected.name + '-cache')
    return Path('/var/cache/shtab-ai')


def matches(path, expected, size=None):
    if not path.is_file() or path.is_symlink():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    if len(expected) == 64:
        digest = hashlib.sha256()
    elif len(expected) == 40:
        digest = hashlib.sha1()
        digest.update(f'blob {path.stat().st_size}\0'.encode())
    else:
        raise ValueError('Unsupported upstream content digest')
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest() == expected


def verify_ollama(models):
    for blob in (models / 'blobs').glob('sha256-*'):
        digest = blob.name[7:]
        if len(digest) == 64 and all(c in '0123456789abcdef' for c in digest):
            print('Проверяем SHA256 слоя Qwen:', blob.name, flush=True)
            if not matches(blob, digest):
                blob.unlink()
                print('Повреждённый слой удалён; он будет скачан заново.', flush=True)


def configure(root, cache):
    if not cache.is_absolute() or '..' in cache.parts:
        raise ValueError('Model cache must be an absolute directory')
    if root == cache or root in cache.parents or cache in root.parents:
        raise ValueError('Model cache must be separate from the application')
    for path in (cache, *cache.parents):
        if path.is_symlink():
            raise ValueError('Model cache cannot contain symbolic links')
    volumes = {}
    for name, folder in [('asr_models', 'whisper'), ('ollama_models', 'ollama')]:
        target = cache / folder
        target.mkdir(parents=True, exist_ok=True)
        volumes[name] = {'driver': 'local', 'driver_opts': {
            'type': 'none', 'o': 'bind', 'device': str(target)}}
    (root / 'compose.cache.yaml').write_text(json.dumps({'volumes': volumes}, indent=2) + '\n')
    # Windows native Ollama uses cache/qwen and is verified by PowerShell.
    verify_ollama(cache / 'ollama' / 'models')
    print('Постоянный кэш моделей:', cache, flush=True)


if __name__ == '__main__':
    root = Path(sys.argv[1])
    configure(root, Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else default_cache(root))
