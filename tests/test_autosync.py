import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock,MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from airplay_flixer.autosync import propose,shifted,window_start,run_process,analyse_files
from airplay_flixer.server import Companion
from airplay_flixer.media import MediaSession

class AlignmentTests(unittest.TestCase):
    def reference(self):
        import numpy as np
        result=np.zeros(24000);rng=np.random.default_rng(42);i=800
        while i<23000:
            length=int(rng.integers(90,350));result[i:i+length]=1;i+=length+int(rng.integers(80,400))
        return result

    def test_ffsubsync_detects_both_directions_without_applying_current_manual_offset(self):
        reference=self.reference()
        for delta in [-350,180,0]:
            result=propose(reference,shifted(reference,delta))
            self.assertEqual(result['state'],'proposed')
            self.assertEqual(result['offset'],-delta/100)

    def test_silence_and_disagreement_between_passages_do_not_produce_a_proposal(self):
        import numpy as np
        reference=self.reference()
        self.assertEqual(propose(np.zeros_like(reference),reference)['state'],'uncertain')
        captions=shifted(reference,200);captions[12000:]=shifted(reference,-500)[12000:]
        self.assertEqual(propose(reference,captions)['state'],'uncertain')

    def test_uninformative_passage_is_not_misreported_as_a_different_montage(self):
        reference=self.reference()
        captions=reference.copy();reference[:12000]=1
        result=propose(reference,captions)
        self.assertEqual(result['state'],'uncertain')
        self.assertEqual(result['reason'],'insufficient_validation')
        self.assertNotIn('autre montage',result['message'])

    def test_excerpt_is_bounded_by_the_movie(self):
        self.assertEqual(window_start(10,1000),0)
        self.assertEqual(window_start(800,1000),740)
        self.assertEqual(window_start(999,1000),760)

    def test_actual_vad_rejects_a_silent_wav_without_hallucinating_an_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sample.wav'
            with wave.open(str(path),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0'*(16000*2*90))
            result=analyse_files(path,[(i,i+2) for i in range(0,90,5)],0)
        self.assertEqual(result['state'],'uncertain')

class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict('os.environ',{'AIRPLAY_FLIXER_HOME':self.tmp.name});self.env.start()
        self.c=Companion();self.c.atv=MagicMock();self.c.atv.remote_control.stop=AsyncMock();self.c.atv.close.return_value=set()
        self.c.media=MediaSession(MagicMock(),{'url':'https://example.test/master.m3u8','subtitles':[{'content':'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nA\n'}]},'http://127.0.0.1:1234')
        self.playing=asyncio.create_task(asyncio.Event().wait());self.c.play_task=self.playing

    async def asyncTearDown(self):
        await self.c.stop();self.env.stop();self.tmp.cleanup()

    async def test_proposal_does_not_seek_reload_or_modify_subtitles(self):
        with patch('airplay_flixer.server.analyse_subtitles',AsyncMock(return_value={'state':'proposed','offset':-1.8,'message':'Coherent'})):
            result=await self.c.start_auto_sync(self.c.media.id,0)
            self.assertEqual(result['autoSync']['state'],'running')
            await self.c.auto_task
        self.assertEqual(self.c.auto_sync['offset'],-1.8)
        self.assertEqual(self.c.media.subtitle_tracks[0]['offset'],0)
        self.assertIs(self.c.play_task,self.playing)
        self.assertFalse(self.playing.done())

    async def test_cancellation_leaves_the_movie_running(self):
        gate=asyncio.Event()
        async def analyse(*args):await gate.wait()
        with patch('airplay_flixer.server.analyse_subtitles',analyse):
            await self.c.start_auto_sync(self.c.media.id,0)
            task=self.c.auto_task;await asyncio.sleep(0)
            await self.c.cancel_auto_sync()
        self.assertTrue(task.cancelled())
        self.assertEqual(self.c.auto_sync['state'],'cancelled')
        self.assertFalse(self.playing.done())

    async def test_old_session_is_rejected(self):
        with self.assertRaises(ValueError):await self.c.start_auto_sync('old',0)
        self.assertIsNone(self.c.auto_task)

    async def test_cancelled_process_is_killed_and_reaped(self):
        gate=asyncio.Event();process=MagicMock();process.returncode=None
        async def communicate():await gate.wait()
        process.communicate=communicate;process.wait=AsyncMock()
        with patch('airplay_flixer.autosync.asyncio.create_subprocess_exec',AsyncMock(return_value=process)):
            task=asyncio.create_task(run_process(['fake'],10));await asyncio.sleep(0);await asyncio.sleep(0);task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        process.kill.assert_called_once();process.wait.assert_awaited_once()

class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_hls_audio_reference_and_leaf_manifest_are_not_confused_with_video_segments(self):
        from airplay_flixer.autosync import analyse
        cues='WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n'+'\n\n'.join(f'00:{i:02}:01.000 --> 00:{i:02}:02.000\nLine' for i in range(8))
        for master,expected in [('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Audio",URI="sound.m3u8"\n#EXT-X-STREAM-INF:RESOLUTION=1280x720,CODECS="avc1.640028,mp4a.40.2",AUDIO="a"\nvideo.m3u8','https://cdn.example/sound.m3u8'),('#EXTM3U\n#EXTINF:3,\nsegment.ts\n#EXT-X-ENDLIST','https://cdn.example/master.m3u8')]:
            media=MediaSession(MagicMock(),{'url':'https://cdn.example/master.m3u8','kind':'hls','duration':1000},'http://192.168.1.2:1234')
            media.subtitle_vtt=AsyncMock(return_value=cues)
            media.fetch_text=AsyncMock(return_value=(master,media.source))
            calls=[]
            async def fake_process(arguments,timeout):
                calls.append(arguments)
                if len(calls)==1:
                    self.assertEqual(arguments[arguments.index('-map')+1],'0:a:0')
                    with wave.open(arguments[-1],'wb') as out:
                        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0'*32000)
                    return b''
                job=json.loads(Path(arguments[-1]).read_text())
                self.assertIn('intervals',job)
                self.assertNotIn('text',job)
                return b'{"state":"proposed","offset":-1.8}'
            with patch('airplay_flixer.media.binary',return_value='/ffmpeg'),patch('airplay_flixer.media.hls_probe_options',return_value=()),patch('airplay_flixer.autosync.run_process',fake_process):
                result=await analyse(media,0,100,'http://127.0.0.1:1234',lambda *args:None)
            self.assertEqual(result['offset'],-1.8)
            self.assertIn(expected,media.resources.values())
            self.assertFalse(Path(calls[0][-1]).exists())

    async def test_uncertain_first_excerpt_automatically_tries_another_passage(self):
        from airplay_flixer.autosync import analyse
        raw='WEBVTT\n\n'+'\n\n'.join(f'00:{i:02}:01.000 --> 00:{i:02}:02.000\nLine' for i in range(8))
        media=MediaSession(MagicMock(),{'url':'https://cdn.example/master.m3u8','kind':'hls','duration':1000},'http://127.0.0.1:1234')
        media.subtitle_vtt=AsyncMock(return_value=raw);media.fetch_text=AsyncMock(return_value=('#EXTM3U\n#EXTINF:3,\nsegment.ts',media.source))
        starts=[];calls=[]
        async def process(arguments,timeout):
            calls.append(arguments)
            if len(calls) in {1,3}:
                with wave.open(arguments[-1],'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0'*32000)
                return b''
            starts.append(json.loads(Path(arguments[-1]).read_text())['start'])
            return b'{"state":"uncertain","reason":"insufficient_validation","sampleSeconds":240}' if len(calls)==2 else b'{"state":"proposed","offset":1.13,"sampleSeconds":240}'
        with patch('airplay_flixer.media.binary',return_value='/ffmpeg'),patch('airplay_flixer.media.hls_probe_options',return_value=()),patch('airplay_flixer.autosync.run_process',process):
            result=await analyse(media,0,800,'http://127.0.0.1:1234',lambda *args:None)
        self.assertEqual(starts,[740,500])
        self.assertEqual(result['attempts'],2)
        self.assertEqual(result['analysedSeconds'],480)
        self.assertEqual(result['offset'],1.13)
