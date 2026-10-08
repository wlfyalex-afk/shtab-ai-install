import json
import os
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
    revision = json.loads(download_marker.read_text())['revision']
else:
    revision = HfApi().model_info(repo).sha
    download_marker.write_text(json.dumps({'repository': repo, 'revision': revision}))
path = snapshot_download(repo_id=repo, revision=revision, local_dir=str(root))
WhisperModel(path, device=os.environ.get('SHTAB_ASR_DEVICE','cpu'), compute_type=os.environ.get('SHTAB_ASR_COMPUTE_TYPE','int8'), cpu_threads=2,
             num_workers=1, local_files_only=True)
print('ASR downloaded and loaded successfully', flush=True)
marker.write_text(json.dumps({'repository': repo, 'revision': revision}))
