import asyncio
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from airplay_flixer.opensubtitles import OpenSubtitles, download_url
from airplay_flixer.server import Companion
from airplay_flixer.media import MediaSession

SRT='1\n00:00:01,000 --> 00:00:03,000\nBonjour\n'
class Body:
    def __init__(self,data):self.data=data
    async def iter_chunked(self,size):
        for i in range(0,len(self.data),size):yield self.data[i:i+size]
class Response:
    def __init__(self,data=b'',status=200,headers=None):self.content=Body(data);self.status=status;self.headers=headers or {}
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass

class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.p=OpenSubtitles(self.folder)
    async def asyncTearDown(self):self.temp.cleanup()

    async def test_credentials_private_and_password_not_saved(self):
        self.p.request=AsyncMock(return_value={'token':'private-token','base_url':'vip-api.opensubtitles.com'})
        result=await self.p.configure(None,{'key':'private-key','username':'stan','password':'private-password'})
        self.assertEqual(result,{'configured':True,'connected':True,'username':'stan'})
        saved=self.p.path.read_text();self.assertNotIn('private-password',saved)
        self.assertEqual(stat.S_IMODE(self.p.path.stat().st_mode),0o600)
        self.assertNotIn('private-key',json.dumps(result));self.assertNotIn('private-token',json.dumps(result))
        await self.p.configure(None,{'clear':True});self.assertFalse(self.p.path.exists())

    async def test_search_episode_filters_and_files(self):
        self.p.request=AsyncMock(return_value={'total_pages':2,'data':[{'attributes':{'language':'fr','release':'Episode.WEB-DL','fps':23.976,'hearing_impaired':True,'feature_details':{'title':'La série · épisode 1'},'files':[{'file_id':12,'file_name':'release.srt'}]}}]})
        result=await self.p.search(None,{'query':'La Série','type':'episode','language':'fr','season_number':2,'episode_number':1})
        params=self.p.request.call_args.kwargs['params'];self.assertEqual(params['season_number'],2);self.assertEqual(params['query'],'la série')
        self.assertTrue(result['results'][0]['hearingImpaired']);self.assertEqual(result['totalPages'],2)
        self.assertIn(12,self.p.results)
        with self.assertRaises(ValueError):await self.p.search(None,{'query':'série','type':'episode','season_number':True,'episode_number':1})

    async def test_quota_has_clear_error_and_cross_host_redirect_cannot_leak_key(self):
        self.p.config={'key':'private-key','token':'private-token'}
        client=MagicMock();client.request.return_value=Response(status=406)
        with self.assertRaisesRegex(ValueError,'Quota'):await self.p.request(client,'POST','download',body={'file_id':12})
        client.request.return_value=Response(status=302,headers={'Location':'https://elsewhere.test/steal'})
        with self.assertRaisesRegex(ValueError,'Redirection'):await self.p.request(client,'GET','subtitles')
        self.assertEqual(client.request.call_count,2)

    async def test_download_requires_result_and_reuses_cached_file_without_quota(self):
        with self.assertRaises(ValueError):await self.p.download(None,12)
        self.p.results[12]={'title':'Film','release':'WEB-DL','language':'fr'}
        self.p.request=AsyncMock(return_value={'link':'https://dl.opensubtitles.com/file.srt','remaining':4})
        client=MagicMock();client.get.return_value=Response(SRT.encode())
        first=await self.p.download(client,12);second=await self.p.download(client,12)
        self.assertEqual(first['id'],second['id']);self.assertEqual(first['track']['content'],SRT)
        self.p.request.assert_awaited_once()
        self.assertNotIn('Api-Key',client.get.call_args.kwargs['headers'])
        self.assertNotIn('Authorization',client.get.call_args.kwargs['headers'])
        for url in ('http://dl.opensubtitles.com/x','https://opensubtitles.com.evil.test/x','https://u:p@dl.opensubtitles.com/x','https://127.0.0.1/x'):
            with self.assertRaises(ValueError):download_url(url)

    async def test_import_without_existing_subtitles_prepares_timing_before_reload(self):
        with patch('airplay_flixer.server.data_directory',return_value=self.folder):c=Companion()
        c.client=None;c.atv=MagicMock();c.settings={'selected':'tv'}
        media=MediaSession(None,{'url':'https://video.test/master.m3u8','kind':'hls'},'http://192.168.1.2:99');c.media=media
        c.play_task=asyncio.create_task(asyncio.Event().wait())
        c.opensubtitles.downloads['id']={'track':{'content':SRT,'label':'OpenSubtitles · WEB-DL','language':'fr','provider':'opensubtitles','fileId':12}}
        media.timestamp=AsyncMock(return_value=900000)
        c.sync_subtitle=AsyncMock(return_value={'ok':True})
        try:
            await c.import_subtitle(media.id,'id')
            self.assertEqual(len(media.subtitle_tracks),1)
            self.assertEqual(media.subtitle_timestamp,900000)
            c.sync_subtitle.assert_awaited_once_with(media.id,0,0)
            self.assertFalse(c.play_task.done())
        finally:c.play_task.cancel();await asyncio.gather(c.play_task,return_exceptions=True)
