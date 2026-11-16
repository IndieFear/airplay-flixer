import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from airplay_flixer.subtitle_offset import shift_webvtt,offset_value
from airplay_flixer.media import MediaSession,to_webvtt
from airplay_flixer.server import Companion
from airplay_flixer.timeline import Timeline

class OffsetTests(unittest.TestCase):
    def test_positive_is_later_and_keeps_mapping_identifiers_settings_and_style(self):
        text='WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:126000\n\nSTYLE\n::cue { color: white; }\n\ncue-1\n00:00:10.000 --> 00:00:12.000 align:start\nBonjour\n'
        result=shift_webvtt(text,.5)
        self.assertIn('00:00:10.500 --> 00:00:12.500 align:start',result)
        self.assertIn('MPEGTS:126000',result)
        self.assertIn('cue-1',result)
        self.assertIn('::cue { color: white; }',result)
        self.assertEqual(shift_webvtt(text,0),text)

    def test_negative_is_earlier_clips_partial_cues_and_removes_fully_negative_cues(self):
        result=shift_webvtt('WEBVTT\n\n1\n00:00:00.000 --> 00:00:00.400\nGone\n\n2\n00:00:00.300 --> 00:00:01.000\nKeep\n\n3\n00:10.000 --> 00:12.000\nLater\n',-.5)
        self.assertNotIn('Gone',result)
        self.assertIn('00:00:00.000 --> 00:00:00.500',result)
        self.assertIn('00:00:09.500 --> 00:00:11.500',result)

    def test_invalid_offsets_are_rejected(self):
        for value in [None,True,'0.5',float('nan'),float('inf'),-601,601]:
            with self.assertRaises(ValueError):offset_value(value)
        self.assertEqual(offset_value(.1234),.123)

class ReloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict('os.environ',{'AIRPLAY_FLIXER_HOME':self.temp.name});self.env.start()
        self.c=Companion()
        self.c.atv=MagicMock();self.c.atv.remote_control.stop=AsyncMock();self.c.atv.close.return_value=set()
        self.c.timeline=Timeline(lambda:100)
        self.c.timeline.update(position=137,duration=1000,state='paused')
        tracks=[{'content':'WEBVTT\n\n00:00:10.000 --> 00:00:12.000\nA\n','label':'Piste 1','language':'fr'},{'content':'WEBVTT\n\n00:00:10.000 --> 00:00:12.000\nB\n','label':'Piste 2','language':'fr'}]
        self.media=MediaSession(MagicMock(),{'url':'https://example.test/master.m3u8','kind':'hls','subtitles':tracks},'http://192.168.1.2:1234')
        for i,t in enumerate(tracks):self.media.cache_subtitle(i,to_webvtt(t['content'],126000))
        self.c.media=self.media
        self.old=asyncio.create_task(asyncio.Event().wait());self.c.play_task=self.old
        self.c.playback_url=self.media.base+'/master.m3u8'
        async def hold(*args,**kwargs):await asyncio.Event().wait()
        self.c.play=AsyncMock(side_effect=hold)

    async def asyncTearDown(self):
        await self.c.stop();self.env.stop();self.temp.cleanup()

    async def test_explicit_apply_preserves_position_pause_and_other_tracks_and_busts_cache(self):
        result=await self.c.sync_subtitle(self.media.id,1,.5)
        await asyncio.sleep(0)
        self.assertTrue(self.old.cancelled())
        self.c.play.assert_awaited_once_with(self.media.base+'/master.m3u8?subrev=1',137,paused=True)
        self.assertEqual(self.media.default_subtitle,1)
        self.assertEqual(self.media.subtitle_tracks[0]['offset'],0)
        self.assertEqual(self.media.subtitle_tracks[1]['offset'],.5)
        self.assertEqual(self.media.subtitle_revision,1)
        self.assertEqual(result['subtitleSession'],self.media.id)
        self.assertIn('00:00:10.000',await self.media.subtitle_vtt(1))
        self.assertIn('00:00:10.500',shift_webvtt(await self.media.subtitle_vtt(1),.5))

    async def test_invalid_stale_or_unchanged_settings_do_not_reload(self):
        for session,index,offset in [('other',0,.5),(self.media.id,99,.5),(self.media.id,0,999)]:
            with self.assertRaises(ValueError):await self.c.sync_subtitle(session,index,offset)
        await self.c.sync_subtitle(self.media.id,0,0)
        self.assertFalse(self.old.done())
        self.c.play.assert_not_called()

    async def test_unavailable_alternative_does_not_interrupt_current_video(self):
        self.media.subtitle_vtt=AsyncMock(side_effect=ValueError('Unavailable'))
        with self.assertRaises(ValueError):await self.c.sync_subtitle(self.media.id,1,.5)
        self.assertFalse(self.old.done())
        self.c.play.assert_not_called()
        self.assertEqual(self.media.subtitle_revision,0)

    async def test_paused_reload_sends_an_explicit_pause_after_playing(self):
        self.c.timeline.update(state='playing')
        controller=MagicMock();controller._send_url_command=AsyncMock(return_value=MagicMock(code=200))
        self.c.timeline.controller=controller
        with patch('airplay_flixer.server.asyncio.sleep',AsyncMock()):
            await self.c.pause_after_reload(self.c.timeline)
        controller._send_url_command.assert_awaited_once_with({'type':'setRate','rate':0.0})
        self.assertEqual(self.c.timeline.state,'paused')
