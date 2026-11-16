import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from airplay_flixer.media import MediaSession, binary
from airplay_flixer.dlna_compat import choose_manifest
from airplay_flixer.video_download import VideoDownload, check_playlist

MASTER = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",LANGUAGE="en",NAME="English",DEFAULT=YES,URI="a/audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360,CODECS="avc1.4D001E,mp4a.40.2",AUDIO="audio"
v/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="audio"
v/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=12000000,RESOLUTION=3840x2160,CODECS="hvc1.2.4.L153.B0,mp4a.40.2",AUDIO="audio"
never-fetch.m3u8
'''

class ChoiceTests(unittest.TestCase):
    def test_video_download_rejects_live_and_drm_playlists(self):
        live = '#EXTM3U\n#EXTINF:2,\nsegment.ts\n'
        with self.assertRaisesRegex(ValueError, 'direct'):
            check_playlist(live)
        with self.assertRaisesRegex(ValueError, 'protection'):
            check_playlist(live + '#EXT-X-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery"\n#EXT-X-ENDLIST\n')

    def test_h264_1080_and_matching_audio_are_kept_together(self):
        text, info = choose_manifest(MASTER,'https://cdn.example/master.m3u8')
        self.assertEqual(info,{'width':1920,'height':1080})
        self.assertIn('https://cdn.example/a/audio.m3u8',text)
        self.assertIn('AUDIO="audio"',text)
        self.assertEqual(text.count('#EXT-X-STREAM-INF:'),1)
        self.assertNotIn('never-fetch',text)
        self.assertNotIn('hvc1',text)

    def test_selects_smallest_oversized_variant_and_rejects_audio_only_master(self):
        text, info = choose_manifest(MASTER.replace('avc1.640028','hvc1.2.4').replace('avc1.4D001E','hvc1.2.4'),'https://cdn.example/master.m3u8')
        self.assertEqual(info['height'],1080)
        with self.assertRaises(ValueError):
            choose_manifest('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=200000,CODECS="mp4a.40.2"\na.m3u8','https://a/master.m3u8')


class DirectDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_copy_direct_and_cancel_cleans_partial_file(self):
        data = b'test video file'
        sending = asyncio.Event()
        release = asyncio.Event()

        async def origin(request):
            if request.path == '/fast.mp4':
                return web.Response(body=data, content_type='video/mp4')
            if request.path == '/subtitle.vtt':
                return web.Response(text='WEBVTT\n\n00:01.000 --> 00:03.500 align:start\n<v Marie>Bonjour &amp; bienvenue\n')
            if request.path == '/missing.srt':
                raise web.HTTPNotFound()
            response = web.StreamResponse(headers={'Content-Type': 'video/mp4'})
            await response.prepare(request)
            await response.write(data)
            sending.set()
            await release.wait()
            return response

        app = web.Application()
        app.router.add_get('/{name}', origin)
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, 'home', return_value=Path(temporary)):
            async with TestServer(app) as server, aiohttp.ClientSession() as client:
                download = VideoDownload()
                base = str(server.make_url('')).rstrip('/')
                track = {'url': f'{base}/subtitle.vtt'}
                payload = {'url': f'{base}/fast.mp4', 'kind': 'mp4', 'title': 'Test', 'subtitle': track}
                folder = Path(temporary) / 'Downloads'
                folder.mkdir()
                (folder / 'Test.srt').write_text('existant')
                download.start(client, payload, base)
                await asyncio.wait_for(download.task, 5)
                self.assertEqual(download.status()['state'], 'done')
                self.assertEqual(download.status()['subtitleFilename'], 'Test (2).srt')
                self.assertEqual((folder / 'Test (2).mp4').read_bytes(), data)
                self.assertEqual((folder / 'Test.srt').read_text(), 'existant')
                self.assertEqual((folder / 'Test (2).srt').read_text(), '1\n00:00:01,000 --> 00:00:03,500\nBonjour & bienvenue\n')
                download.start(client, {**payload, 'title': 'Sans piste', 'subtitle': {'url': f'{base}/missing.srt'}}, base)
                await asyncio.wait_for(download.task, 5)
                self.assertEqual(download.status()['state'], 'done')
                self.assertIn('subtitleWarning', download.status())
                saved = set(folder.iterdir())
                download.start(client, {'url': f'{base}/slow.mp4', 'kind': 'mp4', 'title': 'Annulé'}, base)
                try:
                    await asyncio.wait_for(sending.wait(), 5)
                    self.assertEqual((await download.cancel())['state'], 'cancelled')
                    self.assertEqual(set(folder.iterdir()), saved)
                finally:
                    release.set()
                    await download.cancel()


@unittest.skipUnless(binary('ffmpeg') and binary('ffprobe'),'FFmpeg required')
class RemuxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        for name in ['v','a']:(self.folder/name).mkdir()
        subprocess.run([binary('ffmpeg'),'-v','error','-nostdin','-f','lavfi','-i','testsrc2=size=320x180:rate=25','-t','3','-c:v','libx264','-preset','ultrafast','-g','25','-an','-f','hls','-hls_time','1','-hls_list_size','0','-hls_segment_type','fmp4',str(self.folder/'v/video.m3u8')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run([binary('ffmpeg'),'-v','error','-nostdin','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:a','aac','-ac','2','-f','hls','-hls_time','1','-hls_list_size','0','-hls_segment_type','fmp4',str(self.folder/'a/audio.m3u8')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.fetched=[]
        async def origin(request):
            name=request.match_info['name'];self.fetched.append(name)
            if name=='master.m3u8':return web.Response(text=MASTER,content_type='application/vnd.apple.mpegurl')
            if name == 'download':
                return web.Response(text=MASTER.replace('never-fetch.m3u8', 'v/video.m3u8'), content_type='text/plain')
            path=self.folder/name
            if not path.is_file():raise web.HTTPNotFound()
            # Reproduce the actual CDN disguising fMP4 as image/png.
            return web.Response(body=path.read_bytes(),content_type='application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'image/png')
        app=web.Application();app.router.add_get('/{name:.*}',origin)
        self.source=TestServer(app);await self.source.start_server()
        self.client=aiohttp.ClientSession()
        self.media=None
        async def relay(request):return await self.media.serve(request)
        app=web.Application();app.router.add_get('/media/{session}/{tail:.*}',relay)
        self.relay=TestServer(app);await self.relay.start_server()
        self.media=MediaSession(self.client,{'url':str(self.source.make_url('/master.m3u8')),'kind':'hls','duration':3,'targetProtocol':'dlna'},str(self.relay.make_url('')).rstrip('/'))

    async def asyncTearDown(self):
        if self.media:await self.media.close()
        await self.relay.close();await self.source.close();await self.client.close();self.temp.cleanup()

    async def test_real_fmp4_with_separate_audio_becomes_single_playable_ts(self):
        url=await self.media.prepare_dlna_compatible(str(self.relay.make_url('')).rstrip('/'),'video/mp2t')
        async with self.client.get(url) as response:
            self.assertEqual(response.status,200)
            self.assertEqual(response.content_type,'video/mp2t')
            data=await response.read()
        self.assertGreater(len(data),0)
        self.assertEqual(data[0],0x47)
        out=self.folder/'received.ts';out.write_bytes(data)
        result=subprocess.run([binary('ffprobe'),'-v','error','-show_entries','stream=codec_type,codec_name,width,height','-of','json',str(out)],capture_output=True,check=True)
        streams=json.loads(result.stdout)['streams']
        self.assertTrue(any(s['codec_type']=='video' and s['codec_name']=='h264' for s in streams))
        self.assertTrue(any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in streams))
        self.assertNotIn('never-fetch.m3u8',self.fetched)
        self.assertEqual(self.media.compat_info['height'],180)
        await self.media.compat_process.wait()
        async with self.client.get(url,headers={'Range':'bytes=0-187'}) as response:
            self.assertEqual(response.status,206)
            self.assertEqual(len(await response.read()),188)
        async with self.client.get(url,headers={'Range':'bytes=999999999-'}) as response:
            self.assertEqual(response.status,416)
        process=self.media.compat_process;directory=self.media.compat_directory
        await self.media.close()
        self.assertIsNotNone(process.returncode)
        self.assertFalse(directory.exists())

    async def test_hls_download_keeps_separate_audio_and_avoids_overwriting(self):
        download = VideoDownload()
        app = web.Application()
        app.router.add_get('/media/{session}/{tail:.*}', download.serve)
        async with TestServer(app) as relay:
            base = str(relay.make_url('')).rstrip('/')
            folder = self.folder / 'Downloads'
            folder.mkdir()
            original = folder / 'Épisode.mp4'
            original.write_bytes(b'original')
            with patch.object(Path, 'home', return_value=self.folder):
                download.start(self.client, {'url': str(self.source.make_url('/download')), 'kind': 'hls', 'title': 'Épisode'}, base)
                await asyncio.wait_for(download.task, 20)
            result = download.status()
            self.assertEqual(result['state'], 'done', result)
            self.assertEqual(result['filename'], 'Épisode (2).mp4')
            self.assertEqual(original.read_bytes(), b'original')
            self.assertEqual(list(folder.glob('*.part')), [])
            probe = subprocess.run([binary('ffprobe'), '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(folder / result['filename'])], capture_output=True, check=True)
            self.assertEqual({s['codec_type'] for s in json.loads(probe.stdout)['streams']}, {'video', 'audio'})

    async def test_m2ts_transport_uses_192_byte_packets_for_receivers_requiring_it(self):
        url=await self.media.prepare_dlna_compatible(str(self.relay.make_url('')).rstrip('/'),'video/vnd.dlna.mpeg-tts')
        async with self.client.get(url) as response:
            self.assertEqual(response.content_type,'video/vnd.dlna.mpeg-tts')
            data=await response.read()
        self.assertGreater(len(data),192)
        self.assertEqual(data[4],0x47)
        self.assertEqual(data[196],0x47)

    async def test_timing_probe_reads_video_leaf_without_loading_other_renditions(self):
        local=self.media.resource(self.media.source,'playlist')
        timestamp=await self.media.timestamp(local)
        self.assertIsInstance(timestamp,int)
        self.assertGreaterEqual(timestamp,0)
        self.assertNotIn('a/audio.m3u8',self.fetched)
        self.assertNotIn('never-fetch.m3u8',self.fetched)

    async def test_real_hls_player_discovers_both_external_webvtt_renditions(self):
        tracks=[{'content':f'1\n00:00:00,000 --> 00:00:02,000\nVersion {i+1}\n','language':'fr','label':f'Piste {i+1}','default':i==0} for i in range(2)]
        self.media=MediaSession(self.client,{'url':str(self.source.make_url('/v/video.m3u8')),'kind':'hls','duration':3,'subtitle':tracks[0],'subtitles':tracks},str(self.relay.make_url('')).rstrip('/'))
        url=await self.media.prepare(str(self.relay.make_url('')).rstrip('/'))
        probe=await asyncio.create_subprocess_exec(binary('ffprobe'),'-v','error','-allowed_extensions','ALL','-read_intervals','%+#2','-show_entries','stream=codec_type,codec_name:stream_tags=language,title','-of','json',url,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        output,_=await asyncio.wait_for(probe.communicate(),15)
        self.assertEqual(probe.returncode,0)
        streams=json.loads(output)['streams']
        self.assertEqual(sum(s.get('codec_type')=='subtitle' for s in streams),2)
        self.assertTrue(all(s.get('tags',{}).get('language')=='fr' for s in streams if s.get('codec_type')=='subtitle'))
