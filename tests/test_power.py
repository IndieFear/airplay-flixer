import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
from airplay_flixer.server import Companion
from airplay_flixer.power import SleepGuard


class PlaybackPowerTests(unittest.IsolatedAsyncioTestCase):
    async def test_kept_during_session_and_released_on_cancellation(self):
        ready = asyncio.Event()
        async def playback():
            ready.set()
            await asyncio.Event().wait()
        with patch('airplay_flixer.server.SleepGuard') as factory:
            task = asyncio.create_task(Companion.protected_playback(None, playback))
            await ready.wait()
            factory.return_value.acquire.assert_called_once()
            factory.return_value.release.assert_not_called()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            factory.return_value.release.assert_called_once()

    async def test_released_on_natural_end_and_playback_error(self):
        for fails in (False, True):
            async def playback():
                if fails:
                    raise ValueError('Playback failed')
                return 'finished'
            with patch('airplay_flixer.server.SleepGuard') as factory:
                if fails:
                    with self.assertRaises(ValueError):
                        await Companion.protected_playback(None, playback)
                else:
                    self.assertEqual(await Companion.protected_playback(None, playback), 'finished')
                factory.return_value.release.assert_called_once()

    async def test_cancelling_before_start_does_not_acquire(self):
        async def playback():
            self.fail('Playback should not start')
        with patch('airplay_flixer.server.SleepGuard') as factory:
            task = asyncio.create_task(Companion.protected_playback(None, playback))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            factory.assert_not_called()


class AssertionTests(unittest.TestCase):
    def test_release_is_idempotent(self):
        guard = SleepGuard()
        io = MagicMock()
        io.IOPMAssertionRelease.return_value = 0
        guard.iokit, guard.identifier = io, 42
        guard.release()
        guard.release()
        io.IOPMAssertionRelease.assert_called_once_with(42)
        self.assertIsNone(guard.identifier)
