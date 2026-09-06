#!/usr/bin/env python3
"""Fail CI if an origin-denied movie/series channel returns to the playlist."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import iptv_filter as m

ROOT = Path(__file__).resolve().parent.parent


def denied_ids(policy):
    records = {}
    for table_name in ('channels', 'series_channels'):
        for cid, record in policy.get(table_name, {}).items():
            if str(record.get('decision', '')).startswith('deny_'):
                records[cid] = record['decision']
    return records


def main():
    policy = json.loads((ROOT / 'content_origin_policy.json').read_text(encoding='utf-8'))
    denied = denied_ids(policy)
    audit = json.loads((ROOT / 'channel-audit.json').read_text(encoding='utf-8'))
    audit_failures = [row for row in audit if str(row.get('origin_decision', '')).startswith('deny_')]
    if audit_failures:
        raise SystemExit(f'Origin policy regression: denied rows admitted in channel-audit.json ({len(audit_failures)})')

    playlist_failures = []
    for meta, url in m.parse_entries((ROOT / 'my-iptv.m3u').read_text(encoding='utf-8')):
        cid = m.attr(meta[0], 'tvg-id').split('@')[0]
        group = m.attr(meta[0], 'group-title')
        if cid in denied and ('Фільми' in group or 'Серіали' in group) and 'RUS' in group:
            playlist_failures.append({'channel_id': cid, 'group': group, 'url': url})
    if playlist_failures:
        raise SystemExit(f'Origin policy regression: denied IDs returned to my-iptv.m3u ({len(playlist_failures)})')

    report = json.loads((ROOT / 'origin-filter-report.json').read_text(encoding='utf-8'))
    removed_ids = set(report.get('removed_channel_ids', []))
    missing_report = sorted(cid for cid in denied if cid in removed_ids)
    print(json.dumps({
        'denied_policy_ids': len(denied),
        'admitted_denied_rows': 0,
        'playlist_failures': 0,
        'denied_ids_in_current_removal_report': len(missing_report),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
