import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
from airplay_flixer.timeline import Timeline, cmtime, seconds
from airplay_flixer.airplay_compat import play_url
from pyatv.protocols.raop.protocols import airplayv2


class ClockTests(unittest.TestCase):
    def test_remote_events_anchor_play_pause_and_jumps(self):
        now = [100.0]
        clock = Timeline(lambda: now[0])
        clock.event({'type': 'playbackState', 'params': {'playbackState': 'playing', 'position': cmtime(30), 'duration': cmtime(600), 'rate': 1.0}})
        now[0] += 5
        self.assertEqual(clock.current(), 35)
        clock.event({'type': 'playbackState', 'name': 'paused'})
        now[0] += 100
        self.assertEqual(clock.current(), 35)
        clock.event({'type': 'notification', 'name': 'timeJumped', 'params': {'position': cmtime(300)}})
        self.assertEqual(clock.current(), 300)
        clock.event({'type': 'notification', 'name': 'timeJumped', 'position': cmtime(310)})
        self.assertEqual(clock.current(), 310)
        clock.event({'type': 'playbackState', 'name': 'playing'})
        now[0] += 500
        self.assertEqual(clock.current(), 600)

    def test_invalid_and_millisecond_positions(self):
        self.assertAlmostEqual(seconds(cmtime(123.456)), 123.456)
        for value in (float('nan'), float('inf'), -1, {'value': 1, 'timescale': 0}):
            self.assertIsNone(seconds(value))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_tv_receives_cmtime_position_and_factory_is_restored(self):
        original = airplayv2.AirPlayV2
        commands = []
        tv = MagicMock()
        async def send(_, command):
            commands.append(command)
        async def stream(*args, **kwargs):
            protocol = airplayv2.AirPlayV2(MagicMock(), MagicMock())
            await protocol._send_url_command({'type': 'insertPlayQueueItem', 'item': {'uuid': 'item', 'Content-Location': 'https://example.test/video'}})
        tv.stream.play_url = stream
        with patch.object(original, '_send_url_command', send):
            await play_url(tv, 'https://example.test/video', position=300)
        self.assertEqual(commands[0]['item']['Start-Position'], cmtime(300))
        self.assertIs(airplayv2.AirPlayV2, original)

class SeekTests(unittest.IsolatedAsyncioTestCase):
    async def test_seek_keeps_airplay_task_and_paused_state(self):
        from airplay_flixer.server import Companion
        companion = Companion()
        companion.atv = MagicMock()
        companion.atv.remote_control.set_position = AsyncMock()
        companion.playback_url = 'http://example.test/movie'
        task = asyncio.create_task(asyncio.Event().wait())
        companion.play_task = task
        companion.timeline.update(position=10, duration=600, state='paused')
        companion.confirmed = True
        try:
            await companion.seek(300)
            companion.atv.remote_control.set_position.assert_awaited_once_with(300)
            self.assertIs(companion.play_task, task)
            self.assertFalse(task.done())
            self.assertTrue(companion.confirmed)
            self.assertEqual(companion.timeline.state,'paused')
            self.assertEqual(companion.timeline.position,300)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_rejected_seek_does_not_restart_or_change_position(self):
        from airplay_flixer.server import Companion
        companion = Companion()
        companion.atv = MagicMock()
        companion.atv.remote_control.set_position = AsyncMock(side_effect=RuntimeError('unsupported'))
        companion.playback_url = 'http://example.test/movie'
        task = asyncio.create_task(asyncio.Event().wait())
        companion.play_task = task
        companion.timeline.update(position=10,duration=600,state='paused')
        try:
            with self.assertRaises(ValueError):
                await companion.seek(300)
            self.assertIs(companion.play_task,task)
            self.assertFalse(task.done())
            self.assertEqual(companion.timeline.position,10)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
