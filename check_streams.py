#!/usr/bin/env python3
"""Point-in-time playback check; does not modify the main playlist."""
import concurrent.futures as cf
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import csv, json, subprocess, time, re
import iptv_filter as m

ROOT=Path(__file__).resolve().parent
DEST=ROOT/'checks'

def headers(meta):
    h={'User-Agent':'Mozilla/5.0'}
    for name,key in [('http-user-agent','User-Agent'),('http-referrer','Referer'),('http-origin','Origin')]:
        value=m.attr(meta[0],name)
        for line in meta[1:]:
            prefix='#EXTVLCOPT:'+name+'='
            if line.startswith(prefix): value=line[len(prefix):]
        if value: h[key]=value
    return h

def run(cmd,timeout):
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        return p.returncode,p.stdout,p.stderr[-2200:]
    except subprocess.TimeoutExpired:
        return 124,'','Playback check exceeded time limit'

def check(item):
    idx,meta,url=item
    start=time.monotonic()
    title=m.split_extinf(meta[0])[1]
    h=headers(meta)
    opts=['-rw_timeout','10000000','-headers',''.join(f'{k}: {v}\r\n' for k,v in h.items())]
    code,out,err=run(['ffprobe','-v','error',*opts,'-analyzeduration','5000000','-probesize','2000000','-show_entries','stream=codec_type,codec_name,width,height:format=format_name','-of','json',url],24)
    try: info=json.loads(out) if out else {}
    except ValueError: info={}
    streams=info.get('streams',[])
    video=next((s for s in streams if s.get('codec_type')=='video'),None)
    audio=next((s for s in streams if s.get('codec_type')=='audio'),None)
    status='unavailable';reason='';http=None
    if video:
        decode,progress,decode_err=run(['ffmpeg','-hide_banner','-loglevel','error','-progress','pipe:1','-xerror',*opts,'-i',url,'-map','0:v:0','-map','0:a:0?','-t','2','-frames:v','3','-threads','1','-f','null','-'],22)
        if decode==0 and any(int(n)>0 for n in re.findall(r'(?m)^frame=(\d+)',progress)):
            status='working';reason='Video decoded successfully'+('; audio stream present' if audio else '; no audio stream detected')
        else:
            status='unstable';reason='Video metadata found, but decoding failed or timed out';err=decode_err or err
    else:
        # Independent HTTP check distinguishes server rejection from parsing/timeout failures.
        try:
            with urlopen(Request(url,headers=h),timeout=10) as r:
                http=r.status;sample=r.read(8192)
                if sample.lstrip().startswith(b'#EXTM3U'):
                    reason='HLS playlist responds, but video could not be read';status='unstable'
                elif b'<html' in sample.lower() or b'<!doctype html' in sample.lower():
                    reason='Server returned HTML instead of a video stream'
                else: reason='HTTP responds, but no video stream detected';status='unconfirmed'
        except HTTPError as e:
            http=e.code
            if e.code in (401,403,451): status='restricted';reason=f'HTTP {e.code}: access denied (possible geo/auth restriction)'
            elif e.code in (404,410): reason=f'HTTP {e.code}: stream not found'
            elif e.code==429: status='unconfirmed';reason='HTTP 429: server rate limit'
            else: reason=f'HTTP {e.code}: server error'
        except Exception as e:
            reason=f'Network error: {type(e).__name__}: {str(e)[:200]}'
            if code==124: reason='Timed out in playback and HTTP checks'
    return {'index':idx,'name':title,'channel_id':m.attr(meta[0],'tvg-id').split('@')[0],
            'group':m.attr(meta[0],'group-title'),'url':url,'status':status,'reason':reason,
            'video':video,'audio':audio,'http_status':http,'seconds':round(time.monotonic()-start,1),
            'checked_at':datetime.now(timezone.utc).isoformat(),'error':err[:1600] if status!='working' else ''}


def main():
    DEST.mkdir(exist_ok=True)
    snapshot=(ROOT/'my-iptv.m3u').read_text()
    (DEST/'input.m3u').write_text(snapshot)
    entries=m.parse_entries(snapshot)
    items=[(i,a,u) for i,(a,u) in enumerate(entries)]
    results=[]
    with (DEST/'progress.jsonl').open('w') as log:
        with cf.ThreadPoolExecutor(max_workers=12) as pool:
            futures=[pool.submit(check,item) for item in items]
            for f in cf.as_completed(futures):
                r=f.result();results.append(r);log.write(json.dumps(r,ensure_ascii=False)+'\n');log.flush()
                if len(results)%10==0: print(len(results),'/',len(items),dict(Counter(x['status'] for x in results)),flush=True)
    results.sort(key=lambda x:x['index'])
    (DEST/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    with (DEST/'results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['name','channel_id','group','status','reason','url','checked_at'];writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(results)
    print('COMPLETE',dict(Counter(x['status'] for x in results)),flush=True)

if __name__=='__main__':main()
