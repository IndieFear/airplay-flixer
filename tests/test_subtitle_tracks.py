from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from airplay_flixer.subtitle_tracks import with_subtitle_tracks

class ManifestTests(unittest.TestCase):
    def test_all_renditions_have_unique_names_one_default_and_keep_audio_and_native_tracks(self):
        master='#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="English",URI="audio.m3u8"\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="native",NAME="Original",LANGUAGE="en",DEFAULT=YES,URI="native.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=123,AUDIO="a",SUBTITLES="native"\nvideo.m3u8\n'
        tracks=[{'url':'http://relay/0.m3u8','language':'fr','label':'French'},{'url':'http://relay/1.m3u8','language':'fr','label':'French','default':True}]
        result=with_subtitle_tracks(master,tracks)
        subtitle_lines=[l for l in result.splitlines() if 'TYPE=SUBTITLES' in l]
        self.assertEqual(len(subtitle_lines),3)
        self.assertEqual(sum('DEFAULT=YES' in l for l in subtitle_lines),1)
        self.assertIn('NAME="French (2)"',result)
        self.assertIn('URI="native.m3u8"',result)
        self.assertIn('AUDIO="a",SUBTITLES="flixer-subs"',result)
        self.assertIn('TYPE=AUDIO,GROUP-ID="a",NAME="English",URI="audio.m3u8"',result)
        self.assertTrue(all('GROUP-ID="flixer-subs"' in l for l in subtitle_lines))

    def test_alternative_cache_is_bounded_and_keeps_initial_track(self):
        from unittest.mock import MagicMock,patch
        from airplay_flixer.media import MediaSession
        media=MediaSession(MagicMock(),{'url':'https://example.test/v.mp4'},'http://127.0.0.1:1234')
        with patch('airplay_flixer.media.MAX_SUBTITLE_CACHE',10):
            media.cache_subtitle(0,'first')
            media.cache_subtitle(1,'other')
            media.cache_subtitle(2,'third')
        self.assertEqual(set(media.vtt_tracks),{0,2})
        self.assertEqual(sum(media.subtitle_cache_sizes.values()),10)
