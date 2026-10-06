import os
import signal
import subprocess
import sys
import time

script = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else "loop"
child = None
stopping = False

code = """
import os, runpy, sys
endpoint = os.environ.get("SHTAB_OLLAMA_ENDPOINT")
if endpoint:
    import llm_core013
    llm_core013.ENDPOINT = endpoint
script, mode = sys.argv[1:3]
sys.argv = [script, mode]
runpy.run_path(script, run_name="__main__")
"""

def stop(signum, frame):
    global stopping
    stopping = True
    if child is not None and child.poll() is None:
        child.terminate()

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while not stopping:
    child = subprocess.Popen([
        sys.executable, "-u", "-c", code,
        script, "once" if mode == "loop" else mode,
    ])
    result = child.wait()
    child = None
    if mode != "loop":
        sys.exit(result)
    if result and not stopping:
        print("worker_pass_failed", script, result, flush=True)
    for _ in range(5):
        if stopping:
            break
        time.sleep(1)
