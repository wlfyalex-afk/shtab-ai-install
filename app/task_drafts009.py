"""Conservative extraction of verbatim candidate instructions; no inferred facts."""
import re,uuid
from psycopg2.extras import Json
CUE=re.compile(r'\b(?:поручаю|поручаем|поручить|даю\s+поручение|прошу\s+(?:подготовить|представить|обеспечить|проверить|направить|проработать)|(?:необходимо|нужно)\s+(?:подготовить|представить|обеспечить|проверить|направить|проработать)|подготовьте|представьте|обеспечьте|проверьте|проработайте)\b',re.I)
NEG=re.compile(r'\b(?:не\s+(?:поручаю|поручаем|поручать|прошу|нужно)|не\s+надо|не\s+требуется)\b',re.I)
def candidate_indices(content):
    return [i for i,s in enumerate(content.get('segments',[])) if CUE.search(s['text']) and not NEG.search(s['text'])]

def add_candidate(cur,org,mid,tid,content,index,method='rules-0.9'):
    segments=content['segments'];segment=segments[index]
    # Quote remains unchanged; context is available through playback and transcript.
    cur.execute('''INSERT INTO meeting_task_drafts(id,organization_id,meeting_id,transcript_id,segment_index,
      instruction,source_quote,start_seconds,end_seconds,method)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT DO NOTHING RETURNING id''',
      (str(uuid.uuid4()),org,mid,tid,index,segment['text'].strip(),segment['text'],segment['start'],segment['end'],method))
    return cur.fetchone()

def extract_drafts(cur,org,mid,tid,content):
    count=0
    for index in candidate_indices(content):
        if add_candidate(cur,org,mid,tid,content,index):count+=1
    cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,event_type,entity_type,entity_id,payload)
      VALUES (%s,%s,'WORKER','TASK_DRAFTS_EXTRACTED','MEETING',%s,%s)''',
      (str(uuid.uuid4()),org,mid,Json(dict(method='rules-0.9',created=count,transcript_id=str(tid)))))
    return count
