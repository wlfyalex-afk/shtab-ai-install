import json
import os
import threading
import time
from model_progress import Reporter
# HTTP writes incremental .incomplete files, which the monitor can measure.
os.environ["HF_HUB_DISABLE_XET"] = "1"
from pathlib import Path
from huggingface_hub import snapshot_download, HfApi
from faster_whisper import WhisperModel

root = Path('/srv/shtab-ai/response-models/turbo')
repo = 'mobiuslabsgmbh/faster-whisper-large-v3-turbo'
marker = root / 'shtab-model.json'
if marker.exists():
    WhisperModel(str(root), device=os.environ.get('SHTAB_ASR_DEVICE','cpu'), compute_type=os.environ.get('SHTAB_ASR_COMPUTE_TYPE','int8'), cpu_threads=2,
                 num_workers=1, local_files_only=True)
    print('ASR already installed and loaded successfully', flush=True)
    raise SystemExit(0)
root.mkdir(parents=True, exist_ok=True)
download_marker = root / 'shtab-download.json'
if download_marker.exists():
    metadata = json.loads(download_marker.read_text())
    revision = metadata['revision']
else:
    info = HfApi().model_info(repo, files_metadata=True)
    revision = info.sha
    metadata = {'repository': repo, 'revision': revision, 'files': [
        {'name': item.rfilename, 'size': item.size, 'etag': (getattr(item.lfs, 'sha256', None) if item.lfs else item.blob_id)}
        for item in (getattr(info, 'siblings', None) or []) if item.size is not None]}
    download_marker.write_text(json.dumps(metadata))
progress = Reporter('whisper')
files = metadata.get('files', [])
stop = threading.Event()
def measure():
    completed = 0
    for item in files:
        final = root / item['name']
        partial = root / '.cache/huggingface/download' / (item['name'] + '.' + str(item['etag']) + '.incomplete')
        try:
            completed += min(item['size'], final.stat().st_size if final.is_file() else partial.stat().st_size)
        except FileNotFoundError:
            pass
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
WhisperModel(path, device=os.environ.get('SHTAB_ASR_DEVICE','cpu'), compute_type=os.environ.get('SHTAB_ASR_COMPUTE_TYPE','int8'), cpu_threads=2,
             num_workers=1, local_files_only=True)
print('ASR downloaded and loaded successfully', flush=True)
marker.write_text(json.dumps({'repository': repo, 'revision': revision}))

progress.update(*measure(), phase='done', force=True)
