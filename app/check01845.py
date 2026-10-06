#!/usr/bin/env python3
import json
import os
from pathlib import Path

from store import Store
from brief_core017 import KEEP_ALIVE, LIMIT, RELIABILITY_VERSION, fallback_final
from brief_narrative0182 import NARRATIVE_VERSION, fallback_narrative


assert RELIABILITY_VERSION == "brief-reliability-0.18.4.5"
assert NARRATIVE_VERSION == "narrative-0.18.4.5"
assert LIMIT == 6000
assert KEEP_ALIVE == "5m"

sample = [
    {
        "id": "0123456789abcdef",
        "category": "FACT",
        "statement": "Проверена резервная ветка формирования брифа",
        "quote": "резервная ветка формирования брифа",
        "start_seconds": 0,
        "end_seconds": 1,
        "source_indices": [0],
    }
]
content, rejected = fallback_final(sample, "SELF_TEST")
assert rejected == 0 and content["sources"][0]["id"] == sample[0]["id"]
assert fallback_narrative(sample, "SELF_TEST")["fallback"] is True

config = json.loads(Path(os.environ.get("SHTAB_WEB_CONFIG", "/etc/shtab-ai/secretary-web.json")).read_text())
store = Store(config)
with store.connection() as connection, connection.cursor() as cur:
    cur.execute("SELECT retry_count,last_stage,last_done_reason FROM meeting_brief_jobs LIMIT 0")
    cur.execute("SELECT job_id,run_no,stage,outcome FROM meeting_brief_attempts LIMIT 0")

print(json.dumps({"brief_reliability": "ok", "version": RELIABILITY_VERSION}, ensure_ascii=False))
