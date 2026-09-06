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

if __name__ == '__main__': unittest.main()
