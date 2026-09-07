#!/usr/bin/env python3
"""Build a small XMLTV guide for the checked-in IPTV playlist.

The playlist carries feed-qualified ids such as ``Nickelodeon.ru@SD``.  XMLTV
uses the channel id without the feed qualifier, so this builder strips only
the final ``@feed`` part and then requires an exact id match in the public
source guides.  It deliberately does not guess by channel name.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAYLIST = ROOT / "my-iptv.m3u"
DEFAULT_OUTPUT = ROOT / "epg" / "my-iptv.xml.gz"
DEFAULT_README = ROOT / "epg" / "README.md"
USER_AGENT = "vibe-master-ai/my-iptv-epg/1.0"
SOURCES = (
    ("Ukraine", "https://iptv-epg.org/files/epg-ua.xml.gz"),
    ("Russia", "https://iptv-epg.org/files/epg-ru.xml.gz"),
)


def base_channel_id(value: str) -> str:
    """Remove only the playlist feed qualifier (for example ``@HD``)."""

    return value.split("@", 1)[0].strip()


def playlist_channel_ids(text: str) -> set[str]:
    """Return non-empty base tvg-ids from an M3U playlist."""

    ids: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("#EXTINF"):
            continue
        match = re.search(r'(?:^|\s)tvg-id="([^"]*)"', line, re.I)
        if match and base_channel_id(match.group(1)):
            ids.add(base_channel_id(match.group(1)))
    if not ids:
        raise ValueError("playlist has no tvg-id values")
    return ids


def read_source(url: str, timeout: int = 60) -> tuple[bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        headers = {
            "last-modified": response.headers.get("Last-Modified", ""),
            "content-type": response.headers.get("Content-Type", ""),
            "content-length": response.headers.get("Content-Length", ""),
        }
        return response.read(), headers


def parse_xml(data: bytes, url: str) -> ET.Element:
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"invalid XMLTV from {url}: {exc}") from exc
    if root.tag != "tv":
        raise ValueError(f"{url} is not an XMLTV <tv> document")
    return root


def text_of(element: ET.Element | None) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def programme_key(programme: ET.Element) -> tuple[str, str, str, str, str]:
    """Stable event identity, tolerant of harmless metadata differences."""

    def child_text(tag: str) -> str:
        return text_of(programme.find(tag))

    return (
        programme.attrib.get("channel", ""),
        programme.attrib.get("start", ""),
        programme.attrib.get("stop", ""),
        child_text("title"),
        child_text("sub-title"),
    )


def event_sort_key(programme: ET.Element) -> tuple[str, str, str, str]:
    return (
        programme.attrib.get("channel", ""),
        programme.attrib.get("start", ""),
        programme.attrib.get("stop", ""),
        text_of(programme.find("title")),
    )


def format_source_meta(name: str, url: str, headers: dict[str, str], root: ET.Element) -> dict:
    programmes = root.findall("programme")
    channels = root.findall("channel")
    starts = [p.attrib.get("start", "") for p in programmes if p.attrib.get("start")]
    stops = [p.attrib.get("stop", "") for p in programmes if p.attrib.get("stop")]
    return {
        "name": name,
        "url": url,
        "last_modified": headers.get("last-modified", ""),
        "content_type": headers.get("content-type", ""),
        "content_length": headers.get("content-length", ""),
        "channels": len(channels),
        "programmes": len(programmes),
        "programme_start": min(starts) if starts else "",
        "programme_stop": max(stops) if stops else "",
    }


def build_document(playlist_ids: set[str], roots: list[ET.Element]) -> tuple[ET.Element, dict]:
    """Select exact-id channel/programme data from the source XMLTV roots."""

    channels: dict[str, ET.Element] = {}
    programmes: dict[tuple[str, str, str, str, str], ET.Element] = {}
    source_channel_ids: set[str] = set()

    for root in roots:
        for channel in root.findall("channel"):
            channel_id = channel.attrib.get("id", "").strip()
            if channel_id in playlist_ids:
                source_channel_ids.add(channel_id)
                channels.setdefault(channel_id, channel)
        for programme in root.findall("programme"):
            channel_id = programme.attrib.get("channel", "").strip()
            if channel_id not in playlist_ids:
                continue
            key = programme_key(programme)
            programmes.setdefault(key, programme)

    # A channel declaration without any programme is not useful to OwnTV and
    # would overstate coverage, so publish only channels with real events.
    programme_channel_ids = {key[0] for key in programmes}
    selected_channels = [channels[channel_id] for channel_id in sorted(programme_channel_ids)]
    selected_programmes = sorted(programmes.values(), key=event_sort_key)

    output = ET.Element(
        "tv",
        {
            "generator-info-name": "vibe-master-ai/my-iptv XMLTV builder",
            "generator-info-url": "https://github.com/vibe-master-ai/my-iptv",
        },
    )
    for channel in selected_channels:
        output.append(ET.fromstring(ET.tostring(channel, encoding="utf-8")))
    for programme in selected_programmes:
        output.append(ET.fromstring(ET.tostring(programme, encoding="utf-8")))

    stats = {
        "playlist_ids": len(playlist_ids),
        "matched_source_channel_ids": len(source_channel_ids),
        "mapped_channel_ids": len(selected_channels),
        "coverage_percent": round(100 * len(selected_channels) / len(playlist_ids), 1),
        "programme_events": len(selected_programmes),
        "duplicate_programmes_removed": sum(1 for root in roots for p in root.findall("programme") if p.attrib.get("channel") in playlist_ids) - len(selected_programmes),
        "source_channel_ids_without_programmes": len(source_channel_ids - programme_channel_ids),
    }
    return output, stats


def xml_bytes(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_gzip(root: ET.Element, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb", compresslevel=9) as handle:
        handle.write(xml_bytes(root))


def make_readme(
    generated_at: str,
    playlist_path: Path,
    stats: dict,
    source_meta: list[dict],
    output_path: Path,
) -> str:
    source_lines = []
    for source in source_meta:
        source_lines.append(
            f"- **{source['name']}**: `{source['url']}`; HTTP `200`, "
            f"`{source['content_type'] or 'content type not reported'}`, "
            f"Last-Modified `{source['last_modified'] or 'not reported'}`; "
            f"{source['channels']} source channels / {source['programmes']} programmes "
            f"({source['programme_start']} → {source['programme_stop']})."
        )
    return f"""# OwnTV EPG

