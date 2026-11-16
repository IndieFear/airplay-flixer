import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import xml.etree.ElementTree as ET
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'companion'))
from airplay_flixer.dlna import DlnaPlayer, DlnaError, metadata, time_value, time_target, parse_description
from airplay_flixer.server import Companion


class MetadataTests(unittest.TestCase):
    def test_xml_escapes_titles_and_urls_and_parses_times(self):
        root = ET.fromstring(metadata('http://example.test/v?a=1&b=2', 'Movie < & >', 'video/mp4', 300))
        values = {node.tag.split('}')[-1]: node.text for node in root.iter()}
        self.assertEqual(values['title'],'Movie < & >')
        self.assertEqual(values['res'],'http://example.test/v?a=1&b=2')
        self.assertEqual(time_value('01:02:03.5'),3723.5)
        self.assertIsNone(time_value('NOT_IMPLEMENTED'))
        self.assertEqual(time_target(3723.9),'01:02:03')

    def test_control_url_cannot_point_outside_the_discovered_device(self):
        from test_dlna import XML
        for control in ['http://8.8.8.8/control', 'http://192.168.1.20/control', 'file:///etc/passwd']:
            body = XML.replace(b'/control',control.encode())
            self.assertEqual(parse_description(body,'http://192.168.1.10/description','192.168.1.10'),[])


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        self.calls = []
        self.state = 'STOPPED'
        self.uri = None
        self.position = '00:00:00'
        self.fail_seek = False
        self.fail_hls = False
        tv_app = web.Application()
        tv_app.router.add_post('/control',self.soap)
        self.tv = TestServer(tv_app)
        await self.tv.start_server()
        self.device = {'id':'dlna:test','name':'Fake TV','address':'127.0.0.1','protocol':'dlna','canCast':True,'pairingRequired':False,'controlURL':str(self.tv.make_url('/control')),'serviceType':'urn:schemas-upnp-org:service:AVTransport:1'}
        self.validation = patch('airplay_flixer.dlna.local_location', lambda url,address: url.startswith(str(self.tv.make_url('/'))))
        self.validation.start()
        self.c = Companion()
        self.c.folder = Path(self.temp.name)
        self.c.client = self.client
        self.c.dlna_devices = [self.device]
        self.c.select('dlna:test')
        relay_app = web.Application()
        relay_app.router.add_get('/media/{session}/{tail:.*}',self.c.relay)
        self.relay = TestServer(relay_app)
        await self.relay.start_server()
        self.c.media_port = self.relay.port
        source_app = web.Application()
        async def movie(_):
            return web.Response(body=b'test-video',content_type='video/mp4')
        source_app.router.add_get('/movie.mp4',movie)
        self.source = TestServer(source_app)
        await self.source.start_server()

    async def asyncTearDown(self):
        await self.c.stop()
        await self.source.close()
        await self.relay.close()
        await self.tv.close()
        await self.client.close()
        self.validation.stop()
        self.temp.cleanup()

    async def soap(self,request):
        action = request.headers['SOAPAction'].strip('"').split('#')[-1]
        body = ET.fromstring(await request.read())
        values = {node.tag.split('}')[-1]:node.text for node in body.iter()}
        self.calls.append((action,values))
        service = self.device['serviceType']
        response = {}
        if action == 'SetAVTransportURI':
            if self.fail_hls and values['CurrentURI'].endswith('.m3u8'):
                return web.Response(status=500,text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault><detail><UPnPError><errorCode>714</errorCode></UPnPError></detail></s:Fault></s:Body></s:Envelope>')
            self.uri = values['CurrentURI']
            ET.fromstring(values['CurrentURIMetaData'])
        elif action == 'Play': self.state = 'PLAYING'
        elif action == 'Pause': self.state = 'PAUSED_PLAYBACK'
        elif action == 'Stop': self.state = 'STOPPED'
        elif action == 'Seek':
            if self.fail_seek:
                return web.Response(status=500,text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault><detail><UPnPError><errorCode>710</errorCode></UPnPError></detail></s:Fault></s:Body></s:Envelope>')
            self.position=values['Target']
        elif action == 'GetTransportInfo': response={'CurrentTransportState':self.state,'CurrentTransportStatus':'OK'}
        elif action == 'GetPositionInfo': response={'RelTime':self.position,'TrackDuration':'00:10:00'}
        xml = f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:{action}Response xmlns:u="{service}">' + ''.join(f'<{key}>{value}</{key}>' for key,value in response.items()) + f'</u:{action}Response></s:Body></s:Envelope>'
        return web.Response(text=xml,content_type='text/xml')

    async def cast(self):
        result = await self.c.dispatch({'action':'cast','media':{'url':str(self.source.make_url('/movie.mp4')),'kind':'mp4','title':'Movie & test','duration':600,'subtitle':{'url':'https://subs.example/fr.vtt'}}})
        for _ in range(100):
            if self.c.confirmed: break
            await asyncio.sleep(.01)
        return result

    async def test_load_relay_pause_seek_resume_stop_without_reload(self):
        result = await self.cast()
        self.assertTrue(result['casting'])
        self.assertEqual(result['selectedProtocol'],'dlna')
        self.assertIsNone(self.c.atv)
        self.assertIn('external subtitles',result['playbackWarning'])
        self.assertTrue(self.c.confirmed)
        async with self.client.get(self.uri) as response:
            self.assertEqual(await response.read(),b'test-video')
        task=self.c.play_task
        await self.c.dispatch({'action':'toggle'})
        self.assertEqual(self.state,'PAUSED_PLAYBACK')
        await self.c.dispatch({'action':'seek','position':300})
        self.assertEqual(self.position,'00:05:00')
        self.assertIs(self.c.play_task,task)
        await self.c.dispatch({'action':'toggle'})
        self.assertEqual(self.state,'PLAYING')
        await self.c.dispatch({'action':'stop'})
        self.assertEqual(self.state,'STOPPED')
        self.assertFalse(self.c.status()['casting'])
        self.assertIsNone(self.c.media)
        self.assertEqual([a for a,_ in self.calls].count('SetAVTransportURI'),1)

    async def test_rejected_seek_keeps_video_and_disables_timeline(self):
        await self.cast()
        self.fail_seek=True
        task=self.c.play_task
        with self.assertRaises(DlnaError):
            await self.c.seek(200)
        self.assertIs(self.c.play_task,task)
        self.assertFalse(task.done())
        self.assertFalse(self.c.status()['seekable'])
        self.assertEqual([a for a,_ in self.calls].count('SetAVTransportURI'),1)

    async def test_hls_rejection_switches_automatically_to_compatible_transport(self):
        self.fail_hls = True
        async def compatible(media, loopback, mime, position=0):
            media.compat_mime=mime
            media.compat_offset=position
            media.compat_info={'width':1920,'height':1080}
            return media.base+'/compatible.ts'
        with patch('airplay_flixer.media.MediaSession.prepare',AsyncMock(return_value='http://127.0.0.1/master.m3u8')), patch('airplay_flixer.media.MediaSession.prepare_dlna_compatible',compatible):
            result=await self.c.cast_dlna({'url':str(self.source.make_url('/movie.mp4')),'kind':'hls','position':120,'duration':600})
        self.assertTrue(result['compatibleMode'])
        self.assertFalse(result['seekable'])
        self.assertEqual(self.c.media.compat_offset,120)
        self.assertIn('DLNA compatibility mode',result['playbackWarning'])
        loads=[v['CurrentURI'] for a,v in self.calls if a=='SetAVTransportURI']
        self.assertEqual(len(loads),2)
        self.assertTrue(loads[-1].endswith('/compatible.ts'))
        with self.assertRaises(ValueError):await self.c.seek(300)

    async def test_announced_formats_skip_unsupported_hls_before_loading(self):
        async def compatible(media, loopback, mime, position=0):
            media.compat_mime=mime
            media.compat_info={'width':1280,'height':720}
            return media.base+'/compatible.ts'
        with patch('airplay_flixer.dlna.DlnaPlayer.formats',AsyncMock(return_value={'video/mp2t','video/mp4'})), patch('airplay_flixer.media.MediaSession.prepare_dlna_compatible',compatible), patch('airplay_flixer.media.MediaSession.prepare',AsyncMock()) as plain:
            await self.c.cast_dlna({'url':str(self.source.make_url('/movie.mp4')),'kind':'hls','duration':600})
            plain.assert_not_awaited()
        loads=[v for a,v in self.calls if a=='SetAVTransportURI']
        self.assertEqual(len(loads),1)
        self.assertIn('video/mp2t',loads[0]['CurrentURIMetaData'])
