#!/usr/bin/env python3
# Builds a filtered M3U from the public iptv-org playlists.
# Keeps only Movies / Animation / Kids channels that are
# Ukrainian, Russian or English (with UA/RU country fallback).

from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from pathlib import Path
import re
import sys
import json
import time
from datetime import datetime, timezone

CATEGORY_SOURCES = {
    "🎬 Movies": "https://iptv-org.github.io/iptv/categories/movies.m3u",
    "🧸 Animation": "https://iptv-org.github.io/iptv/categories/animation.m3u",
    "👶 Kids": "https://iptv-org.github.io/iptv/categories/kids.m3u",
}

LANGUAGE_SOURCES = {
    "Ukrainian": "https://iptv-org.github.io/iptv/languages/ukr.m3u",
    "Russian": "https://iptv-org.github.io/iptv/languages/rus.m3u",
    "English": "https://iptv-org.github.io/iptv/languages/eng.m3u",
}

TARGET_LANGS = {"ukrainian", "russian", "english", "ukr", "rus", "eng"}
# Fallback so Ukrainian/Russian channels without clean language metadata are not lost.
TARGET_COUNTRIES = {"UA", "RU"}

OUT = Path(__file__).with_name("my-iptv.m3u")


def fetch(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 iptv-filter/1.0",
            "Accept": "*/*",
        },
    )
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="strict")
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)



def parse_entries(text: str):
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    entries = []
    pending = []
    for line in lines:
        if not line or line == "#EXTM3U":
            continue
        if line.startswith("#EXTINF:"):
            pending = [line]
        elif pending and line.startswith("#"):
            pending.append(line)
        elif pending:
            # stream URL following EXTINF (+ optional #EXTVLCOPT etc.)
            entries.append((pending[:], line))
            pending = []
    return entries


def attr(extinf: str, name: str) -> str:
    m = re.search(rf'{re.escape(name)}="([^"]*)"', extinf, flags=re.I)
    return m.group(1).strip() if m else ""


def set_group(extinf: str, group: str) -> str:
    if re.search(r'group-title="[^"]*"', extinf, flags=re.I):
        return re.sub(r'group-title="[^"]*"', f'group-title="{group}"', extinf, flags=re.I)
    comma = extinf.find(",")
    if comma == -1:
        return extinf + f' group-title="{group}"'
    return extinf[:comma] + f' group-title="{group}"' + extinf[comma:]


def split_meta(value: str):
    return {x.strip() for x in re.split(r"[;,/|]", value) if x.strip()}


def main():
    channels = json.loads(fetch("https://iptv-org.github.io/api/channels.json"))
    if not isinstance(channels, list) or not channels:
        raise ValueError("Invalid channel metadata; keeping previous playlist")
    country_ids = {c["id"] for c in channels if c.get("country") in TARGET_COUNTRIES}
    print("Fetching language indexes…")
    allowed_ids = set()
    allowed_urls = set()

    for lang, url in LANGUAGE_SOURCES.items():
        text = fetch(url)
        if not text.lstrip().startswith("#EXTM3U") or not parse_entries(text):
            raise ValueError(f"Invalid or empty source: {url}")
        for meta_lines, stream_url in parse_entries(text):
            extinf = meta_lines[0]
            tvg_id = attr(extinf, "tvg-id")
            if tvg_id:
                allowed_ids.add(tvg_id)
            allowed_urls.add(stream_url)
        print(f"  ✓ {lang}")

    output = ["#EXTM3U"]
    seen = set()
    counts = {}

    print("Fetching category playlists…")
    for group, url in CATEGORY_SOURCES.items():
        text = fetch(url)
        if not text.lstrip().startswith("#EXTM3U") or not parse_entries(text):
            raise ValueError(f"Invalid or empty source: {url}")
        count = 0

        for meta_lines, stream_url in parse_entries(text):
            extinf = meta_lines[0]
            tvg_id = attr(extinf, "tvg-id")
            lang_raw = attr(extinf, "tvg-language")
            country_raw = attr(extinf, "tvg-country")

            langs = {x.lower() for x in split_meta(lang_raw)}
            countries = {x.upper() for x in split_meta(country_raw)}

            language_match = bool(langs & TARGET_LANGS)
            language_playlist_match = bool(
                stream_url in allowed_urls or (tvg_id and tvg_id in allowed_ids)
            )
            country_fallback = bool(countries & TARGET_COUNTRIES) or tvg_id.split("@")[0] in country_ids

            if not (language_match or language_playlist_match or country_fallback):
                continue

            # Keep alternative streams but never repeat the same stream URL.
            key = stream_url
            if key in seen:
                continue
            seen.add(key)

            meta_lines[0] = set_group(extinf, group)
            output.extend(meta_lines)
            output.append(stream_url)
            count += 1

        counts[group] = count
        print(f"  ✓ {group}: {count}")

    if not counts or any(count == 0 for count in counts.values()):
        raise ValueError("An output category is empty; keeping previous playlist")
    previous_count = OUT.read_text().count("#EXTINF:") if OUT.exists() else 0
    if sum(counts.values()) < previous_count * 0.5:
        raise ValueError("Playlist unexpectedly lost over half its streams; keeping previous playlist")
    temporary = OUT.with_suffix(".tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    temporary.replace(OUT)
    OUT.with_name("status.json").write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "streams": sum(counts.values()), "categories": counts,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total = sum(counts.values())
    print()
    print(f"Done: {OUT}")
    print(f"Total channels: {total}")
    for group, count in counts.items():
        print(f"  {group}: {count}")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)
