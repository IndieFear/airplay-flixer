import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock,MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from airplay_flixer.media import MediaSession

class TimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_video_stream_or_packet_timestamps_when_format_start_is_missing(self):
        for data, expected in [
            ({'streams':[{'start_time':'3.25'}],'format':{}},292500),
            ({'streams':[{}],'packets':[{'pts_time':'2.5'},{'pts_time':'2.6'}]},225000),
        ]:
            media=MediaSession(MagicMock(),{'url':'https://example.test/master.m3u8','kind':'hls'},'http://192.168.1.2:9000')
            media.media_playlist=AsyncMock(return_value=('#EXTM3U','https://example.test/video/leaf.m3u8'))
            proc=MagicMock();proc.returncode=0;proc.communicate=AsyncMock(return_value=(json.dumps(data).encode(),b''))
            with patch('airplay_flixer.media.binary',return_value='/ffprobe'),patch('airplay_flixer.media.hls_probe_options',return_value=()),patch('airplay_flixer.media.asyncio.create_subprocess_exec',AsyncMock(return_value=proc)) as spawn:
                result=await media.timestamp('http://127.0.0.1:9000/media/id/root.m3u8')
            self.assertEqual(result,expected)
            args=spawn.call_args.args
            self.assertIn('-select_streams',args)
            self.assertIn('v:0',args)
            self.assertIn('http://127.0.0.1:9000/media/',args[-1])
            self.assertEqual(list(media.resources.values()),['https://example.test/video/leaf.m3u8'])

    async def test_no_timestamp_is_not_silently_treated_as_zero(self):
        media=MediaSession(MagicMock(),{'url':'https://example.test/video.mp4','kind':'mp4'},'http://127.0.0.1:9000')
        proc=MagicMock();proc.returncode=0;proc.communicate=AsyncMock(return_value=(b'{"streams":[{}]}',b''))
        with patch('airplay_flixer.media.binary',return_value='/ffprobe'),patch('airplay_flixer.media.hls_probe_options',return_value=()),patch('airplay_flixer.media.asyncio.create_subprocess_exec',AsyncMock(return_value=proc)):
            with self.assertRaises(ValueError):await media.timestamp('http://127.0.0.1:9000/media/id/video.mp4')
