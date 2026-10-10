import json
import os
import threading
import wave
import tempfile
from gpu_fallback import verify_whisper
from model_progress import Reporter
# HTTP writes incremental .incomplete files, which the monitor can measure.
os.environ["HF_HUB_DISABLE_XET"] = "1"
from pathlib import Path, PurePosixPath
from model_cache import matches as matches_file
from huggingface_hub import snapshot_download, HfApi
from faster_whisper import WhisperModel

root = Path('/srv/shtab-ai/response-models/turbo')
repo = 'mobiuslabsgmbh/faster-whisper-large-v3-turbo'
marker = root / 'shtab-model.json'
def verify_model(path):
    # Silence is enough to exercise encoder/decoder without shipping private audio.
    with tempfile.NamedTemporaryFile(suffix='.wav') as sample:
        with wave.open(sample.name, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b'\0\0' * 16000)
        device, reason = verify_whisper(path, os.environ.get('SHTAB_ASR_DEVICE', 'cpu'),
                                       os.environ.get('SHTAB_ASR_COMPUTE_TYPE', 'int8'),
                                       WhisperModel, sample.name)
    result = {'device': device, 'fallback_reason': reason}
    result_path = Path(os.environ.get('SHTAB_ASR_RESULT_FILE', '/tmp/asr-compute.json'))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False))
    print('Whisper: ' + device + ('; GPU не подошла, проверка CPU пройдена' if reason else '; пробное распознавание пройдено'), flush=True)

root.mkdir(parents=True, exist_ok=True)
download_marker = root / 'shtab-download.json'
# Resolve the current upstream revision on each install. Immutable hashes validate
# every cached file; a successful previous load alone is not an integrity check.
info = HfApi().model_info(repo, files_metadata=True)
revision = info.sha
metadata = {'repository': repo, 'revision': revision, 'files': [
    {'name': item.rfilename, 'size': item.size, 'etag': (getattr(item.lfs, 'sha256', None) if item.lfs else item.blob_id)}
    for item in (getattr(info, 'siblings', None) or []) if item.size is not None]}
if not metadata['files']:
    raise RuntimeError('Whisper upstream returned no file hashes')
marker.unlink(missing_ok=True)
all_valid = True
for item in metadata['files']:
    relative = PurePosixPath(item['name'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe upstream model filename')
    final = root / relative
    if not final.exists():
        all_valid = False
    if final.exists():
        print('Проверяем контрольную сумму Whisper: ' + item['name'], flush=True)
        if not matches_file(final, item['etag'], item['size']):
            all_valid = False
            final.unlink()
            print('Файл изменился или повреждён — будет скачан заново.', flush=True)
download_marker.write_text(json.dumps(metadata))
progress = Reporter('whisper')
files = metadata.get('files', [])
stop = threading.Event()
def measure():
    completed = 0
    cache = root / '.cache/huggingface/download'
    for item in files:
        final = root / item['name']
        size = 0
        try:
            if final.is_file():
                size = final.stat().st_size
            else:
                # HF Hub may encode the filename and append a random suffix.
                # Match the exact etag segment, not a guessed temporary name.
                etag = str(item.get('etag') or '').strip(chr(34))
                if etag:
                    for partial in cache.rglob('*.incomplete'):
                        if '.' + etag + '.' in partial.name:
                            try:
                                size = max(size, partial.stat().st_size)
                            except FileNotFoundError:
                                pass
                # The downloader may rename the partial while we inspect it.
                if final.is_file():
                    size = max(size, final.stat().st_size)
        except FileNotFoundError:
            pass
        completed += min(item['size'], size)
    return completed, sum(item['size'] for item in files)
def monitor():
    while not stop.is_set():
        done, total = measure()
        progress.update(done, total, detail='Файлы Whisper')
        stop.wait(1)
progress.update(*measure(), detail='Файлы Whisper', force=True)
thread = threading.Thread(target=monitor, daemon=True)
thread.start()
try:
    if all_valid:
        print('Whisper: контрольные суммы совпали — используем кэш без скачивания.', flush=True)
        path = str(root)
    else:
        path = snapshot_download(repo_id=repo, revision=revision, local_dir=str(root))
except Exception:
    stop.set()
    thread.join()
    progress.update(*measure(), phase='error', force=True)
    raise
finally:
    stop.set()
    thread.join()
progress.update(*measure(), phase='verify', force=True)
for item in metadata['files']:
    if not matches_file(root / item['name'], item['etag'], item['size']):
        raise RuntimeError('Whisper hash mismatch: ' + item['name'])
verify_model(path)
print('ASR downloaded and loaded successfully', flush=True)
marker.write_text(json.dumps({'repository': repo, 'revision': revision}))

progress.update(*measure(), phase='done', force=True)
