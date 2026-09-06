import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import iptv_filter as m


class FilterTest(unittest.TestCase):
    def test_nonportable_streams_are_excluded(self):
        self.assertFalse(m.public_stream('http://127.0.0.1:6878/ace/getstream'))
        self.assertFalse(m.public_stream('http://192.168.1.2/live'))
        self.assertFalse(m.public_stream('http://localhost:8000/live'))
        self.assertFalse(m.public_stream('acestream://hash'))
        self.assertTrue(m.public_stream('https://example.com/live.m3u8'))

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
            {'id': 'New.ru', 'name':'New', 'country':'RU', 'categories':['movies']},
            {'id': 'PLUSPLUS.ua', 'name':'PLUSPLUS', 'country':'UA', 'categories':['kids']},
            {'id': 'NikiKids.ua', 'name':'Niki Kids', 'country':'UA', 'categories':['kids']},
        ]
        def entry(cid, url, extra=''):
            return f'#EXTINF:-1 tvg-id="{cid}" {extra},{cid}\nhttps://example.com/{url}\n'
        ukr = '#EXTM3U\n'+entry('Film.ua','film')+entry('PLUSPLUS.ua','plusplus')+entry('NikiKids.ua','niki')
        rus = '#EXTM3U\n'+entry('Toon.ru','toon')+entry('News.ru','news')
        combined = ukr+entry('Toon.ru','toon')+entry('Film.ua','film')+entry('English.ru','english','tvg-language="eng"')+entry('Unknown.ru','unknown')+entry('News.ru','news')+entry('Film.ua','foreign','tvg-language="eng"')+entry('Film.ua','video.mp4')+entry('New.ru@RU','new')+entry('New.ru@EN','english-feed')+entry('New.ru','cinema')+entry('Unknown.ru','cinema-alt')
        def fetch(url):
            if url.endswith('channels.json'): return json.dumps(channels)
            if url.endswith('feeds.json'): return json.dumps([{'channel':'New.ru','id':'RU','languages':['rus']},{'channel':'New.ru','id':'EN','languages':['eng']}])
            if url.endswith('ukr.m3u'): return ukr
            if url.endswith('rus.m3u'): return rus
            return combined
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'extra_channels.json').write_text(json.dumps({'cinema':{'name':'Cinema Test','languages':['rus'],'category':'movies','evidence':'test curated source','urls':['https://example.com/cinema','https://example.com/cinema-alt']}}))
            with patch.object(m,'EXTRA_PATH',root/'extra_channels.json'), patch.object(m,'ROOT',root), patch.object(m,'OUT',root/'my-iptv.m3u'), patch.object(m,'fetch',side_effect=fetch), patch.object(m,'SOURCES',[('test','https://example.com/input',None)]):
                m.main()
                result=m.parse_entries((root/'my-iptv.m3u').read_text())
                self.assertEqual({url for _,url in result},{'https://example.com/film','https://example.com/toon','https://example.com/new','https://example.com/plusplus','https://example.com/niki','https://example.com/cinema','https://example.com/cinema-alt'})
                cinema_ids={m.attr(a[0],'tvg-id') for a,u in result if '/cinema' in u}
                self.assertEqual(len(cinema_ids),1)
                self.assertTrue(next(iter(cinema_ids)).startswith('OnlineCinema.'))
                previous=(root/'my-iptv.m3u').read_bytes()
                with patch.object(m,'fetch',return_value='bad upstream'):
                    with self.assertRaises(ValueError): m.main()
                self.assertEqual((root/'my-iptv.m3u').read_bytes(),previous)

    def test_ukrainian_broadcasts_require_ua_source_category_and_language(self):
        channels = [
            {'id': 'Film.ua', 'name': 'Film UA', 'country': 'UA', 'categories': ['movies']},
            {'id': 'Toon.ua', 'name': 'Toon UA', 'country': 'UA', 'categories': ['animation']},
            {'id': 'General.ua', 'name': 'General UA', 'country': 'UA', 'categories': ['general']},
            {'id': 'Entertainment.ua', 'name': 'Entertainment UA', 'country': 'UA', 'categories': ['entertainment']},
            {'id': 'STB.ua', 'name': 'STB', 'country': 'UA', 'categories': []},
            {'id': 'TET.ua', 'name': 'TET', 'country': 'UA', 'categories': []},
            {'id': 'Mixed.ua', 'name': 'Mixed UA', 'country': 'UA', 'categories': ['general', 'sports']},
            {'id': 'RussianGeneral.ua', 'name': 'Russian General', 'country': 'UA', 'categories': ['general']},
            {'id': 'RussianCountry.ru', 'name': 'Russian Country', 'country': 'RU', 'categories': ['general']},
            {'id': 'Sports.ua', 'name': 'Sports UA', 'country': 'UA', 'categories': ['sports']},
            {'id': 'Foreign.ua', 'name': 'Foreign UA', 'country': 'UA', 'categories': ['general']},
        ]

        def entry(cid, url, extra=''):
            return f'#EXTINF:-1 tvg-id="{cid}@SD" {extra},{cid}\nhttps://example.com/{url}\n'

        ua = '#EXTM3U\n' + ''.join([
            entry('Film.ua', 'film'),
            entry('Toon.ua', 'toon'),
            entry('General.ua', 'general'),
            entry('Entertainment.ua', 'entertainment'),
            entry('STB.ua', 'stb'),
            entry('TET.ua', 'tet'),
            entry('Mixed.ua', 'mixed'),
            entry('RussianGeneral.ua', 'russian'),
            entry('RussianCountry.ru', 'russian-country'),
            entry('Sports.ua', 'sports'),
            entry('Foreign.ua', 'foreign', 'tvg-language="eng"'),
        ])
        ukr = '#EXTM3U\n' + entry('General.ua', 'general')
        rus = '#EXTM3U\n' + entry('RussianGeneral.ua', 'russian')
        feeds = [
            {'channel': 'Film.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'Toon.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'General.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'Entertainment.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'STB.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'TET.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'Mixed.ua', 'id': 'SD', 'languages': ['ukr', 'rus']},
            {'channel': 'RussianGeneral.ua', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'RussianCountry.ru', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'Sports.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'Foreign.ua', 'id': 'SD', 'languages': ['ukr']},
        ]

        def fetch(url):
            if url.endswith('channels.json'):
                return json.dumps(channels)
            if url.endswith('feeds.json'):
                return json.dumps(feeds)
            if url.endswith('/languages/ukr.m3u'):
                return ukr
            if url.endswith('/languages/rus.m3u'):
                return rus
            return ua

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(m, 'EXTRA_PATH', root/'extra_channels.json'), \
                 patch.object(m, 'ROOT', root), patch.object(m, 'OUT', root/'my-iptv.m3u'), \
                 patch.object(m, 'fetch', side_effect=fetch), \
                 patch.object(m, 'SOURCES', [('iptv-org/iptv', 'https://example.com/ua.m3u', 'UA')]):
                m.main()
                result = m.parse_entries((root/'my-iptv.m3u').read_text())

        by_url = {url: (meta, m.attr(meta[0], 'group-title')) for meta, url in result}
        self.assertIn('https://example.com/general', by_url)
        self.assertIn('https://example.com/entertainment', by_url)
        self.assertIn('https://example.com/stb', by_url)
        self.assertIn('https://example.com/tet', by_url)
        self.assertIn('https://example.com/mixed', by_url)
        self.assertNotIn('https://example.com/russian', by_url)
        self.assertNotIn('https://example.com/russian-country', by_url)
        self.assertNotIn('https://example.com/sports', by_url)
        self.assertNotIn('https://example.com/foreign', by_url)
        self.assertEqual(by_url['https://example.com/general'][1], 'Українське ТБ | UKR')
        self.assertEqual(by_url['https://example.com/entertainment'][1], 'Українське ТБ | UKR')
        self.assertEqual(by_url['https://example.com/stb'][1], 'Українське ТБ | UKR')
        self.assertEqual(by_url['https://example.com/tet'][1], 'Українське ТБ | UKR')
        self.assertEqual(by_url['https://example.com/mixed'][1], 'Українське ТБ | RUS/UKR')

if __name__ == '__main__': unittest.main()
