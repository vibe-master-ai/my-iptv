#!/usr/bin/env python3
"""Aggregate public movie/cartoon streams with evidenced Ukrainian/Russian metadata."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import urlsplit
import json
import re
import time

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'my-iptv.m3u'
POLICY = 'movies-cartoons-ukr-rus-v2'
SOURCES = [
    ('iptv-org/iptv', f'https://iptv-org.github.io/iptv/categories/{category}.m3u', None)
    for category in ('movies', 'animation', 'kids')
] + [
    ('dearbulut/iptv', f'https://dearbulut.github.io/iptv/playlists/country/{country}.m3u', None)
    for country in ('ua', 'ru')
] + [
    ('Free-TV/IPTV', f'https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_{country}.m3u8', None)
    for country in ('ukraine', 'russia')
] + [('naggdd/iptv', 'https://raw.githubusercontent.com/naggdd/iptv/main/ru.m3u', 'RU')]
# Kids alone is too broad: retain cartoon-oriented channels, not every children's channel.
CARTOON_IDS = set('''PlusPlus.ua PixelTV.ua MalyatkoTV.ua NIKIJunior.ua NIKIKids.ua
Karusel.ru Mult.ru Multilandia.ru Multimania.ru Multimuzyka.ru Kinomult.ru
Nickelodeon.ru NickJr.ru NicktoonsCIS.ru TiJi.ru GulliGirl.ru STSkids.ru
KapitanFantastika.ru Ryzhiy.ru Vgostyakhuskazki.ru SuperGeroi.ru O.ru
Detskiymir.ru Unikum.ru Smotrim100Detskoe.ru'''.split())
LANG_MAP = {'ukr':'ukr','uk':'ukr','ukrainian':'ukr','українська':'ukr',
            'rus':'rus','ru':'rus','russian':'rus','русский':'rus'}


def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent':'iptv-aggregator/2.0'}), timeout=30) as r:
                return r.read().decode('utf-8-sig')
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def parse_entries(text):
    if not text.lstrip().startswith('#EXTM3U'):
        raise ValueError('Not an M3U playlist')
    entries, pending = [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            pending = [line]
        elif pending and line.startswith('#'):
            pending.append(line)
        elif pending and line:
            entries.append((pending, line))
            pending = []
    if not entries:
        raise ValueError('Empty source playlist')
    return entries


def attr(line, name):
    m = re.search(r'(?:^|\s)' + re.escape(name) + r'="([^"]*)"', line, re.I)
    return m.group(1).strip() if m else ''


def split_extinf(line):
    quoted = False
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ',' and not quoted:
            return line[:i], line[i+1:].strip()
    raise ValueError('EXTINF missing channel name')


def set_attr(line, name, value):
    metadata, title = split_extinf(line)
    metadata = re.sub(r'\s+' + re.escape(name) + r'="[^"]*"', '', metadata, flags=re.I)
    return f'{metadata} {name}="{value}",{title}'


def normalize(name):
    name = re.sub(r'\((?:\d+|\d+[pi]|офиц)\)|\[(?:geo-blocked|not 24/7)\]', '', name, flags=re.I)
    name = re.sub(r'\b(?:HD|SD|FHD|UHD|4K)\b', '', name, flags=re.I)
    return re.sub(r'[^\w]', '', name.casefold())


def main():
    urls = ['https://iptv-org.github.io/api/channels.json'] + [
        f'https://iptv-org.github.io/iptv/languages/{lang}.m3u' for lang in ('ukr','rus')
    ] + [url for _,url,_ in SOURCES]
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = dict(zip(urls, pool.map(fetch, urls)))
    channels = json.loads(texts[urls[0]])
    if not isinstance(channels, list) or not channels:
        raise ValueError('Invalid channel database')
    database = {c['id']:c for c in channels}
    id_lang, url_lang = defaultdict(set), defaultdict(set)
    for lang in ('ukr','rus'):
        for meta, url in parse_entries(texts[f'https://iptv-org.github.io/iptv/languages/{lang}.m3u']):
            cid = attr(meta[0], 'tvg-id').split('@')[0]
            if cid:
                id_lang[cid].add(lang)
            url_lang[url].add(lang)
    names = defaultdict(set)
    for c in channels:
        for name in [c['name']] + c.get('alt_names', []):
            names[normalize(name)].add(c['id'])
    entries, seen, audit = [], set(), []
    counts, contributions = Counter(), Counter()
    for repo, source, country_hint in SOURCES:
        for meta, url in parse_entries(texts[source]):
            if urlsplit(url).scheme not in ('http','https') or re.search(r'\.(mp4|mkv|avi)(?:\?|$)', url, re.I):
                continue
            line = meta[0]
            _, title = split_extinf(line)
            cid = attr(line,'tvg-id').split('@')[0]
            if cid not in database:
                possible = names[normalize(title)]
                if country_hint:
                    possible = {i for i in possible if database[i]['country'] == country_hint}
                cid = next(iter(possible)) if len(possible) == 1 else ''
            channel = database.get(cid, {})
            if channel.get('is_nsfw'):
                continue
            raw_languages = attr(line,'tvg-language').lower()
            if raw_languages:
                langs = {LANG_MAP[x] for x in re.split(r'[;,/|\s]+',raw_languages) if x in LANG_MAP}
                evidence = 'explicit language tag'
            else:
                langs = url_lang[url] or id_lang[cid]
                evidence = 'language playlist URL' if url_lang[url] else 'language playlist channel ID'
            if not langs:
                continue
            categories = set(channel.get('categories', []))
            categories.update(x.strip().lower() for x in attr(line,'group-title').split(';'))
            if 'animation' in categories or cid in CARTOON_IDS:
                kind = 'Мультфільми'
            elif 'movies' in categories:
                kind = 'Фільми'
            else:
                continue
            if url in seen:
                continue
            seen.add(url)
            language = '/'.join(x.upper() for x in sorted(langs))
            group = f'{kind} | {language}'
            line = set_attr(line, 'group-title', group)
            line = set_attr(line, 'tvg-language', ';'.join(sorted(langs)))
            if cid and not attr(line,'tvg-id'):
                line = set_attr(line,'tvg-id',cid)
            entries.append((group, title, [line] + meta[1:], url))
            counts[group] += 1
            contributions[repo] += 1
            audit.append({'name':title,'channel_id':cid,'languages':sorted(langs),
                          'category':kind,'source':source,'language_evidence':evidence})
    if not entries or not any('ukr' in e['languages'] for e in audit) or not all(any(e['category']==k for e in audit) for k in ('Фільми','Мультфільми')):
        raise ValueError('Missing required language/category coverage; preserving published playlist')
    previous_path = ROOT / 'status.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    if previous.get('policy') == POLICY and len(entries) < previous['streams'] * 0.5:
        raise ValueError('Unexpected loss of over half of streams; preserving published playlist')
    output = ['#EXTM3U']
    for _,_,meta,url in sorted(entries, key=lambda x:(x[0],x[1].casefold(),x[3])):
        output.extend(meta + [url])
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text('\n'.join(output)+'\n',encoding='utf-8')
    tmp.replace(OUT)
    status = {'policy':POLICY,'updated_at':datetime.now(timezone.utc).isoformat(),
              'streams':len(entries),'unique_channel_ids':len({e['channel_id'] for e in audit if e['channel_id']}),
              'categories':dict(counts),'source_contributions':dict(contributions)}
    previous_path.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'channel-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(status,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
