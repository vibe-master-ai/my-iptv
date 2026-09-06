#!/usr/bin/env python3
"""Review technically working streams for obvious wrong-content splash screens.

The playback checker proves that a URL returned decodable video.  It cannot prove
that the video is the advertised channel.  This pass records lightweight HLS
manifest evidence for every technically working URL and applies conservative
identity rejections only where a provider or sampled frame is known to be wrong.
It deliberately does not claim semantic verification of every programme.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import csv
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import iptv_filter as m

ROOT = Path(__file__).resolve().parent
MANIFEST_LIMIT = 64 * 1024
PROMO_MARKERS = (
    # Provider names in a manifest or URL are not themselves wrong-content
    # evidence (for example, a normal Bollywood frame comes from an edge
    # whose path contains ``cinerama``).  Match visible-slate text only.
    '180+ телеканалов', '180+ telekanallar',
    'qr-код', 'qr-kodni', 'смотрите телеканалы',
)
ENTITLEMENT_MARKERS = (
    'не включен в ваш тариф', 'не включён в ваш тариф',
    'канал не включен', 'канал не включён', 'тарифный план',
    'не подключен к вашему тарифу', 'not included in your plan',
    'not included in your package', 'subscription required',
    'channel is not included',
)

# These are the endpoints that were actually inspected in this pass.  The
# shared stream8 Cinerama host returned the same Cinerama subscription slate
# when sampled as advertised Discovery, 365 Дней ТВ and Охота и рыбалка.  Keep
# other providers available as alternatives, but do not publish this host in
# the verified snapshot until a future identity review clears it.
SAMPLED_PROVIDER_REJECTIONS = {
    'stream8.cinerama.uz': (
        'Shared Cinerama provider returned a Cinerama promotional slate in '
        'sampled frames for advertised Discovery, 365 Дней ТВ and Охота и '
        'рыбалка; channel identity is not reliable for this endpoint.'
    ),
}

# User visual review identified this exact generated Viasat Explore URL as a
# Bulgarian entitlement splash.  Keep the override URL-specific so a later
# properly evidenced UA/RU alternative can be admitted.
MANUAL_REJECTIONS = {
    # The value is filled by channel/id matching below because the tokenized
    # URL changes in upstream snapshots.
    'ViasatExplore.ua': 'Observed Bulgarian tariff/entitlement splash instead of the advertised channel (user visual review).',
}
VISUAL_AUDIT_PATH = ROOT / 'dyvy_identity_audit.json'


def _manifest(url):
    """Return a small textual HLS sample and redirect target when available."""
    parts = urlsplit(url)
    if not (parts.path.lower().endswith(('.m3u8', '.m3u')) or 'm3u8' in parts.query.lower()):
        return '', '', ''
    try:
        request = Request(url, headers={'User-Agent': 'iptv-identity-audit/1.0'})
        with urlopen(request, timeout=8) as response:
            body = response.read(MANIFEST_LIMIT).decode('utf-8', errors='replace')
            return body, response.geturl(), ''
    except HTTPError as exc:
        return '', getattr(exc, 'url', '') or '', f'HTTP {exc.code}'
    except (URLError, TimeoutError, OSError) as exc:
        return '', '', f'{type(exc).__name__}: {str(exc)[:180]}'


def review(row, entry):
    """Annotate one checker row; status becomes identity_failed only on evidence."""
    meta, url = entry
    result = dict(row)
    prior_identity_status = row.get('identity_status', '')
    result['technical_status'] = row.get('status', '')
    result['identity_checked_at'] = datetime.now(timezone.utc).isoformat()
    result['identity_status'] = (prior_identity_status if row.get('status') == 'identity_failed'
                                 and prior_identity_status else 'reviewed_metadata_only')
    result['identity_reason'] = ''
    result['identity_method'] = 'ffprobe metadata; HLS manifest text where available'
    result['redirect_url'] = ''
    result['manifest_flags'] = []
    if row.get('status') != 'working':
        # Preserve a prior wrong-content verdict when the technical status was
        # reused by expand_check.py.  Re-running this audit must not erase the
        # evidence that removed a known promo/entitlement stream.
        if row.get('status') == 'identity_failed' and prior_identity_status:
            for field in ('identity_reason', 'identity_method', 'identity_evidence',
                          'redirect_url', 'manifest_flags'):
                if field in row:
                    result[field] = row[field]
        else:
            result['identity_status'] = 'not_applicable'
        return result

    host = (urlsplit(url).hostname or '').lower()
    if host in SAMPLED_PROVIDER_REJECTIONS:
        result['status'] = 'identity_failed'
        result['identity_status'] = 'rejected_wrong_content'
        result['identity_reason'] = SAMPLED_PROVIDER_REJECTIONS[host]
        result['identity_evidence'] = 'identity_evidence/cinerama-promo-ohota.jpg; identity_evidence/cinerama-promo-discovery.jpg; identity_evidence/cinerama-promo-365-days.jpg'
        return result

    # Tokenized tvstitch snapshots for this catalog ID have changed over time;
    # all current generated Viasat Explore URLs are reviewed by channel ID and
    # provider rather than by copying a fragile token into this script.
    if row.get('channel_id') == 'ViasatExplore.ua' and host == '777905.live.tvstitch.com':
        result['status'] = 'identity_failed'
        result['identity_status'] = 'rejected_wrong_content'
        result['identity_reason'] = MANUAL_REJECTIONS['ViasatExplore.ua']
        result['identity_evidence'] = 'manual visual review recorded 2026-09-06'
        return result

    # New Dyvy additions have a separate sampled-frame/OCR record.  Keep this
    # URL-specific so future API refreshes must earn a fresh visual review.
    if row.get('channel_id', '').startswith('Dyvy.') and VISUAL_AUDIT_PATH.exists():
        visual_rows = {item.get('url'): item for item in json.loads(VISUAL_AUDIT_PATH.read_text())}
        visual = visual_rows.get(url)
        if visual:
            result['identity_method'] = visual.get('method', 'sampled frames and OCR')
            result['identity_evidence'] = visual.get('sample', 'dyvy_identity_audit.json')
            result['identity_reason'] = 'Sampled visual channel/topic content; no promo or entitlement marker detected.'
            if visual.get('status') == 'visual_failed':
                result['status'] = 'identity_failed'
                result['identity_status'] = 'rejected_visual_marker'
                result['identity_reason'] = 'Sampled frame OCR matched a promotional or entitlement marker.'
            else:
                result['identity_status'] = 'reviewed_visual_sample'
            return result

    body, redirected, fetch_error = _manifest(url)
    result['redirect_url'] = redirected
    haystack = body.lower()
    flags = [marker for marker in PROMO_MARKERS + ENTITLEMENT_MARKERS if marker in haystack]
    result['manifest_flags'] = sorted(set(flags))
    if flags:
        result['status'] = 'identity_failed'
        result['identity_status'] = 'rejected_manifest_splash'
        result['identity_reason'] = 'Manifest/HTTP content contains a promotional or entitlement splash marker: ' + ', '.join(sorted(set(flags)))
        result['identity_evidence'] = 'manifest sample'
    elif fetch_error:
        result['identity_status'] = 'reviewed_metadata_only'
        result['identity_reason'] = 'HLS manifest could not be sampled after technical decode: ' + fetch_error
    else:
        result['identity_status'] = 'reviewed_metadata_manifest'
    return result


def main():
    results = json.loads((ROOT / 'results.json').read_text())
    entries = m.parse_entries((ROOT / 'input.m3u').read_text())
    with ThreadPoolExecutor(max_workers=20) as pool:
        reviewed = list(pool.map(review, results, entries))
    (ROOT / 'results.json').write_text(json.dumps(reviewed, ensure_ascii=False, indent=2) + '\n')
    with (ROOT / 'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ['name', 'channel_id', 'group', 'status', 'technical_status',
                  'identity_status', 'identity_reason', 'identity_method',
                  'redirect_url', 'manifest_flags', 'reason', 'url', 'checked_at',
                  'identity_checked_at']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        for row in reviewed:
            row = dict(row)
            row['manifest_flags'] = '; '.join(row.get('manifest_flags', []))
            writer.writerow(row)
    failed = [r for r in reviewed if r.get('status') == 'identity_failed']
    print(json.dumps({
        'technical_working': sum(r.get('technical_status') == 'working' for r in reviewed),
        'identity_failed': len(failed),
        'identity_failed_by_reason': {
            key: sum(key in r.get('identity_status', '') for r in failed)
            for key in ('rejected_wrong_content', 'rejected_manifest_splash')
        },
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
