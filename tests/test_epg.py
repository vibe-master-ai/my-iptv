import gzip
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from epg.build_epg import build_document, playlist_channel_ids, xml_bytes


class EpgTest(unittest.TestCase):
    def test_published_feed_uses_only_playlist_channel_ids(self):
        root = Path(__file__).resolve().parents[1]
        playlist_ids = playlist_channel_ids((root / "my-iptv.m3u").read_text(encoding="utf-8"))
        with gzip.open(root / "epg" / "my-iptv.xml.gz", "rb") as handle:
            document = ET.parse(handle).getroot()
        self.assertEqual(document.tag, "tv")
        channel_ids = {channel.attrib["id"] for channel in document.findall("channel")}
        programme_ids = {programme.attrib["channel"] for programme in document.findall("programme")}
        self.assertTrue(channel_ids)
        self.assertTrue(channel_ids <= playlist_ids)
        self.assertTrue(programme_ids <= playlist_ids)
        self.assertEqual(channel_ids, programme_ids)

    def test_playlist_ids_strip_only_feed_qualifier(self):
        playlist = """#EXTM3U
#EXTINF:-1 tvg-id="Demo.ru@HD",Demo
https://example.test/demo
#EXTINF:-1 tvg-id="Demo.ru@SD",Demo SD
https://example.test/demo-sd
#EXTINF:-1 tvg-id="Other.ua",Other
https://example.test/other
"""
        self.assertEqual(playlist_channel_ids(playlist), {"Demo.ru", "Other.ua"})

    def test_build_keeps_exact_ids_and_deduplicates_events(self):
        first = ET.fromstring(
            """<tv>
              <channel id="Demo.ru"><display-name>Demo</display-name></channel>
              <channel id="NoSchedule.ru"><display-name>No schedule</display-name></channel>
              <programme channel="Demo.ru" start="20260907000000 +0000" stop="20260907010000 +0000">
                <title lang="rus">Morning</title><category lang="rus">Show</category>
              </programme>
            </tv>"""
        )
        second = ET.fromstring(
            """<tv>
              <channel id="Demo.ru"><display-name>Demo duplicate</display-name></channel>
              <channel id="Other.ua"><display-name>Other</display-name></channel>
              <programme channel="Demo.ru" start="20260907000000 +0000" stop="20260907010000 +0000">
                <title lang="rus">Morning</title><category lang="rus">Show</category>
              </programme>
              <programme channel="Other.ua" start="20260907010000 +0000" stop="20260907020000 +0000">
                <title lang="ukr">News</title>
              </programme>
              <programme channel="NotInPlaylist.ru" start="20260907000000 +0000" stop="20260907010000 +0000">
                <title>Ignored</title>
              </programme>
            </tv>"""
        )
        document, stats = build_document({"Demo.ru", "Other.ua"}, [first, second])
        self.assertEqual([c.attrib["id"] for c in document.findall("channel")], ["Demo.ru", "Other.ua"])
        programmes = document.findall("programme")
        self.assertEqual(len(programmes), 2)
        self.assertEqual(stats["mapped_channel_ids"], 2)
        self.assertEqual(stats["duplicate_programmes_removed"], 1)
        self.assertNotIn("NotInPlaylist.ru", {p.attrib["channel"] for p in programmes})
        parsed = ET.fromstring(gzip.decompress(gzip.compress(xml_bytes(document))))
        self.assertEqual(len(parsed.findall("programme")), 2)


if __name__ == "__main__":
    unittest.main()
