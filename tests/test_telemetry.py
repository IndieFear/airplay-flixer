import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
from airplay_flixer.telemetry import StreamTelemetry


class TelemetryTests(unittest.TestCase):
    def test_variant_switch_tracks_delivered_segments_not_manifest_probes(self):
        now = [100.0]
        t = StreamTelemetry(lambda: now[0])
        root = 'https://media.example/master.m3u8'
        t.manifest('#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1280x720,BANDWIDTH=2000000\nlow.m3u8\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080,BANDWIDTH=5000000\nhigh.m3u8', root)
        for variant in ['low', 'high']:
            t.manifest(f'#EXTM3U\n#EXTINF:4,\n{variant}.ts', f'https://media.example/{variant}.m3u8')
        self.assertNotIn('height', t.snapshot())
        t.delivered(1000000, 'https://media.example/low.ts', complete=True,total=1000000)
        self.assertEqual(t.snapshot()['height'],720)
        self.assertEqual(t.snapshot()['segmentBitrate'],2000000)
        now[0] += 2
        self.assertEqual(t.snapshot()['transferBitrate'],4000000)
        t.delivered(2000000, 'https://media.example/high.ts', complete=True,total=2000000)
        self.assertEqual(t.snapshot()['height'],1080)
        now[0] += 11
        self.assertEqual(t.snapshot()['transferBitrate'],0)
        now[0] += 31
        self.assertIsNone(t.snapshot()['segmentBitrate'])

    def test_audio_does_not_replace_resolution_or_video_rate(self):
        t = StreamTelemetry()
        t.manifest('#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080,BANDWIDTH=5000000\nv.m3u8', 'https://a/master.m3u8')
        t.manifest('#EXTM3U\n#EXTINF:4,\nv.ts','https://a/v.m3u8')
        t.manifest('#EXTM3U\n#EXTINF:4,\na.aac','https://a/a.m3u8')
        t.delivered(100,'https://a/v.ts',complete=True,total=100)
        t.delivered(10,'https://a/a.aac',complete=True,total=10)
        self.assertEqual(t.snapshot()['height'],1080)
        self.assertEqual(t.snapshot()['segmentBitrate'],200)

    def test_unknown_resolution_and_incomplete_segments_are_not_guessed(self):
        t = StreamTelemetry()
        t.manifest('#EXTM3U\n#EXTINF:4,\na.ts','https://a/v.m3u8')
        t.delivered(100,'https://a/a.ts')
        self.assertNotIn('height',t.snapshot())
        self.assertIsNone(t.snapshot()['segmentBitrate'])
