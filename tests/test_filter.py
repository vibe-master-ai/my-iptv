import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import iptv_filter as m


class FilterTest(unittest.TestCase):
    def test_quoted_comma_and_stream_options(self):
        line = '#EXTINF:-1 tvg-name="Film, One",Film, One'
        self.assertEqual(m.split_extinf(line)[1], 'Film, One')
        changed = m.set_attr(line, 'group-title', 'Фільми')
        self.assertIn('tvg-name="Film, One"', changed)
        parsed = m.parse_entries('#EXTM3U\n'+changed+'\n#EXTVLCOPT:http-referrer=https://example.com\nhttps://example.com/live\n')
        self.assertEqual(len(parsed[0][0]), 2)

    def test_language_category_and_duplicates_end_to_end(self):
        channels = [
            {'id': 'Film.ua', 'name':'Film', 'country':'UA', 'categories':['movies']},
            {'id': 'Toon.ru', 'name':'Toon', 'country':'RU', 'categories':['animation']},
            {'id': 'English.ru', 'name':'English', 'country':'RU', 'categories':['movies']},
            {'id': 'News.ru', 'name':'News', 'country':'RU', 'categories':['news']},
            {'id': 'Unknown.ru', 'name':'Unknown', 'country':'RU', 'categories':['movies']},
        ]
        def entry(cid, url, extra=''):
            return f'#EXTINF:-1 tvg-id="{cid}" {extra},{cid}\nhttps://example.com/{url}\n'
        ukr = '#EXTM3U\n'+entry('Film.ua','film')
        rus = '#EXTM3U\n'+entry('Toon.ru','toon')+entry('News.ru','news')
        combined = ukr+entry('Toon.ru','toon')+entry('Film.ua','film')+entry('English.ru','english','tvg-language="eng"')+entry('Unknown.ru','unknown')+entry('News.ru','news')+entry('Film.ua','foreign','tvg-language="eng"')+entry('Film.ua','video.mp4')
        def fetch(url):
            if url.endswith('channels.json'): return json.dumps(channels)
            if url.endswith('ukr.m3u'): return ukr
            if url.endswith('rus.m3u'): return rus
            return combined
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(m,'ROOT',root), patch.object(m,'OUT',root/'my-iptv.m3u'), patch.object(m,'fetch',side_effect=fetch), patch.object(m,'SOURCES',[('test','https://example.com/input',None)]):
                m.main()
                result=m.parse_entries((root/'my-iptv.m3u').read_text())
                self.assertEqual({url for _,url in result},{'https://example.com/film','https://example.com/toon'})
                previous=(root/'my-iptv.m3u').read_bytes()
                with patch.object(m,'fetch',return_value='bad upstream'):
                    with self.assertRaises(ValueError): m.main()
                self.assertEqual((root/'my-iptv.m3u').read_bytes(),previous)

if __name__ == '__main__': unittest.main()
