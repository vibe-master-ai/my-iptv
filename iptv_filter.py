#!/usr/bin/env python3
"""Aggregate public movie/cartoon, Ukrainian broadcast and educational streams with evidenced metadata."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
import json
import ipaddress
import hashlib
import re
import time
import base64

ROOT = Path(__file__).resolve().parent
EXTRA_PATH = ROOT / 'extra_channels.json'
OUT = ROOT / 'my-iptv.m3u'
POLICY = 'movies-cartoons-ukr-rus-ua-broadcast-educational-series-v7-foreign-movie-origin'
ORIGIN_POLICY_PATH = ROOT / 'content_origin_policy.json'
ORIGIN_REPORT_PATH = ROOT / 'origin-filter-report.json'
DYVY_API_URL = 'https://dyvy.tv/api/v1/channels?provider-id=777905&limit=500'
DYVY_REPO = 'DyvyTV official public API'
DYVY_CHANNELS_PATH = ROOT / 'dyvy_channels.json'
SOURCES = [
    ('iptv-org/iptv', f'https://iptv-org.github.io/iptv/categories/{category}.m3u', None)
    for category in ('movies', 'animation', 'kids', 'series', 'documentary', 'education', 'science', 'travel', 'outdoor')
] + [
    ('dearbulut/iptv', f'https://dearbulut.github.io/iptv/playlists/country/{country}.m3u', None)
    for country in ('ua', 'ru')
] + [
    ('Free-TV/IPTV', f'https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_{country}.m3u8', None)
    for country in ('ukraine', 'russia')
] + [('naggdd/iptv', 'https://raw.githubusercontent.com/naggdd/iptv/main/ru.m3u', 'RU'),
    ('smolnp/IPTVru', 'https://raw.githubusercontent.com/smolnp/IPTVru/gh-pages/IPTVru.m3u', 'RU'),
    ('Dimonovich/TV', 'https://raw.githubusercontent.com/Dimonovich/TV/Dimonovich/FREE/TV', None),
    ('substanc1/iptv-ukraine', 'https://raw.githubusercontent.com/substanc1/iptv-ukraine/main/streams/ua.m3u', None),
    ('Spirt007/Tvru', 'https://raw.githubusercontent.com/Spirt007/Tvru/Master/Rus.m3u', None),
    ('egno/egno.github.io', 'https://raw.githubusercontent.com/egno/egno.github.io/master/kino.m3u', None),
    # Adam-ZS maintains separate current RU/UA snapshots with exact iptv-org IDs.
    # They are used as alternative streams only after the same catalog/feed checks.
    ('Adam-ZS/iptv-ru-ua', 'https://raw.githubusercontent.com/Adam-ZS/iptv-ru-ua/main/sources/ru.m3u', 'RU'),
    ('Adam-ZS/iptv-ru-ua', 'https://raw.githubusercontent.com/Adam-ZS/iptv-ru-ua/main/sources/ua.m3u', 'UA')]
# This catalog supplies the general/entertainment Ukrainian broadcast group.  It is
# kept as a country-specific source so the broader Russian country playlist cannot
# accidentally opt into the broadcast policy.
UKRAINE_SOURCE = ('iptv-org/iptv', 'https://iptv-org.github.io/iptv/countries/ua.m3u', 'UA')
SOURCES.append(UKRAINE_SOURCE)
# The API is a public player catalogue used by the official 24tv/DyvyTV embed.
# Only entries in dyvy_channels.json are admitted, and only the portable origin
# HLS encoded in the player's public ``m`` parameter is emitted.  IP-bound JWT
# CDN links and package-protected entries are deliberately excluded.
SOURCES.append((DYVY_REPO, DYVY_API_URL, 'UA'))
# iptv-org currently leaves these two Ukrainian broadcasters uncategorized in
# channels.json and marks their country-playlist rows as Undefined.  Their exact
# UA feeds still carry Ukrainian language metadata, so keep this narrow fallback
# for the requested mainstream channels rather than admitting every Undefined row.
UKRAINIAN_BROADCAST_FALLBACK_IDS = {'STB.ua', 'TET.ua'}
# iptv-org categories are the primary topical gate.  These exact catalog IDs
# are additionally admitted when a reviewed current source labels them
# ``Undefined`` despite the channel's unambiguous educational identity.
EDUCATIONAL_CATEGORIES = {'documentary', 'education', 'science', 'travel', 'outdoor'}
EDUCATIONAL_FALLBACK_IDS = {
    'BigPlanet.ru', 'TNVPlanet.ru', 'ZhivayaPlaneta.ru',
    'ViasatExplore.ua', 'ViasatNature.ua', 'DiscoveryChannel.ru',
}
# Keep the science category from admitting a fictional/scifi stream.
EDUCATIONAL_EXCLUDED_IDS = {'scifi.ru'}
# Entertainment/series brands are admitted only by explicit catalog ID.  This
# prevents a general Russian playlist from turning every entertainment row into
# a series channel while retaining reviewed linear brands requested by the user.
SERIES_FALLBACK_IDS = {'SonyChannel.ru', 'ParamountComedy.ru'}
# A few active RU playlists omit tvg-id for the Discovery family.  These are
# reviewed title aliases limited to those source repositories; the resulting
# catalog/feed language evidence is still required below.
SOURCE_TITLE_ALIASES = {
    'naggdd/iptv': {
        'discovery': 'DiscoveryChannel.ru',
        'discoverychannel': 'DiscoveryChannel.ru',
        'investigationdiscovery': 'InvestigationDiscovery.ru',
    },
    'smolnp/IPTVru': {
        'discoverychannel': 'DiscoveryChannel.ru',
        'red': 'SonyChannel.ru',
        'black': 'SonyTurbo.ru',
    },
    'Spirt007/Tvru': {
        'red': 'SonyChannel.ru',
        'black': 'SonyTurbo.ru',
    },
    'Dimonovich/TV': {
        'discovery': 'DiscoveryChannel.ru',
        'discoverychannel': 'DiscoveryChannel.ru',
        'investigationdiscovery': 'InvestigationDiscovery.ru',
        'discoveryscience': 'DiscoveryScienceEurope.uk',
        'red': 'SonyChannel.ru',
        'black': 'SonyTurbo.ru',
        'paramountcomedy': 'ParamountComedy.ru',
    },
}
# Kids alone is too broad: retain cartoon-oriented channels, not every children's channel.
CARTOON_IDS = set('''PLUSPLUS.ua PixelTV.ua MalyatkoTV.ua NikiJunior.ua NikiKids.ua CinePlusKids.ua
KSTVNinjaTurtles.ua KSTVPawPatrol.ua KSTVSpongeBob.ua
Karusel.ru Mult.ru Multilandia.ru Multimania.ru Multimuzyka.ru Kinomult.ru
Nickelodeon.ru NickJr.ru NicktoonsCIS.ru TiJi.ru GulliGirl.ru STSkids.ru
KapitanFantastika.ru Ryzhiy.ru Vgostyakhuskazki.ru SuperGeroi.ru O.ru
Detskiymir.ru Unikum.ru Smotrim100Detskoe.ru'''.split())
LANG_MAP = {'ukr':'ukr','uk':'ukr','ukrainian':'ukr','українська':'ukr',
            'rus':'rus','ru':'rus','russian':'rus','русский':'rus'}


def fetch(url):
    for attempt in range(3):
        try:
            headers = {'User-Agent':'iptv-aggregator/2.0'}
            if url == DYVY_API_URL:
                headers.update({'Accept':'application/json', 'X-localization':'uk',
                                'X-OTT-Provider-ID':'777905'})
            with urlopen(Request(url, headers=headers), timeout=30) as r:
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


def dyvy_playlist(text, reviewed=None):
    """Convert reviewed public DyvyTV API rows into portable M3U entries.

    Dyvy's player API returns two kinds of links: a direct origin URL embedded
    in the public ``m`` parameter for FAST channels, and JWT URLs bound to the
    API caller's IP for some live channels.  The latter are not portable and
    are intentionally omitted from the published playlist.
    """
    payload = json.loads(text)
    rows = payload.get('data', []) if isinstance(payload, dict) else payload
    reviewed = reviewed if reviewed is not None else _dyvy_reviewed()
    output = ['#EXTM3U']
    for row in rows:
        if not isinstance(row, dict):
            continue
        slug = row.get('slug', '')
        policy = reviewed.get(slug)
        if not policy or row.get('type') not in ('live', 'fast') or not row.get('link'):
            continue
        # Never turn a package-gated row into a public URL.
        if row.get('package_block'):
            continue
        parts = urlsplit(row['link'])
        params = dict((k, v[0]) for k, v in parse_qs(parts.query).items())
        direct = ''
        if params.get('m'):
            try:
                direct = base64.urlsafe_b64decode(params['m'] + '=' * (-len(params['m']) % 4)).decode('utf-8')
            except (ValueError, UnicodeDecodeError):
                continue
        elif (parts.hostname or '').lower() == 'playout-stream.adt-playout.top':
            direct = row['link']
        dparts = urlsplit(direct)
        if (dparts.scheme, (dparts.hostname or '').lower()) != ('https', 'playout-stream.adt-playout.top'):
            continue
        if not dparts.path.lower().endswith('.m3u8') or not public_stream(direct):
            continue
        title = str(row.get('name') or policy['name']).replace('"', "'").strip()
        group = f"{policy['kind']} | UKR"
        cid = f'Dyvy.{slug}.ua'
        output.append(f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{title}" tvg-language="ukr" group-title="{group}",{title}')
        output.append(direct)
    return '\n'.join(output) + '\n'


def _dyvy_reviewed():
    if not DYVY_CHANNELS_PATH.exists():
        return {}
    rows = json.loads(DYVY_CHANNELS_PATH.read_text(encoding='utf-8'))
    return {row['slug']: row for row in rows if isinstance(row, dict) and row.get('slug')}


def normalize(name):
    name = re.sub(r'\((?:\d+|\d+[pi]|офиц)\)|\[(?:geo-blocked|not 24/7)\]', '', name, flags=re.I)
    name = re.sub(r'\([^)]*(?:HD|SD|\d{3,4}[pi])[^)]*\)', '', name, flags=re.I)
    name = re.sub(r'\b(?:HD|SD|FHD|UHD|4K|HEVC|H264|H265)\b', '', name, flags=re.I)
    name = re.sub(r'\s+\+\d+$', '', name)
    return re.sub(r'[^\w]', '', name.casefold())


def public_stream(url):
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.hostname.lower() == 'localhost':
            return False
        try:
            return ipaddress.ip_address(parts.hostname).is_global
        except ValueError:
            return not parts.hostname.endswith(('.local', '.internal'))
    except ValueError:
        return False


def cinema_stream_key(url):
    parts = urlsplit(url)
    return parts.netloc.lower() + parts.path if parts.hostname == 'kinowalk.hopto.org' else url


def load_origin_policy():
    """Load the evidence-backed foreign-film origin policy.

    The origin filter is deliberately conservative: an unlisted Russian movie
    channel is treated as mixed/uncertain and is excluded from the foreign-film
    subset.  Channels outside Russian-language movies and reviewed online
    cinemas do not use this policy.
    """
    if not ORIGIN_POLICY_PATH.exists():
        raise ValueError(f'Missing content origin policy: {ORIGIN_POLICY_PATH}')
    policy = json.loads(ORIGIN_POLICY_PATH.read_text(encoding='utf-8'))
    if not isinstance(policy, dict) or not isinstance(policy.get('channels'), dict):
        raise ValueError('Invalid content origin policy')
    return policy


def origin_review(cid, kind, langs, extra, policy):
    """Return the origin decision and evidence for one generated entry."""
    outside_scope = {
        'decision': 'outside_russian_movie_scope',
        'origin_evidence': 'Origin restriction applies only to Russian-language movie and online-cinema records.',
        'evidence_urls': [],
    }
    if kind != 'Фільми' or 'rus' not in langs:
        return outside_scope
    if extra:
        decision = policy.get('default_russian_online_cinema_decision', 'deny_uncertain_or_mixed')
        evidence = policy.get('default_origin_evidence', '')
        return {'decision': decision, 'origin_evidence': evidence,
                'evidence_urls': []}
    record = policy.get('channels', {}).get(cid)
    if not record:
        return {
            'decision': policy.get('default_russian_movie_decision', 'deny_uncertain_or_mixed'),
            'origin_evidence': policy.get('default_origin_evidence', ''),
            'evidence_urls': [],
        }
    return {
        'decision': record.get('decision', policy.get('default_russian_movie_decision', 'deny_uncertain_or_mixed')),
        'origin_evidence': record.get('origin_evidence', policy.get('default_origin_evidence', '')),
        'evidence_urls': record.get('evidence_urls', []),
    }


def main():
    origin_policy = load_origin_policy()
    urls = ['https://iptv-org.github.io/api/channels.json', 'https://iptv-org.github.io/api/feeds.json'] + [
        f'https://iptv-org.github.io/iptv/languages/{lang}.m3u' for lang in ('ukr','rus')
    ] + [url for _,url,_ in SOURCES]
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = dict(zip(urls, pool.map(fetch, urls)))
    channels = json.loads(texts[urls[0]])
    if not isinstance(channels, list) or not channels:
        raise ValueError('Invalid channel database')
    database = {c['id']:c for c in channels}
    feeds = json.loads(texts['https://iptv-org.github.io/api/feeds.json'])
    feed_languages, catalog_languages = {}, defaultdict(set)
    for feed in feeds:
        languages = set(feed.get('languages', []))
        feed_languages[feed['channel'] + '@' + feed['id']] = languages
        catalog_languages[feed['channel']].update(languages)

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
    extra_channels = json.loads(EXTRA_PATH.read_text()) if EXTRA_PATH.exists() else {}
    extra_by_url = {cinema_stream_key(u):v for v in extra_channels.values() for u in v.get('urls', [])}
    entries, seen, audit = [], set(), []
    origin_exclusions = []
    counts, contributions = Counter(), Counter()
    for repo, source, country_hint in SOURCES:
        source_text = dyvy_playlist(texts[source]) if source == DYVY_API_URL else texts[source]
        for meta, url in parse_entries(source_text):
            if not public_stream(url) or re.search(r'\.(mp4|mkv|avi)(?:\?|$)', url, re.I):
                continue
            line = meta[0]
            try:
                _, title = split_extinf(line)
            except ValueError:
                continue
            extra = extra_by_url.get(cinema_stream_key(url))
            cid = attr(line,'tvg-id').split('@')[0]
            alias_used = False
            if repo == DYVY_REPO:
                # dyvy_playlist emits an allowlisted synthetic ID; do not try
                # to resolve it through the iptv-org title catalogue.
                pass
            elif cid not in database:
                alias = SOURCE_TITLE_ALIASES.get(repo, {}).get(normalize(title), '')
                if alias:
                    cid = alias
                    alias_used = True
                else:
                    possible = names[normalize(title)]
                    if country_hint:
                        possible = {i for i in possible if database[i]['country'] == country_hint}
                    cid = next(iter(possible)) if len(possible) == 1 else ''
            if extra:
                cid = ''  # Exact reviewed cinema stream overrides a colliding broadcast-channel name.
            reviewed_dyvy = _dyvy_reviewed().get(cid.removeprefix('Dyvy.').removesuffix('.ua')) if cid.startswith('Dyvy.') else None
            channel = database.get(cid, {})
            if reviewed_dyvy:
                channel = {'country': 'UA', 'categories': [reviewed_dyvy['catalog_category']]}
            if channel.get('is_nsfw'):
                continue
            raw_languages = attr(line,'tvg-language').lower()
            if raw_languages:
                langs = {LANG_MAP[x] for x in re.split(r'[;,/|\s]+',raw_languages) if x in LANG_MAP}
                evidence = 'explicit language tag'
            else:
                full_id = attr(line, 'tvg-id')
                if full_id in feed_languages:
                    langs = feed_languages[full_id] & {'ukr', 'rus'}
                    evidence = 'exact feed language metadata'
                else:
                    langs = url_lang[url] or id_lang[cid] or (catalog_languages[cid] & {'ukr', 'rus'})
                    evidence = 'language playlist URL' if url_lang[url] else ('language playlist channel ID' if id_lang[cid] else 'channel feed language metadata')
            if alias_used and evidence == 'channel feed language metadata':
                evidence = 'reviewed source title alias + channel feed language metadata'
            extra = extra or (extra_channels.get(normalize(title)) if repo in ('naggdd/iptv','Dimonovich/TV','Spirt007/Tvru','egno/egno.github.io') else None)
            if not langs and not raw_languages and not cid and extra:
                langs = set(extra['languages'])
                evidence = extra['evidence'] + ' (inferred; audio language not verified)'
            if not langs:
                continue
            categories = set(channel.get('categories', []))
            if reviewed_dyvy:
                evidence = reviewed_dyvy['source']
            if not cid and extra:
                categories.add(extra['category'])
                cid = 'OnlineCinema.' + hashlib.sha256(normalize(extra['name']).encode()).hexdigest()[:12]
            categories.update(x.strip().lower() for x in attr(line,'group-title').split(';'))
            if reviewed_dyvy:
                kind = reviewed_dyvy['kind']
            elif 'animation' in categories or cid in CARTOON_IDS:
                kind = 'Мультфільми'
            elif 'series' in categories or cid in SERIES_FALLBACK_IDS:
                kind = 'Серіали'
            elif 'movies' in categories:
                kind = 'Фільми'
            elif cid not in EDUCATIONAL_EXCLUDED_IDS and (
                    categories & EDUCATIONAL_CATEGORIES or cid in EDUCATIONAL_FALLBACK_IDS):
                kind = 'Пізнавальні'
            elif (country_hint == 'UA' and channel.get('country') == 'UA'
                  and (categories & {'general', 'entertainment'}
                       or cid in UKRAINIAN_BROADCAST_FALLBACK_IDS)
                  and 'ukr' in langs):
                kind = 'Українське ТБ'
            else:
                continue
            origin = origin_review(cid, kind, langs, extra, origin_policy)
            if origin['decision'].startswith('deny_'):
                report_cid = cid or 'Unresolved.' + hashlib.sha256(normalize(title).encode()).hexdigest()[:12]
                origin_exclusions.append({
                    'name': title,
                    'channel_id': report_cid,
                    'channel_id_raw': cid,
                    'languages': sorted(langs),
                    'category': kind,
                    'source': source,
                    'url': url,
                    'decision': origin['decision'],
                    'origin_evidence': origin['origin_evidence'],
                    'evidence_urls': origin['evidence_urls'],
                    'language_evidence': evidence,
                })
                continue
            if url in seen:
                continue
            seen.add(url)
            language = '/'.join(x.upper() for x in sorted(langs))
            group = f'{kind} | {language}'
            line = set_attr(line, 'group-title', group)
            line = set_attr(line, 'tvg-language', ';'.join(sorted(langs)))
            if cid and attr(line,'tvg-id').split('@')[0] != cid:
                line = set_attr(line,'tvg-id',cid)
            entries.append((group, title, [line] + meta[1:], url))
            counts[group] += 1
            contributions[repo] += 1
            audit.append({'name':title,'channel_id':cid,'languages':sorted(langs),
                          'category':kind,'source':source,'url':url,'language_evidence':evidence,
                          'origin_decision':origin['decision'],
                          'origin_evidence':origin['origin_evidence'],
                          'origin_evidence_urls':origin['evidence_urls']})
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
    by_decision = Counter(item['decision'] for item in origin_exclusions)
    retained_foreign = sorted({
        item['channel_id'] for item in audit
        if item.get('origin_decision', '').startswith('allow_')
    })
    origin_report = {
        'policy_version': origin_policy.get('version'),
        'policy': POLICY,
        'scope': origin_policy.get('scope'),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'removed_streams': len(origin_exclusions),
        'removed_channel_ids': sorted({item['channel_id'] for item in origin_exclusions}),
        'removed_by_decision': dict(sorted(by_decision.items())),
        'retained_foreign_movie_channel_ids': retained_foreign,
        'removed_entries': origin_exclusions,
        'note': 'Foreign means the channel programming policy is evidenced as non-Russian-origin content; a Russian audio track is localization. Live programming may change and this does not classify each future film frame-by-frame.',
    }
    (ROOT / 'origin-filter-report.json').write_text(json.dumps(origin_report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(status,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
