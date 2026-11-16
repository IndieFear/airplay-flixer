import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
from airplay_flixer.dlna import ssdp_location, parse_description, Discovery
from airplay_flixer.server import Companion

XML = b'''<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
<friendlyName>TV Salon</friendlyName><manufacturer>Example</manufacturer>
<modelName>TV 123</modelName><UDN>uuid:salon</UDN><serviceList><service>
<serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType><controlURL>/control</controlURL>
</service></serviceList></device></root>'''

class DiscoveryTests(unittest.TestCase):
    def test_local_reply_and_duplicate_locations(self):
        packet = b'HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.10:9197/device.xml\r\n\r\n'
        self.assertEqual(ssdp_location(packet,'192.168.1.10'),'http://192.168.1.10:9197/device.xml')
        protocol = Discovery()
        for _ in range(3): protocol.datagram_received(packet,('192.168.1.10',1900))
        self.assertEqual(len(protocol.locations),1)
        for location in ['http://127.0.0.1/x','http://8.8.8.8/x','http://192.168.1.11/x','file:///x','http://u:p@192.168.1.10/x']:
            self.assertIsNone(ssdp_location(f'HTTP/1.1 200 OK\r\nLocation: {location}\r\n'.encode(),'192.168.1.10'))

    def test_only_renderers_with_transport_are_shown_and_ids_are_stable(self):
        first = parse_description(XML,'http://192.168.1.10/x','192.168.1.10')[0]
        self.assertEqual(first['name'],'TV Salon')
        self.assertEqual(first['protocol'],'dlna')
        self.assertTrue(first['canCast'])
        self.assertEqual(first['controlURL'], 'http://192.168.1.10/control')
        moved = parse_description(XML,'http://192.168.1.11/x','192.168.1.11')[0]
        self.assertEqual(first['id'],moved['id'])
        self.assertEqual(parse_description(XML.replace(b'MediaRenderer:1',b'MediaServer:1'),'http://a/x','a'),[])
        self.assertEqual(parse_description(XML.replace(b'AVTransport:1',b'Other:1'),'http://a/x','a'),[])
        self.assertEqual(parse_description(b'<!DOCTYPE root>'+XML,'http://a/x','a'),[])
        self.assertEqual(parse_description(b'invalid','http://a/x','a'),[])

class CombinedTests(unittest.IsolatedAsyncioTestCase):
    async def test_dlna_discovery_survives_airplay_failure(self):
        c = Companion()
        device = parse_description(XML,'http://192.168.1.10/x','192.168.1.10')[0]
        with patch('airplay_flixer.server.pyatv.scan',AsyncMock(side_effect=OSError())), patch('airplay_flixer.server.discover_dlna',AsyncMock(return_value=[device])):
            result = await c.scan()
        self.assertEqual(result['devices'],[device])
        self.assertIn('AirPlay',result['discoveryWarning'])
        self.assertIn('1 DLNA',result['message'])

    async def test_unknown_dlna_selection_does_not_stop_existing_playback(self):
        c = Companion()
        c.stop = AsyncMock()
        c.play_task = asyncio.create_task(asyncio.Event().wait())
        try:
            with self.assertRaises(ValueError):
                await c.dispatch({'action':'select','id':'dlna:abc'})
            c.stop.assert_not_awaited()
            self.assertFalse(c.play_task.done())
        finally:
            c.play_task.cancel()
            await asyncio.gather(c.play_task,return_exceptions=True)
