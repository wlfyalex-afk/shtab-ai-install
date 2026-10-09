"""Persist a verified CPU fallback without discarding downloaded models."""
import json
import os
from pathlib import Path
import time


def request_native_cpu(root, reason):
    (root / 'native-cpu-applied').unlink(missing_ok=True)
    target = root / 'native-cpu-fallback.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps({'mode': 'cpu', 'reason': reason, 'created_at': time.time()}, ensure_ascii=False))
    os.replace(temp, target)


def whisper_probe(path, device, compute, model_class, audio):
    model = model_class(str(path), device=device, compute_type=compute,
                        cpu_threads=2, num_workers=1, local_files_only=True)
    segments, _ = model.transcribe(audio, beam_size=1, language='ru', vad_filter=False,
                                  condition_on_previous_text=False)
    # Transcription is lazy: consume it to actually run the model.
    list(segments)


def verify_whisper(path, device, compute, model_class, audio):
    try:
        whisper_probe(path, device, compute, model_class, audio)
        return device, ''
    except Exception as gpu_error:
        if device != 'cuda':
            raise
        # A corrupt/incomplete model also fails on CPU and remains an error.
        whisper_probe(path, 'cpu', 'int8', model_class, audio)
        return 'cpu', str(gpu_error)
