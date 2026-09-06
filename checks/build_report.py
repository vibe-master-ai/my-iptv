from collections import Counter,defaultdict
from pathlib import Path
from datetime import datetime,timezone
import csv,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import iptv_filter as m
root=Path(__file__).resolve().parent
results=json.loads((root/'results.json').read_text())
entries=m.parse_entries((root/'input.m3u').read_text())
# Normalize report identity to the exact checked snapshot.
for r in results:
    meta,url=entries[r['index']]
    assert url == r['url']
    r['channel_id']=m.attr(meta[0],'tvg-id').split('@')[0]
labels={'working':'✅ Працює','restricted':'🔒 Обмежено доступ','unstable':'⚠️ Нестабільний / не підтверджено','unavailable':'❌ Недоступний','unconfirmed':'❓ Не підтверджено','identity_failed':'🚫 Помилковий вміст / не підтверджено'}
paid_audit_path = root / 'paid_gate_audit.json'
paid_audit = json.loads(paid_audit_path.read_text()) if paid_audit_path.exists() else None
origin_report_path = root.parent / 'origin-filter-report.json'
origin_report = json.loads(origin_report_path.read_text()) if origin_report_path.exists() else None
channels=defaultdict(list)
for r in results:channels[r['channel_id'] or r['name']].append(r)
working=[r for r in results if r['status']=='working']
output=['#EXTM3U']
for r in working:
 meta,url=entries[r['index']];assert url==r['url'];output.extend(meta+[url])
cinema_output=['#EXTM3U']
for r in working:
    if r['channel_id'].startswith('OnlineCinema.'):
        meta,url=entries[r['index']];cinema_output.extend(meta+[url])
