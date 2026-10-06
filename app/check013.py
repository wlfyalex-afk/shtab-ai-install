#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

ROOT=Path('/opt/shtab-ai/secretary-web-006')
sys.path.insert(0,str(ROOT))

def main():
    required=['llm_core013.py','llm_queue013.py','llm_web013.py','llm_worker013.py','templates/llm013.html']
    missing=[name for name in required if not (ROOT/name).is_file()]
    if missing: raise SystemExit('Missing: '+', '.join(missing))
    from app import create_app
    from store import Store
    cfg=json.loads(Path(os.environ.get('SHTAB_WEB_CONFIG','/etc/shtab-ai/secretary-web.json')).read_text())
    app=create_app(cfg,Store(cfg))
    rules={(r.rule,tuple(sorted(r.methods))) for r in app.url_map.iter_rules()}
    expected={'/meetings/<uuid:mid>/llm','/meetings/<uuid:mid>/llm/<uuid:jid>/retry'}
    if not expected <= {r for r,_ in rules}: raise SystemExit('LLM routes missing')
    with app.store.connection() as c,c.cursor() as cur:
        cur.execute("SELECT candidate_key,model_analysis FROM meeting_task_drafts LIMIT 0")
        cur.execute("SELECT organization_id,status,next_chunk,total_chunks FROM meeting_llm_jobs LIMIT 0")
        cur.execute("SELECT job_id,chunk_no,response FROM meeting_llm_chunks LIMIT 0")
    print('TASK-EXTRACTION-013: database, routes and templates OK')

if __name__=='__main__': main()
