import uuid
from llm_core013 import VERSION,MODEL,segments,chunks,fingerprint

def enqueue(cur,org,mid,tid,content):
    count=len(chunks(segments(content)))
    cur.execute('''INSERT INTO meeting_llm_jobs(id,organization_id,meeting_id,transcript_id,method,model,source_hash,total_chunks)
      SELECT %s,%s,%s,%s,%s,%s,%s,%s FROM transcripts t JOIN meetings m ON m.id=t.meeting_id
      WHERE t.id=%s AND t.meeting_id=%s AND t.organization_id=%s AND m.organization_id=%s
      ON CONFLICT(transcript_id,method) DO NOTHING RETURNING id''',
      (str(uuid.uuid4()),org,mid,tid,VERSION,MODEL,fingerprint(content),count,tid,mid,org,org))
    return cur.fetchone()