(root/'cinemas-working.m3u').write_text('\n'.join(cinema_output)+'\n')
(root/'working.m3u').write_text('\n'.join(output)+'\n')
counts=Counter(r['status'] for r in results)
summary={'checked_from':min(r['checked_at'] for r in results),'checked_at':max(r['checked_at'] for r in results),'identity_checked_at':max((r.get('identity_checked_at','') for r in results),default=''),'total_streams':len(results),'stream_results':dict(counts),'working_languages':dict(Counter(r['group'].split(' | ')[-1] for r in working)),'catalog_channels':sum(not k.startswith('OnlineCinema.') for k in channels),'online_cinemas':sum(k.startswith('OnlineCinema.') for k in channels),'online_cinemas_working':sum(k.startswith('OnlineCinema.') and any(r['status']=='working' for r in rs) for k,rs in channels.items()),'total_channels':len(channels),'channels_with_working_stream':sum(any(r['status']=='working' for r in rs) for rs in channels.values()),'identity_reviewed_working':sum(r.get('identity_status','').startswith('reviewed_') for r in results if r.get('technical_status')=='working'),'identity_failed':sum(r.get('status')=='identity_failed' for r in results),'identity_failed_by_reason':dict(Counter(r.get('identity_status','') for r in results if r.get('status')=='identity_failed'))}
(root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
with (root/'results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    fields=['name','channel_id','group','status','technical_status','identity_status',
            'identity_reason','identity_method','identity_evidence','redirect_url',
            'manifest_flags','paid_gate_classification','reason','url','checked_at',
            'identity_checked_at','paid_gate_audit_checked_at']
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore',lineterminator='\n')
    writer.writeheader()
    for row in results:
        item=dict(row)
        if isinstance(item.get('manifest_flags'),list):
            item['manifest_flags']='; '.join(item['manifest_flags'])
        writer.writerow(item)
def esc(s):return str(s).replace('|','\\|').replace('\n',' ')
lines=['# Перевірка IPTV: фільми, мультфільми, серіали, українське та пізнавальне ТБ UKR/RUS','',f"Завершено: {summary['identity_checked_at'] or summary['checked_at']} (UTC).",'',
'Перевірено з поточної мережі Mac. Для незмінених URL збережено результати попереднього проходу цього ж ранку; нові URL перевірено окремо. Час кожної перевірки вказано в CSV/JSON. Для онлайн-кінозалів мова припускається за описом джерела; це не перевірка звуку. Для кожного URL: ffprobe читає структуру/кодеки; ffmpeg пробує декодувати 3 відеокадри та до 2 секунд потоку з аудіо, якщо воно є. При невдачі додатково перевіряється HTTP-відповідь. Окремий identity-аудит перевірив HLS manifest/redirect для доступних HLS; для всіх 12 нових Dyvy-потоків поточного main додатково знято три кадри та виконано OCR/візуальну перевірку. Для решти довгого списку семантична відповідність кожної передачі та мови звуку не гарантована. Технічна доступність не є висновком про ліцензію чи право на розповсюдження.', '',
'Після виявлення Cinerama-промо в кадрах Discovery, 365 Дней ТВ та Охота и рыбалка всі 84 робочі URL спільного stream8.cinerama.uz виключено з цього verified snapshot; інші джерела цих каналів збережено. URL Viasat Explore на live.tvstitch.com виключено після візуально підтвердженого болгарського повідомлення про тариф. Докази кадрів: [Cinerama promo](identity_evidence/cinerama-promo-ohota.jpg), [Discovery promo](identity_evidence/cinerama-promo-discovery.jpg), [365 Дней ТВ promo](identity_evidence/cinerama-promo-365-days.jpg).', '',
'HTTP 403/401/451 означає відмову доступу; геоблокування не доведене. Тайм-аут означає недоступність під час перевірки, а не остаточне закриття каналу. Альтернативний потік може працювати.','',
'Нові записи DyvyTV: [візуальний аудит 12 потоків](dyvy_identity_audit.json) та [контактний лист кадрів](identity_evidence/dyvy-visual-montage.jpg). API‑записи з IP‑прив’язаним JWT або без переносимого origin HLS (Eco TV, Cars&Stars TV, Суспільне Культура, Конкурент Україна, LUX TV) не опубліковано.','',
]
if paid_audit:
    paid_counts = paid_audit.get('summary', {})
    removed = sum(count for key, count in paid_counts.items() if key != 'decodable_no_gate_marker')
    lines += [
        f"Окремий paid-gate аудит {paid_audit.get('audit_finished_at', '')}: повторно перевірено HTTP/redirect/manifest для всіх {len(paid_audit.get('entries', []))} URL та знято startup-кадр з OCR для кожного технічно робочого URL. Вилучено {removed} записів: {paid_counts.get('entitlement_or_paywall', 0)} явних paywall-кадри, {paid_counts.get('provider_promo_or_identity_review', 0)} provider placeholder, {paid_counts.get('inaccessible_http', 0)} HTTP-помилки та {paid_counts.get('inaccessible_network', 0)} повторні мережеві тайм-аути. {paid_counts.get('decodable_no_gate_marker', 0)} URL не дали gate/error-маркера; це не доводить семантичну відповідність каналу або права на розповсюдження.",
        '[Детальний paid-gate audit](paid_gate_audit.json) · [Кадри paywall/placeholder](identity_evidence/paid-gate/).', '',
    ]
if origin_report:
    lines += [
        f"Окремий origin-аудит фільмів і серіалів: вилучено {origin_report.get('removed_streams', 0)} російськомовних фільмових/серіальних/онлайн-кінозальних URL ({len(origin_report.get('removed_channel_ids', []))} ID), залишено {len(origin_report.get('retained_foreign_movie_channel_ids', []))} каналів із доказаним іноземним або українським походженням. Російська доріжка трактована як локалізація; mixed/uncertain записи вилучено з foreign-only фільмової та серіальної вибірки.",
        '[Політика походження](../content_origin_policy.json) · [Точний origin-звіт](../origin-filter-report.json).', '',
    ]
lines += [
f"**{len(working)} із {len(results)} потоків декодуються; {summary['channels_with_working_stream']} із {len(channels)} каналів мають хоча б один робочий потік.**",'',
'Генератор основного плейлиста вже оновив snapshot; ця перевірка лише формує знімок робочих потоків. [Знімок лише перевірених робочих потоків](working.m3u) актуальний на момент цієї перевірки й не оновлюється щодня. [Детальний CSV](results.csv) · [JSON з помилками й кодеками](results.json).','',
'## Підсумок потоків','', '| Результат | Кількість |','|---|---:|']
for status,count in counts.items():lines.append(f'| {labels[status]} | {count} |')
lines+=['','## Канали','', '| Канал / ID | Робочих / усіх потоків | Результат |','|---|---:|---|']
for cid,rs in sorted(channels.items(),key=lambda x:x[0].lower()):
 n=sum(r['status']=='working' for r in rs)
 label='✅ Є робочий потік' if n else '; '.join(labels[s] for s in sorted({r['status'] for r in rs}))
 lines.append(f'| {esc(cid)} | {n}/{len(rs)} | {label} |')
lines+=['','## Онлайн-кінозали','', '[Лише робочі кінозали — знімок перевірки](cinemas-working.m3u). Мови визначено за описом джерел; звук не розпізнавався.','', '| Кінозал | Робочих / усіх потоків |','|---|---:|']
extra=json.loads((root.parent/'extra_channels.json').read_text())
canonical={'OnlineCinema.'+m.hashlib.sha256(m.normalize(v['name']).encode()).hexdigest()[:12]:v['name'] for v in extra.values()}
for cid,rs in sorted(channels.items(),key=lambda x:canonical.get(x[0],x[0]).casefold()):
    if cid.startswith('OnlineCinema.'):
        lines.append(f"| {esc(canonical.get(cid,rs[0]['name']))} | {sum(r['status']=='working' for r in rs)}/{len(rs)} |")
lines+=['','## Кожне посилання','', '| № | Назва | Результат | Деталі | URL |','|---:|---|---|---|---|']
for r in results:
 v=r.get('video') or {};quality=f"{v.get('codec_name','')} {v.get('width','')}×{v.get('height','')}" if v else ''
 lines.append(f"| {r['index']+1} | {esc(r['name'])} | {labels[r['status']]} | {esc(r['reason'])} {quality} | [Потік]({r['url']}) |")
(root/'README.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
