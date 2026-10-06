import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

script = sys.argv[1]
child = None
stopping = False
def stop(*_):
    global stopping
    stopping = True
    if child is not None and child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while not stopping:
    guard = None
    if script != 'meeting_worker.py':
        guard = open(os.environ.get('SHTAB_LLM_LOCK', '/srv/shtab-ai/logs/llm.lock'), 'a')
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            guard.close()
            time.sleep(2)
            continue
    try:
        if stopping:
            break
        child = subprocess.Popen([sys.executable, '-u', script, 'once'], start_new_session=True)
        result = child.wait()
        child = None
        if result:
            print('worker_pass_failed', script, result, flush=True)
    finally:
        if guard:
            guard.close()
    for _ in range(15):
        if stopping:
            break
        time.sleep(1)
