from pathlib import Path
import sys,json,concurrent.futures as cf,csv
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import check_streams as c
root=Path(__file__).resolve().parent
previous=json.loads((root/'results.json').read_text());previous={r['url']:r for r in previous}
snapshot=(root.parent/'my-iptv.m3u').read_text();(root/'input.m3u').write_text(snapshot);entries=c.m.parse_entries(snapshot)
rows=[None]*len(entries);todo=[]
for i,(meta,url) in enumerate(entries):
 if url in previous:
  r=dict(previous[url]);r.update(index=i,name=c.m.split_extinf(meta[0])[1],channel_id=c.m.attr(meta[0],'tvg-id').split('@')[0],group=c.m.attr(meta[0],'group-title'));rows[i]=r
 else:todo.append((i,meta,url))
print('Reusing',len(entries)-len(todo),'recent checks; checking',len(todo),'new streams',flush=True)
with (root/'expansion-progress.jsonl').open('w') as log:
 with cf.ThreadPoolExecutor(max_workers=16) as pool:
  for i,f in enumerate(cf.as_completed([pool.submit(c.check,x) for x in todo]),1):
   r=f.result();rows[r['index']]=r;log.write(json.dumps(r,ensure_ascii=False)+'\n');log.flush()
   if i%20==0:print(i,'/',len(todo),'new streams checked; working',sum(r is not None and r['status']=='working' for r in rows),flush=True)
(root/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
with (root/'results.csv').open('w',encoding='utf-8-sig',newline='') as f:
 fields=['name','channel_id','group','status','reason','url','checked_at'];writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
print('COMPLETE',flush=True)
