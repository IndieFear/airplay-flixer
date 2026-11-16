import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import AsyncMock, MagicMock
import aiohttp

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from airplay_flixer.subdl import SubDL, file_url, subtitle_text
from test_opensubtitles import Response, SRT


def zipped(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():z.writestr(name,data)
    return output.getvalue()

class ArchiveTests(unittest.TestCase):
    def test_selects_exact_episode_and_refuses_ambiguity(self):
        archive=zipped({'Show.S02E01.srt':SRT,'Show.S02E02.srt':SRT.replace('Bonjour','Autre')})
        self.assertEqual(subtitle_text(archive,2,1),SRT)
        with self.assertRaises(ValueError):subtitle_text(archive,2,3)
        with self.assertRaises(ValueError):subtitle_text(archive)
        with self.assertRaises(ValueError):subtitle_text(zipped({'Show.S02E02.srt':SRT}),2,1)

    def test_rejects_large_or_invalid_archive_and_restricted_urls(self):
        with self.assertRaises(ValueError):subtitle_text(zipped({'big.srt':'x'*500001}))
        with self.assertRaises(ValueError):subtitle_text(b'PKbroken')
        self.assertEqual(file_url('/subtitle/id/file'),'https://dl.subdl.com/subtitle/id/file')
        self.assertNotIn('api_key',file_url('/subtitle/id/file?api_key=secret'))
        for url in ('http://dl.subdl.com/subtitle/id','https://dl.subdl.com.evil.test/subtitle/id','https://user:password@dl.subdl.com/subtitle/id','https://127.0.0.1/subtitle/id'):
            with self.assertRaises(ValueError):file_url(url)

class SubDLTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.p=SubDL(Path(self.temp.name))
    async def asyncTearDown(self):self.temp.cleanup()

    async def test_key_private_and_never_returned(self):
        status=await self.p.configure(None,{'key':'private-key'})
        self.assertEqual(status,{'configured':True})
        self.assertEqual(stat.S_IMODE(self.p.path.stat().st_mode),0o600)
        await self.p.configure(None,{'clear':True});self.assertFalse(self.p.path.exists())

    async def test_raw_files_filter_episode_language_and_preserve_fps(self):
        self.p.request=AsyncMock(return_value={'status':True,'results':[{'name':'La série'}],'subtitles':[{'fps':'23.976','unpack_files':[{'url':'/subtitle/pack/fr1','format':'srt','season':2,'episode':1,'language':'FR','release_name':'WEB-DL'},{'url':'/subtitle/pack/fr2','format':'srt','season':2,'episode':2,'language':'FR'},{'url':'/subtitle/pack/en1','format':'srt','season':2,'episode':1,'language':'EN'}]}]})
        result=await self.p.search(None,{'query':'tt1234567','language':'fr','type':'episode','season_number':2,'episode_number':1})
        self.assertEqual(len(result['results']),1)
        self.assertEqual(result['results'][0]['fps'],'23.976')
        params=self.p.request.call_args.args[1];self.assertEqual(params['type'],'tv');self.assertEqual(params['imdb_id'],'tt1234567');self.assertEqual(params['languages'],'FR');self.assertEqual(params['unpack'],1)

    async def test_request_errors_do_not_expose_key(self):
        self.p.config={'key':'private-key'}
        client=MagicMock();client.get.side_effect=aiohttp.ClientError('https://api.subdl.com/?api_key=private-key')
        with self.assertRaises(ValueError) as err:await self.p.request(client,{'film_name':'film'})
        self.assertNotIn('private-key',str(err.exception))
        client.get.side_effect=None;client.get.return_value=Response(status=429)
        with self.assertRaisesRegex(ValueError,'Quota'):await self.p.request(client,{'film_name':'film'})

    async def test_download_only_known_results_and_cache_without_key_to_files(self):
        with self.assertRaises(ValueError):await self.p.download(None,'missing')
        self.p.results['file']={'url':'https://dl.subdl.com/subtitle/id/file','season':None,'episode':None,'release':'WEB-DL','title':'Movie','language':'fr'}
        client=MagicMock();client.get.return_value=Response(SRT.encode())
        first=await self.p.download(client,'file');second=await self.p.download(client,'file')
        self.assertEqual(first['id'],second['id']);self.assertEqual(first['track']['provider'],'subdl');self.assertEqual(first['track']['content'],SRT)
        self.assertEqual(client.get.call_count,1)
        self.assertNotIn('x-api-key',client.get.call_args.kwargs['headers'])

    async def test_import_does_not_reuse_other_providers_track_with_same_id(self):
        import asyncio
        from unittest.mock import patch
        from airplay_flixer.server import Companion
        from airplay_flixer.media import MediaSession
        with patch('airplay_flixer.server.data_directory',return_value=Path(self.temp.name)):c=Companion()
        c.atv=MagicMock();c.settings={'selected':'tv'}
        media=MediaSession(None,{'url':'https://video.test/master.m3u8','kind':'hls'},'http://192.168.1.2:99')
        media.subtitle_tracks=[{'provider':'opensubtitles','fileId':'same','content':SRT,'language':'fr','label':'OS','offset':0}]
        media.subtitle_timestamp=900000;c.media=media
        c.subdl.downloads['id']={'track':{'provider':'subdl','fileId':'same','content':SRT,'language':'fr','label':'SubDL'}}
        c.play_task=asyncio.create_task(asyncio.Event().wait());c.sync_subtitle=AsyncMock(return_value={})
        try:
            await c.import_subtitle(media.id,'id','subdl')
            self.assertEqual(len(media.subtitle_tracks),2)
            self.assertEqual(media.subtitle_tracks[1]['provider'],'subdl')
            c.sync_subtitle.assert_awaited_once_with(media.id,1,0)
        finally:c.play_task.cancel();await asyncio.gather(c.play_task,return_exceptions=True)
