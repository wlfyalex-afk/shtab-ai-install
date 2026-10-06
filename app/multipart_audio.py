"""Ordered PCM concatenation with sample-accurate source coordinates."""
import hashlib
import json
import os
import wave
from import_common import ImportFailure,MAX_DURATION,atomic_json,digest_file

def concatenate(parts,folder):
    target=folder/'combined.wav';tmp=folder/'combined.part.wav';mapping=[];offset=0
    with wave.open(str(tmp),'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed'))
        for number,part in enumerate(parts,1):
            with wave.open(str(part['audio']),'rb') as src:
                if (src.getnchannels(),src.getsampwidth(),src.getframerate(),src.getcomptype())!=(1,2,16000,'NONE'):
                    raise ImportFailure('INVALID_MEDIA')
                frames=src.getnframes()
                if frames<=0 or offset+frames>MAX_DURATION*16000:raise ImportFailure('TOO_LONG')
                consumed=0
                while True:
                    data=src.readframes(65536)
                    if not data:break
                    consumed+=len(data)//2;out.writeframesraw(data)
                if consumed!=frames:raise ImportFailure('INVALID_MEDIA')
            mapping.append(dict(part=number,import_id=part['id'],original_name=part['name'],
              source_sha256=part['sha'],start=offset/16000,end=(offset+frames)/16000,
              gap_before='unknown' if number>1 else None))
            offset+=frames
    with open(tmp,'rb') as f:os.fsync(f.fileno())
    os.replace(tmp,target)
    sha=hashlib.sha256(json.dumps(mapping,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    atomic_json(folder/'source.json',dict(sha256=sha,parts=mapping,timeline='concatenated_audio',audio_sha256=digest_file(target)))
    return target,sha,offset/16000

def bind_segments(segments,parts):
    for segment in segments:
        refs=[]
        for part in parts:
            start=max(float(segment['start']),part['start']);end=min(float(segment['end']),part['end'])
            if end>start:
                refs.append(dict(part=part['part'],import_id=part['import_id'],
                    start=round(start-part['start'],6),end=round(end-part['start'],6)))
        segment['source_parts']=refs
    return segments
