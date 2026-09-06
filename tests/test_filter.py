import json
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import iptv_filter as m
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'checks'))
import identity_audit as identity
import paid_gate_audit as paid


class FilterTest(unittest.TestCase):
    def test_dyvy_public_api_keeps_only_reviewed_portable_origins(self):
        direct = 'https://playout-stream.adt-playout.top/player/video/test/1/master.m3u8?subs=1'
        wrapped = 'https://777905.live.tvstitch.com/catchup/stream.m3u8?m=' + base64.urlsafe_b64encode(direct.encode()).decode()
        rows = [
            {'slug':'allowed', 'name':'Allowed', 'type':'fast', 'link':wrapped},
            {'slug':'gated', 'name':'Gated', 'type':'fast', 'package_block':{'name':'Authorized'}, 'link':wrapped},
            {'slug':'jwt', 'name':'IP bound', 'type':'live', 'link':'https://cdn.dyvyapp.com/x/video.m3u8?token=jwt'},
        ]
        reviewed = {'allowed': {'name':'Allowed','kind':'Пізнавальні'},
                    'gated': {'name':'Gated','kind':'Пізнавальні'},
                    'jwt': {'name':'IP bound','kind':'Українське ТБ'}}
        result = m.parse_entries(m.dyvy_playlist(json.dumps({'data':rows}), reviewed=reviewed))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], direct)
        self.assertEqual(m.attr(result[0][0][0], 'tvg-language'), 'ukr')

    def test_nonportable_streams_are_excluded(self):
        self.assertFalse(m.public_stream('http://127.0.0.1:6878/ace/getstream'))
        self.assertFalse(m.public_stream('http://192.168.1.2/live'))
        self.assertFalse(m.public_stream('http://localhost:8000/live'))
        self.assertFalse(m.public_stream('acestream://hash'))
        self.assertTrue(m.public_stream('https://example.com/live.m3u8'))

    def test_russian_movie_origin_filter_defaults_to_conservative_exclusion(self):
        policy = {
            'channels': {},
            'default_russian_movie_decision': 'deny_uncertain_or_mixed',
            'default_russian_online_cinema_decision': 'deny_uncertain_or_mixed',
            'default_origin_evidence': 'No foreign-only evidence in fixture',
        }
        review = m.origin_review('Unknown.ru', 'Фільми', {'rus'}, None, policy)
        self.assertEqual(review['decision'], 'deny_uncertain_or_mixed')
        self.assertIn('foreign-only', review['origin_evidence'])
        outside = m.origin_review('Unknown.ru', 'Серіали', {'rus'}, None, policy)
        self.assertEqual(outside['decision'], 'outside_russian_movie_scope')

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
            (root/'content_origin_policy.json').write_text(json.dumps({
                'version': 1,
                'channels': {'New.ru': {'decision': 'allow_test_foreign', 'origin_evidence': 'test fixture', 'evidence_urls': []}},
                'default_russian_movie_decision': 'deny_uncertain_or_mixed',
                'default_russian_online_cinema_decision': 'allow_test_online_cinema',
                'default_origin_evidence': 'test fixture default',
            }))
            with patch.object(m,'EXTRA_PATH',root/'extra_channels.json'), patch.object(m,'ROOT',root), patch.object(m,'OUT',root/'my-iptv.m3u'), patch.object(m,'ORIGIN_POLICY_PATH',root/'content_origin_policy.json'), patch.object(m,'fetch',side_effect=fetch), patch.object(m,'SOURCES',[('test','https://example.com/input',None)]):
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

    def test_educational_streams_require_topical_category_and_target_language(self):
        channels = [
            {'id': 'Film.ua', 'name': 'Film UA', 'country': 'UA', 'categories': ['movies']},
            {'id': 'Toon.ru', 'name': 'Toon', 'country': 'RU', 'categories': ['animation']},
            {'id': 'History.ru', 'name': 'History', 'country': 'RU', 'categories': ['documentary']},
            {'id': 'Science.ru', 'name': 'Science', 'country': 'RU', 'categories': ['science']},
            {'id': 'Series.ru', 'name': 'Series', 'country': 'RU', 'categories': ['series']},
            {'id': 'BigPlanet.ru', 'name': 'Big Planet', 'country': 'RU', 'categories': []},
            {'id': 'DiscoveryChannel.ru', 'name': 'Discovery Channel', 'country': 'RU', 'categories': ['entertainment']},
            {'id': 'SonyChannel.ru', 'name': 'Sony Channel', 'country': 'RU', 'categories': []},
            {'id': 'ParamountComedy.ru', 'name': 'Paramount Comedy', 'country': 'RU', 'categories': ['comedy']},
            {'id': 'scifi.ru', 'name': 'Sci-Fi', 'country': 'RU', 'categories': ['science']},
            {'id': 'Sports.ru', 'name': 'Sports', 'country': 'RU', 'categories': ['sports']},
            {'id': 'Foreign.ru', 'name': 'Foreign', 'country': 'RU', 'categories': ['documentary']},
        ]

        def entry(cid, url, extra=''):
            return f'#EXTINF:-1 tvg-id="{cid}@SD" {extra},{cid}\nhttps://example.com/{url}\n'

        source = '#EXTM3U\n' + ''.join([
            entry('Film.ua', 'film'), entry('Toon.ru', 'toon'),
            entry('History.ru', 'history'), entry('Science.ru', 'science'),
            entry('Series.ru', 'series'),
            entry('BigPlanet.ru', 'planet'), entry('scifi.ru', 'scifi'),
            entry('Sports.ru', 'sports'), entry('Foreign.ru', 'foreign', 'tvg-language="eng"'),
            '#EXTINF:-1 group-title="Popular",Discovery\nhttps://example.com/discovery\n',
            '#EXTINF:-1 group-title="Кино и сериалы",.RED\nhttps://example.com/sony\n',
            '#EXTINF:-1 group-title="Кино и сериалы",Paramount Comedy\nhttps://example.com/paramount\n',
        ])
        feeds = [
            {'channel': 'Film.ua', 'id': 'SD', 'languages': ['ukr']},
            {'channel': 'Toon.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'History.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'Science.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'Series.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'BigPlanet.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'DiscoveryChannel.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'SonyChannel.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'ParamountComedy.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'scifi.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'Sports.ru', 'id': 'SD', 'languages': ['rus']},
            {'channel': 'Foreign.ru', 'id': 'SD', 'languages': ['rus']},
        ]

        def fetch(url):
            if url.endswith('channels.json'):
                return json.dumps(channels)
            if url.endswith('feeds.json'):
                return json.dumps(feeds)
            if url.endswith('/languages/ukr.m3u'):
                return '#EXTM3U\n' + entry('Film.ua', 'film')
            if url.endswith('/languages/rus.m3u'):
                return '#EXTM3U\n' + entry('Toon.ru', 'toon')
            return source

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(m, 'EXTRA_PATH', root/'extra_channels.json'), \
                 patch.object(m, 'ROOT', root), patch.object(m, 'OUT', root/'my-iptv.m3u'), \
                 patch.object(m, 'fetch', side_effect=fetch), \
                 patch.object(m, 'SOURCES', [('Dimonovich/TV', 'https://example.com/source.m3u', None)]):
                m.main()
                result = m.parse_entries((root/'my-iptv.m3u').read_text())

        by_url = {url: m.attr(meta[0], 'group-title') for meta, url in result}
        self.assertEqual(by_url['https://example.com/history'], 'Пізнавальні | RUS')
        self.assertEqual(by_url['https://example.com/science'], 'Пізнавальні | RUS')
        self.assertEqual(by_url['https://example.com/series'], 'Серіали | RUS')
        self.assertEqual(by_url['https://example.com/planet'], 'Пізнавальні | RUS')
        self.assertEqual(by_url['https://example.com/discovery'], 'Пізнавальні | RUS')
        self.assertEqual(by_url['https://example.com/sony'], 'Серіали | RUS')
        self.assertEqual(by_url['https://example.com/paramount'], 'Серіали | RUS')
        self.assertNotIn('https://example.com/scifi', by_url)
        self.assertNotIn('https://example.com/sports', by_url)
        self.assertNotIn('https://example.com/foreign', by_url)

    def test_identity_audit_rejects_known_promo_and_entitlement_sources(self):
        def row(cid):
            return {
                'status': 'working', 'channel_id': cid, 'name': cid,
                'url': '', 'group': 'Пізнавальні | RUS',
            }

        promo_meta = ['#EXTINF:-1 tvg-id="DiscoveryChannel.ru",Discovery']
        promo = identity.review(row('DiscoveryChannel.ru'),
                                (promo_meta, 'https://stream8.cinerama.uz/1039/index.m3u8'))
        self.assertEqual(promo['status'], 'identity_failed')
        self.assertEqual(promo['identity_status'], 'rejected_wrong_content')

        entitlement_meta = ['#EXTINF:-1 tvg-id="ViasatExplore.ua",Viasat Explore']
        entitlement = identity.review(row('ViasatExplore.ua'),
                                      (entitlement_meta, 'http://777905.live.tvstitch.com/playlist.m3u8?token=test'))
        self.assertEqual(entitlement['status'], 'identity_failed')
        self.assertIn('tariff', entitlement['identity_reason'])

    def test_paid_gate_audit_detects_visible_subscription_and_http_failures(self):
        flags = paid._text_flags('Вам необходимо оплатить подписку. You need to pay for a subscription.')
        self.assertIn('pay for a subscription', flags)
        row = {'status': 'working', 'reason': 'decoded'}
        self.assertEqual(
            paid._classification(row, {'http_status': 200, 'manifest_flags': []},
                                 {'ocr_flags': ['pay for a subscription']})[0],
            'entitlement_or_paywall')
        self.assertEqual(
            paid._classification(row, {'http_status': 503, 'manifest_flags': []}, {})[0],
            'inaccessible_http')
        self.assertEqual(
            paid._classification(row, {'http_status': 0, 'manifest_flags': []}, {})[0],
            'inaccessible_network')

if __name__ == '__main__': unittest.main()
