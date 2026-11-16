import asyncio
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pyatv.const import PairingRequirement

from airplay_flixer.config import origin, web_url
from airplay_flixer.media import MediaSession, binary, to_webvtt, with_subtitles
from airplay_flixer.native_host import MAX_MESSAGE, read_message, write_message
from airplay_flixer.server import Companion


class ProtocolTests(unittest.TestCase):
    def test_actual_pyatv_srp_pairing_primitives_are_compatible(self):
        from pyatv.auth.hap_srp import SRPAuthHandler
        auth = SRPAuthHandler()
        auth.initialize()
        auth.step1('1234')
        public, proof = auth.step2(bytes([3]) * 384, bytes([2]) * 16)
        self.assertEqual(len(public), 384)
        self.assertEqual(len(proof), 64)

    def test_native_message_utf8_roundtrip_and_eof(self):
        output = io.BytesIO()
        write_message(output, {'action': 'cast', 'title': 'Episode français'})
        output.seek(0)
        self.assertEqual(read_message(output)['title'], 'Episode français')
        self.assertIsNone(read_message(output))

    def test_truncated_and_oversized_native_messages(self):
        for data in (b'\x01', struct.pack('<I', 5) + b'{}', struct.pack('<I', MAX_MESSAGE + 1)):
            with self.assertRaises(ValueError):
                read_message(io.BytesIO(data))

    def test_only_web_urls_and_no_embedded_credentials(self):
        for value in (None, 'blob:https://site/abc', 'file:///etc/passwd', 'https://user:pass@site/x', 'ftp://site/a'):
            with self.assertRaises(ValueError):
                web_url(value)
        self.assertEqual(origin('https://VIDEO.example:443/a?token=secret'), 'https://video.example')

    def test_srt_and_webvtt_timestamp_mapping(self):
        vtt = to_webvtt('\ufeff1\r\n00:00:01,250 --> 00:00:03,000\r\nBonjour\r\n', 126000)
        self.assertIn('00:00:01.250 --> 00:00:03.000', vtt)
        self.assertIn('MPEGTS:126000', vtt)
        self.assertEqual(to_webvtt(vtt, 500), vtt)
        with self.assertRaises(ValueError):
            to_webvtt('<html>Access denied</html>', 0)

    def test_subtitle_groups_and_off_preserve_audio(self):
        master = '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="audio.m3u8"\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="old",URI="old.m3u8"\n#EXT-X-STREAM-INF:SUBTITLES="old",BANDWIDTH=100,AUDIO="a"\nvideo.m3u8\n'
        wrapped = with_subtitles(master, 'http://relay/subs.m3u8', 'French', 'fr')
        self.assertEqual(wrapped.count('TYPE=SUBTITLES'), 1)
        self.assertIn('TYPE=AUDIO', wrapped)
        self.assertIn('SUBTITLES="flixer-subs"', wrapped)
        self.assertNotIn(':,', wrapped)
        off = with_subtitles(master, None, off=True)
        self.assertNotIn('SUBTITLES', off)
        self.assertIn('TYPE=AUDIO', off)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []
        self.current = None
        self.fixtures = tempfile.TemporaryDirectory()
        self.folder = Path(self.fixtures.name)
        source = web.Application()
        source.router.add_get('/master.m3u8', self.master)
        source.router.add_get('/variant/video.m3u8', self.playlist)
        source.router.add_get('/subs.srt', self.subtitles)
        source.router.add_get('/segment.ts', self.segment)
        source.router.add_get('/movie.mp4', self.movie)
        source.router.add_get('/redirect', self.redirect)
        source.router.add_get('/image-playlist.m3u8', self.image_playlist)
        source.router.add_get('/image-segment.jpg', self.image_segment)
        source.router.add_get('/chunked.m3u8', self.chunked_playlist)
        source.router.add_static('/real/', self.folder)
        self.source = TestServer(source)
        await self.source.start_server()
        relay = web.Application()
        relay.router.add_get('/media/{session}/{tail:.*}', self.relay)
        self.relay_server = TestServer(relay)
        await self.relay_server.start_server()
        self.client = aiohttp.ClientSession()

    async def asyncTearDown(self):
        if self.current:
            await self.current.close()
        await self.client.close()
        await self.relay_server.close()
        await self.source.close()
        self.fixtures.cleanup()

    async def master(self, request):
        return web.Response(text='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000\nvariant/video.m3u8\n', content_type='application/vnd.apple.mpegurl')

    async def subtitles(self, request):
        return web.Response(text='1\n00:00:00,000 --> 00:00:02,000\nBonjour\n')

    async def image_playlist(self, request):
        return web.Response(text='#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nimage-segment.jpg\n#EXT-X-ENDLIST\n', content_type='application/vnd.apple.mpegurl')

    async def image_segment(self, request):
        return web.Response(body=b'video-bytes', content_type='image/jpeg')

    async def chunked_playlist(self, request):
        response = web.StreamResponse(headers={'Content-Type': 'application/vnd.apple.mpegurl'})
        await response.prepare(request)
        await response.write(b'#EXTM3U\n')
        await asyncio.sleep(0.02)
        await response.write(b'#EXT-X-TARGETDURATION:3\n#EXTINF:3,\nsegment.ts\n#EXT-X-ENDLIST\n')
        await response.write_eof()
        return response

    async def test_media_disguised_as_images_gets_video_mime_and_extension(self):
        media, base = self.new_session(url=str(self.source.make_url('/image-playlist.m3u8')))
        text, actual = await media.fetch_text(media.source)
        rewritten = media.rewrite(text, actual)
        segment = next(line for line in rewritten.splitlines() if line.startswith('http'))
        self.assertTrue(segment.endswith('.ts'))
        async with self.client.get(segment) as response:
            self.assertEqual(response.headers['Content-Type'], 'video/mp2t')
            self.assertEqual(await response.read(), b'video-bytes')

    async def test_chunked_manifests_are_not_truncated(self):
        media, _ = self.new_session(url=str(self.source.make_url('/chunked.m3u8')))
        text, _ = await media.fetch_text(media.source)
        self.assertIn('#EXT-X-ENDLIST', text)

    async def playlist(self, request):
        return web.Response(text='#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXT-X-KEY:METHOD=AES-128,URI="../key.bin"\n#EXTINF:3,\n../segment.ts\n#EXT-X-ENDLIST\n', content_type='application/vnd.apple.mpegurl')

    async def segment(self, request):
        self.seen.append(dict(request.headers))
        return web.Response(body=b'video-bytes', content_type='video/mp2t')

    async def movie(self, request):
        return web.FileResponse(self.folder / 'movie.mp4')

    async def redirect(self, request):
        raise web.HTTPFound(self.redirect_target)

    async def relay(self, request):
        if request.match_info['session'] != self.current.id:
            raise web.HTTPNotFound()
        return await self.current.serve(request)

    def new_session(self, **payload):
        defaults = {'url': str(self.source.make_url('/master.m3u8')), 'kind': 'hls'}
        defaults.update(payload)
        base = str(self.relay_server.make_url('')).rstrip('/')
        self.current = MediaSession(self.client, defaults, base)
        return self.current, base

    async def test_nested_manifests_keys_and_segments_are_proxied(self):
        media, base = self.new_session()
        url = await media.prepare(base)
        async with self.client.get(url) as response:
            master = await response.text()
        self.assertNotIn(str(self.source.port), master)
        variant_url = next(line for line in master.splitlines() if line.startswith('http'))
        async with self.client.get(variant_url) as response:
            playlist = await response.text()
        self.assertRegex(playlist, r'URI="http://127\.0\.0\.1:')
        segment_url = next(line for line in playlist.splitlines() if line.startswith('http'))
        async with self.client.get(segment_url) as response:
            self.assertEqual(await response.read(), b'video-bytes')
        async with self.client.head(variant_url) as response:
            self.assertEqual(response.status, 200)
            self.assertGreater(int(response.headers['Content-Length']), 20)

    async def test_hls_range_probe_returns_complete_manifest(self):
        media, base = self.new_session()
        await media.prepare(base)
        url = media.resource(media.source)
        async with self.client.get(url, headers={'Range': 'bytes=0-1'}) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('#EXT-X-STREAM-INF', await response.text())
        self.assertTrue(url.endswith('.m3u8'))

    async def test_range_requests_survive_the_relay(self):
        (self.folder / 'movie.mp4').write_bytes(bytes(range(100)))
        media, base = self.new_session(url=str(self.source.make_url('/movie.mp4')), kind='mp4')
        url = await media.prepare(base)
        async with self.client.get(url, headers={'Range': 'bytes=10-19'}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(await response.read(), bytes(range(10, 20)))
            self.assertEqual(response.headers['Content-Range'], 'bytes 10-19/100')

    async def test_credentials_do_not_cross_origin_redirects(self):
        other = TestServer(web.Application())
        other.app.router.add_get('/video', self.segment)
        await other.start_server()
        try:
            self.redirect_target = str(other.make_url('/video'))
            source_origin = str(self.source.make_url('')).rstrip('/')
            media, base = self.new_session(url=str(self.source.make_url('/redirect')), kind='mp4', headersByOrigin={source_origin: {'Cookie': 'secret-session', 'Authorization': 'Bearer secret', 'Referer': 'https://page.example/'}})
            url = await media.prepare(base)
            async with self.client.get(url) as response:
                self.assertEqual(await response.read(), b'video-bytes')
            self.assertNotIn('Cookie', self.seen[-1])
            self.assertNotIn('Authorization', self.seen[-1])
            self.assertEqual(self.seen[-1]['Referer'], 'https://page.example/')
        finally:
            await other.close()

    async def test_external_srt_is_declared_and_synced(self):
        media, base = self.new_session(subtitle={'url': str(self.source.make_url('/subs.srt')), 'language': 'fr', 'label': 'French'})
        with patch.object(media, 'timestamp', AsyncMock(return_value=126000)):
            url = await media.prepare(base)
        async with self.client.get(url) as response:
            master = await response.text()
        self.assertIn('LANGUAGE="fr"', master)
        self.assertIn('SUBTITLES="flixer-subs"', master)
        async with self.client.get(media.base + '/subtitles.vtt') as response:
            text = await response.text()
        self.assertIn('MPEGTS:126000', text)
        self.assertIn('Bonjour', text)

    async def test_alternative_subtitles_are_selectable_lazy_cached_and_independently_synced(self):
        tracks = [
            {'content': '1\n00:00:00,000 --> 00:00:02,000\nInitiale\n', 'language':'fr','label':'Track 1','default':True},
            {'url':str(self.source.make_url('/subs.srt')), 'language':'fr','label':'Track 2'},
            {'url':str(self.source.make_url('/missing.srt')), 'language':'fr','label':'Track 3'},
        ]
        media, base = self.new_session(subtitle=tracks[0], subtitles=tracks)
        with patch.object(media, 'timestamp', AsyncMock(return_value=126000)) as timestamp:
            url = await media.prepare(base)
            timestamp.assert_awaited_once()
        self.assertEqual(set(media.vtt_tracks), {0})
        async with self.client.get(url) as response:
            master = await response.text()
        self.assertEqual(master.count('TYPE=SUBTITLES'), 3)
        self.assertEqual(master.count('DEFAULT=YES'), 1)
        for i in [0,1]:
            async with self.client.get(media.base + f'/subtitles/{i}.m3u8') as response:
                self.assertEqual(response.status, 200)
                self.assertIn(f'/subtitles/{i}.vtt', await response.text())
            async with self.client.get(media.base + f'/subtitles/{i}.vtt') as response:
                text = await response.text()
                self.assertIn('MPEGTS:126000', text)
                self.assertIn('Initiale' if i == 0 else 'Bonjour', text)
        with patch.object(media, 'fetch_text', AsyncMock(side_effect=AssertionError('cached track refetched'))):
            async with self.client.get(media.base + '/subtitles/1.vtt') as response:
                self.assertEqual(response.status, 200)
        async with self.client.get(media.base + '/subtitles/2.m3u8') as response:
            self.assertEqual(response.status, 404)
        self.assertIsNone(media.error)
        async with self.client.get(media.base + '/subtitles/0.vtt') as response:
            self.assertEqual(response.status, 200)
        async with self.client.get(media.base + '/subtitles/900.vtt') as response:
            self.assertEqual(response.status, 404)

    async def test_failed_local_analysis_read_does_not_mark_the_tv_stream_as_failed(self):
        media, base = self.new_session()
        url = media.resource(str(self.source.make_url('/missing.ts')), 'segment')
        async with self.client.get(url) as response:
            self.assertEqual(response.status, 404)
        self.assertIsNone(media.error)

    async def test_drm_manifest_is_rejected(self):
        media, _ = self.new_session()
        for line in ('#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://license"', '#EXT-X-KEY:METHOD=AES-128,KEYFORMAT="com.apple.streamingkeydelivery",URI="https://license"'):
            with self.assertRaises(ValueError):
                media.rewrite('#EXTM3U\n' + line, media.source)

    @unittest.skipUnless(binary('ffmpeg') and binary('ffprobe'), 'FFmpeg not installed')
    async def test_real_hls_timestamp_through_opaque_relay_urls(self):
        process = await asyncio.create_subprocess_exec(binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=24:d=3', '-c:v', 'libx264', '-g', '24', '-f', 'hls', '-hls_time', '1', '-y', str(self.folder / 'real.m3u8'), stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        self.assertEqual(process.returncode, 0, stderr)
        media, base = self.new_session(url=str(self.source.make_url('/real/real.m3u8')), kind='hls', subtitle={'content': '1\n00:00:00,000 --> 00:00:02,000\nBonjour\n', 'language': 'fr'})
        await media.prepare(base)
        self.assertRegex(media.vtt, r'MPEGTS:\d+')

    @unittest.skipUnless(binary('ffmpeg') and binary('ffprobe'), 'FFmpeg not installed')
    async def test_real_mp4_remux_ffprobe_and_temporary_cache_cleanup(self):
        result = await asyncio.create_subprocess_exec(binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=24:d=3', '-c:v', 'libx264', '-g', '24', '-pix_fmt', 'yuv420p', '-y', str(self.folder / 'movie.mp4'), stderr=asyncio.subprocess.PIPE)
        _, stderr = await result.communicate()
        self.assertEqual(result.returncode, 0, stderr)
        media, base = self.new_session(url=str(self.source.make_url('/movie.mp4')), kind='mp4', duration=3, subtitle={'content': '1\n00:00:00,000 --> 00:00:02,000\nBonjour\n', 'language': 'fr'})
        url = await media.prepare(base)
        async with self.client.get(url) as response:
            self.assertIn('SUBTITLES="flixer-subs"', await response.text())
        self.assertRegex(media.vtt, r'MPEGTS:\d+')
        cache = media.directory
        self.assertTrue(cache.exists())
        await media.close()
        self.assertFalse(cache.exists())


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'AIRPLAY_FLIXER_HOME': self.directory.name})
        self.environment.start()
        self.companion = Companion()
        await self.companion.start()
        app = web.Application()
        app.router.add_post('/rpc', self.companion.rpc)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.companion.stop()
        await self.companion.client.close()
        await self.client.close()
        self.environment.stop()
        self.directory.cleanup()

    async def test_control_requires_token_and_rejects_web_origins(self):
        async with self.client.post('/rpc', json={'action': 'status'}) as response:
            self.assertEqual(response.status, 403)
        headers = {'Authorization': 'Bearer ' + self.companion.token}
        async with self.client.post('/rpc', json={'action': 'status'}, headers=headers) as response:
            self.assertTrue((await response.json())['ok'])
        async with self.client.post('/rpc', json={'action': 'status'}, headers={**headers, 'Origin': 'https://untrusted.example'}) as response:
            self.assertEqual(response.status, 403)

    async def test_real_airplay_auth_failure_is_not_suppressed(self):
        from airplay_flixer.airplay_compat import play_url
        from pyatv import exceptions
        from pyatv.protocols import airplay
        original = airplay.AirPlayPlayer
        tv = MagicMock()
        tv.stream.play_url = AsyncMock(side_effect=exceptions.AuthenticationError('not authenticated'))
        with self.assertRaises(exceptions.AuthenticationError):
            await play_url(tv, 'https://example.test/video.mp4')
        self.assertIs(airplay.AirPlayPlayer, original)

    async def test_cast_keeps_playback_alive_after_rpc_returns(self):
        config = MagicMock()
        config.identifier, config.name, config.address = 'tv', 'Salon', '127.0.0.1'
        config.get_service.return_value.credentials = 'paired'
        config.get_service.return_value.pairing = PairingRequirement.NotNeeded
        self.companion.devices = {'tv': config}
        self.companion.select('tv')
        tv = MagicMock()
        tv.close.return_value = set()
        tv.remote_control.stop = AsyncMock()
        tv.remote_control.set_position = AsyncMock()
        hold = asyncio.Event()
        tv.stream.play_url = AsyncMock(side_effect=lambda *args, **kwargs: hold.wait())
        # Use a coroutine that genuinely remains active for the entire playback.
        async def playing(*args, **kwargs):
            await hold.wait()
        tv.stream.play_url.side_effect = playing
        with patch('airplay_flixer.server.pyatv.connect', AsyncMock(return_value=tv)), patch('airplay_flixer.server.MediaSession') as media:
            media.return_value.prepare = AsyncMock(return_value='http://127.0.0.1/video.mp4')
            media.return_value.describe = AsyncMock()
            media.return_value.close = AsyncMock()
            result = await self.companion.cast({'url': 'https://video.example/movie.mp4', 'position': 123})
            await asyncio.sleep(0)
            self.assertTrue(result['casting'])
            self.assertFalse(self.companion.play_task.done())
            tv.stream.play_url.assert_awaited_once_with('http://127.0.0.1/video.mp4', position=123.0)
            await self.companion.stop()
            self.assertIsNone(self.companion.play_task)

    async def test_seek_reuses_the_same_media_and_connection(self):
        config = MagicMock()
        config.identifier, config.name, config.address = 'tv', 'Salon', '127.0.0.1'
        config.get_service.return_value.credentials = 'paired'
        self.companion.devices = {'tv': config}
        self.companion.select('tv')
        tv = MagicMock()
        tv.close.return_value = set()
        tv.remote_control.stop = AsyncMock()
        tv.remote_control.set_position = AsyncMock()
        async def hold(*args, **kwargs):
            await asyncio.Event().wait()
        tv.stream.play_url = AsyncMock(side_effect=hold)
        with patch('airplay_flixer.server.pyatv.connect', AsyncMock(return_value=tv)) as connect, patch('airplay_flixer.server.MediaSession') as media:
            media.return_value.prepare = AsyncMock(return_value='http://127.0.0.1/movie.m3u8')
            media.return_value.describe = AsyncMock()
            media.return_value.close = AsyncMock()
            media.return_value.directory = None
            media.return_value.kind = 'hls'
            media.return_value.source = 'https://example.test/movie.m3u8'
            media.return_value.duration = 600
            await self.companion.cast({'url': 'https://example.test/movie.m3u8'})
            await asyncio.sleep(0)
            original_media = self.companion.media
            result = await self.companion.seek(300)
            await asyncio.sleep(0)
            self.assertAlmostEqual(result['position'], 300, delta=0.1)
            self.assertIs(self.companion.media, original_media)
            self.assertEqual(connect.await_count, 1)
            self.assertEqual(tv.stream.play_url.await_count, 1)
            tv.remote_control.set_position.assert_awaited_once_with(300.0)
            self.assertEqual(media.return_value.prepare.await_count, 1)


if __name__ == '__main__':
    unittest.main()
