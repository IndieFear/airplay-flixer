import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
from airplay_flixer.now_playing import with_title
from airplay_flixer.airplay_compat import play_url
from pyatv.protocols.raop.protocols import airplayv2


class PlaylistTitleTests(unittest.TestCase):
    def test_title_is_safe_unique_and_other_metadata_preserved(self):
        playlist = '#EXTM3U\n#EXT-X-VERSION:9\n#EXT-X-SESSION-DATA:DATA-ID="com.apple.hls.title",VALUE="old"\n#EXT-X-SESSION-DATA:DATA-ID="other",VALUE="keep"\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo.m3u8\n'
        result = with_title(playlist, 'Série "été"\n#EXT-X-ENDLIST\0')
        self.assertEqual(result.count('DATA-ID="com.apple.hls.title"'), 1)
        self.assertIn('VALUE="Série ”été” #EXT-X-ENDLIST"', result)
        self.assertIn('#EXT-X-VERSION:9', result)
        self.assertIn('DATA-ID="other",VALUE="keep"', result)
        self.assertTrue(result.endswith('video.m3u8\n'))
        self.assertEqual(with_title(playlist, ''), playlist)


class AirPlayTitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_sent_once_and_rejection_does_not_stop_playback(self):
        original = airplayv2.AirPlayV2
        for reject in (False, True):
            rtsp = MagicMock()
            rtsp.set_metadata = AsyncMock(side_effect=RuntimeError('Unsupported') if reject else None, return_value=SimpleNamespace(code=200))
            context = SimpleNamespace(rtsp_session=1, rtpseq=2, rtptime=3)
            async def send(_, command):
                return SimpleNamespace(code=200)
            async def stream(*args, **kwargs):
                protocol = airplayv2.AirPlayV2(context, rtsp)
                for _ in range(2):
                    response = await protocol._send_url_command({'type':'setRate','rate':1})
                    self.assertEqual(response.code, 200)
            tv = MagicMock()
            tv.stream.play_url = stream
            with patch.object(original, '_send_url_command', send):
                await play_url(tv, 'http://example.test/video', title='Série · S02E01')
            self.assertEqual(rtsp.set_metadata.await_count, 1)
            self.assertEqual(rtsp.set_metadata.call_args.args[-1].title, 'Série · S02E01')
            self.assertIs(airplayv2.AirPlayV2, original)

class ArtworkTests(unittest.TestCase):
    def test_rejects_invalid_and_oversized_payloads(self):
        import base64
        from airplay_flixer.now_playing import decode_artwork
        jpeg = b'\xff\xd8fixture\xff\xd9'
        self.assertEqual(decode_artwork(base64.b64encode(jpeg).decode()), jpeg)
        for invalid in (None, {}, 'invalid!', base64.b64encode(b'not jpeg').decode(), 'A'*240001):
            self.assertIsNone(decode_artwork(invalid))

    def test_poster_uses_local_session_and_preserves_other_tags(self):
        playlist = '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-SESSION-DATA:DATA-ID="com.apple.hls.poster",VALUE="https://old.test/poster"\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo.m3u8\n'
        result = with_title(playlist, 'Un film', 'http://127.0.0.1/media/session/artwork.jpg')
        self.assertEqual(result.count('DATA-ID="com.apple.hls.poster"'), 1)
        self.assertIn('VALUE="http://127.0.0.1/media/session/artwork.jpg"', result)
        self.assertIn('#EXT-X-VERSION:7', result)
        self.assertTrue(result.endswith('video.m3u8\n'))


class ArtworkTransmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_artwork_refusal_does_not_interrupt_playback_or_resend_on_resume(self):
        original = airplayv2.AirPlayV2
        for reject in (False, True):
            rtsp = MagicMock()
            rtsp.set_artwork = AsyncMock(side_effect=RuntimeError('Unsupported') if reject else None, return_value=SimpleNamespace(code=200))
            context = SimpleNamespace(rtsp_session=1, rtpseq=2, rtptime=3)
            async def send(_, command):
                return SimpleNamespace(code=200)
            async def stream(*args, **kwargs):
                protocol = airplayv2.AirPlayV2(context, rtsp)
                for _ in range(2):
                    response = await protocol._send_url_command({'type':'setRate','rate':1})
                    self.assertEqual(response.code, 200)
            tv = MagicMock()
            tv.stream.play_url = stream
            with patch.object(original, '_send_url_command', send):
                await play_url(tv, 'http://example.test/video', artwork=b'jpeg fixture')
            rtsp.set_artwork.assert_awaited_once_with(1,2,3,b'jpeg fixture')
            self.assertIs(airplayv2.AirPlayV2, original)

class ArtworkRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_master_points_to_session_local_jpeg(self):
        import base64
        from airplay_flixer.media import MediaSession
        jpeg = b'\xff\xd8fixture\xff\xd9'
        media = MediaSession(None, {'url':'https://source.test/master.m3u8','kind':'hls','title':'Film','artwork':base64.b64encode(jpeg).decode()}, 'http://192.168.1.2:9999')
        media.fetch_text = AsyncMock(return_value=('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo.m3u8\n','https://source.test/master.m3u8'))
        playlist = await media.master()
        self.assertIn(f'VALUE="{media.base}/artwork.jpg"', playlist)
        request = MagicMock(match_info={'tail':'artwork.jpg'})
        response = await media.serve(request)
        self.assertEqual(response.body, jpeg)
        self.assertEqual(response.content_type, 'image/jpeg')
