from pathlib import Path
import sys,json,concurrent.futures as cf,csv
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import check_streams as c
root=Path(__file__).resolve().parent
rows=json.loads((root/'results.json').read_text());entries=c.m.parse_entries((root/'input.m3u').read_text())
retry=[r for r in rows if r['status'] in ('unstable','unconfirmed') or (r['status']=='unavailable' and ('timed out' in (r['reason']+r['error']).lower() or 'time limit' in r['error'].lower()))]
print('Retrying',len(retry),'uncertain/time-out streams',flush=True)
with cf.ThreadPoolExecutor(max_workers=12) as pool:
 futures={pool.submit(c.check,(r['index'],*entries[r['index']])):r for r in retry}
 for i,f in enumerate(cf.as_completed(futures),1):
  old=futures[f];new=f.result();new['first_attempt']={'status':old['status'],'reason':old['reason'],'checked_at':old['checked_at']}
  rows[new['index']]=new
  print(i,'/',len(retry),new['name'],new['status'],flush=True)
(root/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
with (root/'results.csv').open('w',encoding='utf-8-sig',newline='') as f:
 fields=['name','channel_id','group','status','reason','url','checked_at'];writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
