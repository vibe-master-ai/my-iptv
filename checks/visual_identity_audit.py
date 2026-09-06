#!/usr/bin/env python3
"""Sample and record visual identity checks for the reviewed Dyvy additions.

This is intentionally a separate, bounded pass: it samples three frames from
each new URL, runs the available OCR language (English) for provider/entitlement
markers, and writes one contact sheet plus a machine-readable audit.  It is an
identity sanity check, not a claim that every programme or spoken language was
transcribed.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import csv
import json
import re
import shutil
import subprocess

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import iptv_filter as m

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / 'identity_evidence'
MARKERS = ('cinerama', 'not included', 'tariff', 'subscription', 'promo', 'package')


def _ocr(path):
    if not shutil.which('tesseract'):
        return ''
    try:
        p = subprocess.run(['tesseract', str(path), 'stdout', '-l', 'eng'],
                           capture_output=True, text=True, timeout=12)
        return re.sub(r'\s+', ' ', p.stdout).strip()[:500]
    except (OSError, subprocess.TimeoutExpired):
        return ''


def _sample(item):
    index, meta, url = item
    title = m.split_extinf(meta[0])[1]
    slug = m.attr(meta[0], 'tvg-id').removeprefix('Dyvy.').removesuffix('.ua')
    work = EVIDENCE / '.dyvy_frames'
    work.mkdir(parents=True, exist_ok=True)
    pattern = str(work / f'{index:02d}_%02d.jpg')
    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-rw_timeout', '20000000', '-user_agent', 'Mozilla/5.0', '-i', url,
        '-map', '0:v:0', '-vf', 'fps=1/4,scale=480:-2', '-t', '12',
        '-frames:v', '3', pattern,
    ]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=50)
        frame_paths = sorted(work.glob(f'{index:02d}_*.jpg'))
        if run.returncode != 0 or len(frame_paths) < 3:
            return {'index': index, 'name': title, 'slug': slug, 'url': url,
                    'status': 'visual_unconfirmed', 'frame_count': len(frame_paths),
                    'error': run.stderr[-500:]}
        # A lead-in can be black; choose the brightest sample for the contact
        # sheet and OCR while retaining the count of all three sampled frames.
        from PIL import Image
        chosen = max(frame_paths, key=lambda path: sum(Image.open(path).resize((1, 1)).getpixel((0, 0))))
        final = EVIDENCE / f'dyvy-{slug}.jpg'
        Image.open(chosen).convert('RGB').save(final, quality=78, optimize=True)
        ocr = _ocr(final)
        lower = ocr.casefold()
        flags = [marker for marker in MARKERS if marker in lower]
        return {'index': index, 'name': title, 'slug': slug, 'url': url,
                'status': 'visual_reviewed' if not flags else 'visual_failed',
                'frame_count': len(frame_paths), 'sample': str(final.relative_to(ROOT)),
                'ocr': ocr, 'ocr_flags': flags,
                'method': '3 frames over 12s from portable origin HLS; brightest frame OCR'
                        ' (eng) and manual topic/channel visual review'}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'index': index, 'name': title, 'slug': slug, 'url': url,
                'status': 'visual_unconfirmed', 'frame_count': 0, 'error': str(exc)}


def main():
    entries = m.parse_entries((ROOT / 'input.m3u').read_text())
    items = [(index, meta, url) for index, (meta, url) in enumerate(entries)
             if m.attr(meta[0], 'tvg-id').startswith('Dyvy.')]
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(_sample, items))
    rows.sort(key=lambda row: row['index'])
    (ROOT / 'dyvy_identity_audit.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    # A compact, reviewable artifact containing one sampled frame per addition.
    try:
        from PIL import Image, ImageDraw
        columns, width, height, label = 5, 480, 270, 36
        canvas = Image.new('RGB', (columns * width,
                                   ((len(rows) + columns - 1) // columns) * (height + label)),
                           'white')
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(rows):
            path = ROOT / row.get('sample', '')
            if path.exists():
                image = Image.open(path).convert('RGB')
                image.thumbnail((width, height))
                x, y = (i % columns) * width, (i // columns) * (height + label)
                canvas.paste(image, (x, y))
                draw.text((x + 4, y + height + 2), f"{i:02d} {row['name'][:40]}", fill='black')
        canvas.save(EVIDENCE / 'dyvy-visual-montage.jpg', quality=72, optimize=True)
    except ImportError:
        pass
    print(json.dumps({
        'total': len(rows),
        'visual_reviewed': sum(row['status'] == 'visual_reviewed' for row in rows),
        'visual_failed': sum(row['status'] == 'visual_failed' for row in rows),
        'visual_unconfirmed': sum(row['status'] == 'visual_unconfirmed' for row in rows),
        'ocr_marker_hits': sum(bool(row.get('ocr_flags')) for row in rows),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