Generated **{generated_at}** from the checked-in playlist `{playlist_path.name}`.
The public OwnTV XMLTV source is:

`https://raw.githubusercontent.com/vibe-master-ai/my-iptv/main/{output_path.relative_to(ROOT).as_posix()}`

## Mapping

- Playlist channel identities after removing only the `@feed` qualifier: **{stats['playlist_ids']}**.
- Exact XMLTV channel IDs retained: **{stats['mapped_channel_ids']}** ({stats['coverage_percent']}%).
- Programme events retained: **{stats['programme_events']}**.
- Duplicate programme keys removed across source feeds: **{stats['duplicate_programmes_removed']}**.
- Source channel IDs with no programme were omitted: **{stats['source_channel_ids_without_programmes']}**.

Mapping is exact by `tvg-id` base ID. The builder does not map by a similar name, country, or language guess. The remaining playlist channels therefore have no promise of EPG data.

## Sources at build time

{chr(10).join(source_lines)}

The feeds are public country XMLTV feeds from [IPTV-EPG.org](https://iptv-epg.org/guides). Their schedule horizon and channel contents can change. XMLTV programme times retain the source timestamps and timezone offsets.

## OwnTV

Open **Settings → EPG Sources → Add**, enter the public URL above, save, and synchronize. OwnTV matches the exact `tvg-id` values automatically. Manual matching can be used for channels without an exact mapping, but this repository does not fabricate those mappings.

EPG supplies programme metadata only. It does not add catch-up, rewind, archive storage, or access to a stream. Catch-up remains dependent on the stream's own `catchup` attributes and server-side archive.

This feed is generated by [`build_epg.py`](build_epg.py) and checked by the tests. A failed or invalid upstream input stops the workflow before publishing, preserving the last published EPG file.
"""


def build(playlist: Path, output: Path, readme: Path) -> dict:
    playlist_ids = playlist_channel_ids(playlist.read_text(encoding="utf-8", errors="replace"))
    roots: list[ET.Element] = []
    source_meta: list[dict] = []
    for name, url in SOURCES:
        data, headers = read_source(url)
        root = parse_xml(data, url)
        roots.append(root)
        source_meta.append(format_source_meta(name, url, headers, root))
    document, stats = build_document(playlist_ids, roots)
    if stats["mapped_channel_ids"] == 0 or stats["programme_events"] == 0:
        raise ValueError("sources produced no exact playlist mappings")
    write_gzip(document, output)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    readme.write_text(make_readme(generated_at, playlist, stats, source_meta, output), encoding="utf-8")
    report = {
        "generated_at": generated_at,
        "playlist": str(playlist.relative_to(ROOT)),
        "output": str(output.relative_to(ROOT)),
        "sources": source_meta,
        "stats": stats,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    (output.parent / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playlist", type=Path, default=DEFAULT_PLAYLIST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--readme", type=Path, default=DEFAULT_README)
    args = parser.parse_args(argv)
    try:
        report = build(args.playlist, args.output, args.readme)
    except Exception as exc:  # workflow must fail before replacing a good feed
        print(f"EPG build failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
