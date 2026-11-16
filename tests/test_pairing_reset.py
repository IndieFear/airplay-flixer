import asyncio
from ipaddress import IPv4Address
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
from pyatv.conf import AppleTV, ManualService
from pyatv.const import Protocol
from pyatv.storage.file_storage import FileStorage
from airplay_flixer.server import Companion


def device(name, identifier, address):
    config = AppleTV(IPv4Address(address),name)
    config.add_service(ManualService(identifier,Protocol.AirPlay,7000,{},credentials='dummy-'+identifier))
    config.add_service(ManualService(identifier+'-raop',Protocol.RAOP,7000,{},credentials='dummy-audio'))
    return config


class PairingResetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict('os.environ',{'AIRPLAY_FLIXER_HOME':self.temp.name})
        self.env.start()
        self.c=Companion()
        self.first=device('Salon','salon','192.168.1.10')
        self.second=device('Chambre','chambre','192.168.1.11')
        self.c.devices={'salon':self.first,'chambre':self.second}
        await self.c.storage.update_settings(self.first)
        await self.c.storage.update_settings(self.second)
        await self.c.storage.save()
        self.c.select('salon')

    async def asyncTearDown(self):
        await self.c.stop()
        self.env.stop();self.temp.cleanup()

    async def test_offline_status_uses_saved_pairing_without_network_or_secrets(self):
        self.c.devices={}
        status=self.c.status()
        self.assertTrue(status['selectedPaired'])
        self.assertEqual(status['selectedName'],'Salon')
        self.assertNotIn('dummy-',str(status))

    async def test_forget_only_selected_receiver_persists_and_preserves_default(self):
        result=await self.c.pair_forget('salon')
        self.assertFalse(result['selectedPaired'])
        self.assertEqual(result['selected'],'salon')
        self.assertIsNone(self.first.get_service(Protocol.AirPlay).credentials)
        self.assertIsNone(self.first.get_service(Protocol.RAOP).credentials)
        self.assertEqual(self.second.get_service(Protocol.AirPlay).credentials,'dummy-chambre')
        loaded=FileStorage(str(Path(self.temp.name)/'credentials.json'),asyncio.get_running_loop())
        await loaded.load()
        self.assertIsNone((await loaded.get_settings(self.first)).protocols.airplay.credentials)
        self.assertIsNone((await loaded.get_settings(self.first)).protocols.raop.credentials)
        self.assertEqual((await loaded.get_settings(self.second)).protocols.airplay.credentials,'dummy-chambre')
        self.assertEqual((Path(self.temp.name)/'credentials.json').stat().st_mode & 0o777,0o600)
        self.c.devices={}
        self.assertFalse(self.c.status()['selectedPaired'])

    async def test_forget_stops_selected_playback_and_cancels_pending_pairing(self):
        self.c.atv=MagicMock()
        self.c.atv.close.return_value=set()
        stopped=self.c.atv.remote_control.stop=AsyncMock()
        self.c.play_task=asyncio.create_task(asyncio.Event().wait())
        pending=MagicMock();pending.close=AsyncMock();self.c.pairing=pending
        result=await self.c.pair_forget('salon')
        stopped.assert_awaited_once()
        pending.close.assert_awaited_once()
        self.assertFalse(result['casting'])
        self.assertIsNone(self.c.pairing)

    async def test_stale_confirmation_or_dlna_id_cannot_forget_another_device(self):
        self.c.stop=AsyncMock()
        for target in [None,'chambre','dlna:tv']:
            with self.assertRaises(ValueError):await self.c.pair_forget(target)
        self.c.stop.assert_not_awaited()
        self.assertTrue(self.c.status()['selectedPaired'])
