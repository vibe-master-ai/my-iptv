#!/usr/bin/env python3
"""Fail CI if an origin-denied movie/series channel returns to the playlist."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import iptv_filter as m

ROOT = Path(__file__).resolve().parent.parent
PAID_GATE_BLOCKED = {
    'entitlement_or_paywall',
    'inaccessible_auth_or_geo',
    'error_or_auth_slate',
    'inaccessible_http',
}


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

    paid_audit_path = ROOT / 'checks' / 'paid_gate_audit.json'
    paid_audit = json.loads(paid_audit_path.read_text(encoding='utf-8')) if paid_audit_path.exists() else {}
    # The audit is deliberately run before build_report: its blocked rows are
    # the evidence used to quarantine those streams.  Regression is about the
    # final verified snapshot, so only a blocked URL that survived into
    # working.m3u is a failure.  Historical/current audit evidence remains in
    # paid_gate_audit.json and is linked from the report for review.
    verified_urls = {url for _meta, url in m.parse_entries(
        (ROOT / 'checks' / 'working.m3u').read_text(encoding='utf-8'))} if (ROOT / 'checks' / 'working.m3u').exists() else set()
    paid_failures = [row for row in paid_audit.get('entries', [])
                     if row.get('classification') in PAID_GATE_BLOCKED
                     and row.get('url') in verified_urls]
    if paid_failures:
        raise SystemExit(f'Paid gate regression: restricted URLs remain in checks/working.m3u ({len(paid_failures)})')
    result_failures = []
    exclusion_path = ROOT / 'checks' / 'paid_gate_exclusions.json'
    exclusions = json.loads(exclusion_path.read_text(encoding='utf-8')) if exclusion_path.exists() else {}
    quarantined_ids = set(exclusions.get('quarantined_channel_ids', []))
    result_path = ROOT / 'checks' / 'results.json'
    if result_path.exists():
        checked_results = json.loads(result_path.read_text(encoding='utf-8'))
        result_failures = [row for row in checked_results
                           if row.get('status') == 'working'
                           and (row.get('paid_gate_classification') in PAID_GATE_BLOCKED
                                or row.get('channel_id') in quarantined_ids)]
    if result_failures:
        raise SystemExit(f'Paid gate regression: restricted entries marked working in results.json ({len(result_failures)})')
    verified_failures = [row for row in paid_audit.get('entries', [])
                         if row.get('classification') in PAID_GATE_BLOCKED
                         and row.get('url') in verified_urls]
    if verified_failures:
        raise SystemExit(f'Paid gate regression: restricted URLs returned to checks/working.m3u ({len(verified_failures)})')

    report = json.loads((ROOT / 'origin-filter-report.json').read_text(encoding='utf-8'))
    removed_ids = set(report.get('removed_channel_ids', []))
    missing_report = sorted(cid for cid in denied if cid in removed_ids)
    print(json.dumps({
        'denied_policy_ids': len(denied),
        'admitted_denied_rows': 0,
        'playlist_failures': 0,
        'denied_ids_in_current_removal_report': len(missing_report),
        'paid_gate_failures': 0,
        'verified_streams_with_paid_gate_failures': 0,
        'quarantined_channel_ids': len(quarantined_ids),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
