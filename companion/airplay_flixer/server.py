from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hmac
import json
import logging
import math
import os
import secrets
import signal
import socket
import ssl
import time

import aiohttp
import certifi
from aiohttp import web
import pyatv
from pyatv.const import DeviceState, PairingRequirement, Protocol
from pyatv.storage.file_storage import FileStorage

from urllib.parse import urlsplit

from .opensubtitles import OpenSubtitles
from .subdl import SubDL
from .video_download import VideoDownload
from .power import SleepGuard
from . import VERSION
from .config import data_directory, read_json, write_json
from .media import MediaSession, to_webvtt
from .airplay_compat import play_url
from .timeline import Timeline, seconds
from .pairing import matching_settings, clear_credentials
from .subtitle_offset import offset_value
from .autosync import analyse as analyse_subtitles, log_result as log_auto_sync_result
from .dlna import discover as discover_dlna, DlnaPlayer, DlnaError, time_value

LOG = logging.getLogger(__name__)


class Companion:
    def __init__(self):
        self.folder = data_directory()
        self.opensubtitles = OpenSubtitles(self.folder)
        self.subdl = SubDL(self.folder)
        self.download = VideoDownload()
        self.settings = read_json(self.folder / 'preferences.json')
        self.devices = {}
        self.dlna = None
        self.playback_warning = None
        self.dlna_devices = []
        self.discovery_warning = None
        self.storage = FileStorage(str(self.folder / 'credentials.json'), asyncio.get_running_loop())
        self.client = None
        self.atv = None
        self.pairing = None
        self.play_task = None
        self.media = None
        self.received = asyncio.Event()
        self.confirmed = False
        self.message = 'Ready to cast'
        self.error = None
        self.title = None
        self.token = secrets.token_urlsafe(32)
        self.media_port = 0
        self.control_port = 0
        self.local_ip = '127.0.0.1'
        self.action_lock = asyncio.Lock()
        self.timeline = Timeline()
        self.playback_url = None
        self.auto_task = None
        self.auto_sync = {"state":"idle"}

    async def start(self):
        await self.storage.load()
        self.client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_read=45), trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
        )

    async def scan(self):
        self.message = 'Searching for AirPlay and DLNA devices…'
        airplay, dlna = await asyncio.gather(
            pyatv.scan(asyncio.get_running_loop(), timeout=5, storage=self.storage),
            discover_dlna(self.client), return_exceptions=True)
        warnings = []
        if isinstance(airplay, BaseException):
            if isinstance(airplay, asyncio.CancelledError):
                raise airplay
            warnings.append('AirPlay search unavailable.')
        else:
            self.devices = {config.identifier or str(config.address): config for config in airplay if config.get_service(Protocol.AirPlay)}
        if isinstance(dlna, BaseException):
            if isinstance(dlna, asyncio.CancelledError):
                raise dlna
            warnings.append('DLNA search unavailable.')
            self.dlna_devices = []
        else:
            self.dlna_devices = dlna
        self.discovery_warning = ' '.join(warnings) or None
        count = len(self.devices) + len(self.dlna_devices)
        self.message = f'{len(self.devices)} AirPlay · {len(self.dlna_devices)} DLNA' if count else 'No devices found. Check the local network and DLNA sharing on the TV.'
        self.error = None
        return self.status()

    def selected_identifiers(self):
        selected = self.settings.get('selected')
        ids = {selected} if isinstance(selected, str) and selected else set()
        ids.update(i for i in self.settings.get('selectedIdentifiers', []) if isinstance(i, str))
        config = self.devices.get(selected)
        if config:
            ids.update(i for i in config.all_identifiers if isinstance(i, str))
        return ids

    def selected_paired(self):
        if str(self.settings.get('selected', '')).startswith('dlna:'):
            return False
        config = self.devices.get(self.settings.get('selected'))
        if config and config.get_service(Protocol.AirPlay).credentials:
            return True
        return any(item.protocols.airplay.credentials for item in matching_settings(self.storage, self.selected_identifiers()))

    async def pair_forget(self, device_id):
        if not device_id or device_id != self.settings.get('selected') or str(device_id).startswith('dlna:'):
            raise ValueError('First select the Apple TV to forget in Settings.')
        ids = self.selected_identifiers()
        if self.pairing:
            await self.pairing.close()
            self.pairing = None
        await self.stop()
        for item in matching_settings(self.storage, ids):
            clear_credentials(item)
        # Cached scan configurations also carry secrets. Clear all aliases of this
        # receiver so a later reconnect cannot restore credentials from memory.
        for key, config in self.devices.items():
            if key == device_id or ids.intersection(config.all_identifiers):
                for service in config.services:
                    service.credentials = None
                    service.password = None
        await self.storage.save()
        path = self.folder / 'credentials.json'
        if path.exists():
            path.chmod(0o600)
        self.error = None
        self.message = 'Local pairing removed. You can pair this Apple TV again.'
        return self.status()

    def status(self):
        selected = self.settings.get('selected')
        return {
            'version': VERSION, 'message': self.message, 'error': self.error,
            'discoveryWarning': self.discovery_warning,
            'playbackWarning': self.playback_warning, 'selectedProtocol': self.settings.get('selectedProtocol', 'airplay'),
            'pauseable': self.dlna.pauseable if self.dlna else True,
            'compatibleMode': bool(self.dlna and self.media and self.media.compat_mime),
            'selected': selected, 'selectedName': self.settings.get('selectedName'),
            'selectedPaired': bool(self.selected_paired()),
            'devices': [{'id': key, 'name': config.name, 'address': str(config.address), 'paired': bool(config.get_service(Protocol.AirPlay).credentials), 'pairingRequired': config.get_service(Protocol.AirPlay).pairing != PairingRequirement.NotNeeded, 'protocol': 'airplay', 'canCast': True} for key, config in self.devices.items()] + self.dlna_devices,
            'pairing': self.pairing is not None, 'casting': self.play_task is not None and not self.play_task.done(),
            'confirmed': self.confirmed, 'title': self.title,
            'subtitleSession': self.media.id if self.media and self.atv else None,
            'autoSync': dict(self.auto_sync),
            'opensubtitles': self.opensubtitles.status(),
            'subdl': self.subdl.status(),
            'videoDownload': self.download.status(),
            'videoDownloadSubtitles': True,
            'subtitleImportable': bool(self.atv and self.media and (self.media.directory or self.media.kind == 'hls' or '.m3u8' in urlsplit(self.media.source).path.lower())),
            'subtitleTracks': [{'index':i, 'label':t.get('label') or f'Track {i+1}', 'language':t.get('language','und'), 'offset':t.get('offset',0), 'default':i == self.media.default_subtitle} for i,t in enumerate(self.media.subtitle_tracks)] if self.media and self.atv else [],
            **self.timeline.snapshot(),
            'stream': self.media.telemetry.snapshot() if self.media and self.play_task and not self.play_task.done() else None,
            'seekable': bool(self.play_task and not self.play_task.done() and self.timeline.duration > 0 and (not self.dlna or self.dlna.seekable) and not (self.dlna and self.media and self.media.compat_mime)),
        }

    async def configuration(self):
        key = self.settings.get('selected')
        if key not in self.devices and self.settings.get('address'):
            configs = await pyatv.scan(asyncio.get_running_loop(), timeout=3, hosts=[self.settings['address']], storage=self.storage)
            self.devices.update({c.identifier or str(c.address): c for c in configs if c.get_service(Protocol.AirPlay)})
        if key not in self.devices:
            await self.scan()
        if key not in self.devices:
            if len(self.devices) == 1 and not key:
                self.select(next(iter(self.devices)))
                key = self.settings['selected']
            else:
                raise ValueError('Select your Apple TV in the menu bar, then pair it.')
        self.settings['address'] = str(self.devices[key].address)
        self.settings['selectedName'] = self.devices[key].name
        return self.devices[key]

    def select(self, key):
        if isinstance(key, str) and key.startswith('dlna:'):
            device = next((d for d in self.dlna_devices if d['id'] == key), None)
            if not device:
                raise ValueError('DLNA device not found. Scan again.')
            self.settings.update(selected=key, selectedName=device['name'], address=device['address'], selectedProtocol='dlna', selectedIdentifiers=[])
            write_json(self.folder / 'preferences.json', self.settings)
            self.error = None
            self.message = 'DLNA destination ' + device['name']
            return
        if key not in self.devices:
            raise ValueError('Apple TV not found. Scan again.')
        config = self.devices[key]
        self.settings.update(selected=key, selectedName=config.name, address=str(config.address), selectedProtocol='airplay', selectedIdentifiers=[i for i in config.all_identifiers if isinstance(i, str)])
        write_json(self.folder / 'preferences.json', self.settings)
        self.error = None
        self.message = f'Apple TV : {config.name}'

    async def pair_begin(self):
        if str(self.settings.get("selected", "")).startswith("dlna:"):
            raise ValueError("DLNA devices do not require AirPlay pairing.")
        self.error = None
        if self.pairing:
            await self.pairing.close()
            self.pairing = None
        config = await self.configuration()
        service = config.get_service(Protocol.AirPlay)
        if service.pairing == PairingRequirement.NotNeeded:
            self.message = 'This Apple TV does not require an AirPlay code'
            return {'pinRequired': False}
        self.pairing = await pyatv.pair(config, Protocol.AirPlay, asyncio.get_running_loop(), storage=self.storage, name='Airplay Flixer')
        try:
            await self.pairing.begin()
        except Exception:
            await self.pairing.close()
            self.pairing = None
            raise
        self.message = 'Enter the code displayed on the Apple TV'
        return {'pinRequired': True}

    async def pair_finish(self, pin):
        if not self.pairing or not isinstance(pin, str) or not pin.isdigit() or len(pin) != 4:
            raise ValueError('Enter the 4-digit code displayed on the Apple TV.')
        pairing = self.pairing
        try:
            pairing.pin(int(pin))
            await pairing.finish()
            if not pairing.has_paired:
                raise ValueError('Pairing rejected. Check the code and try again.')
            await self.storage.save()
            path = self.folder / 'credentials.json'
            if path.exists():
                path.chmod(0o600)
            self.message = 'Apple TV paired and ready to cast'
            self.error = None
        finally:
            await pairing.close()
            self.pairing = None

    async def stop(self, strict=False):
        await self.cancel_auto_sync(clear=True)
        if self.dlna:
            try:
                await self.dlna.command('Stop')
            except Exception:
                if strict:
                    raise
            # Once Stop is accepted, finish polling before resetting the clock.
            if self.play_task:
                self.play_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self.play_task
                self.play_task = None
            self.dlna = None
        self.playback_warning = None
        if self.atv:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.atv.remote_control.stop(), 3)
        if self.play_task:
            self.play_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self.play_task, 4)
            self.play_task = None
        if self.atv:
            tasks = self.atv.close()
            if tasks:
                await asyncio.wait(tasks, timeout=3)
            self.atv = None
        if self.media:
            await self.media.close()
            self.media = None
        self.confirmed = False
        self.title = None
        self.message = 'Ready to cast'
        self.playback_url = None
        self.timeline = Timeline()

    async def cast(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Invalid media.')
        if str(self.settings.get('selected', '')).startswith('dlna:'):
            return await self.cast_dlna(payload)
        config = await self.configuration()
        service = config.get_service(Protocol.AirPlay)
        if not service.credentials and service.pairing in {PairingRequirement.Mandatory, PairingRequirement.Optional}:
            raise ValueError('First pair this Apple TV from the Mac menu bar.')
        await self.stop()
        self.message = 'Preparing media…'
        self.error = None
        self.received.clear()
        self.title = str(payload.get('title') or 'Web video')[:200]
        route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            route.connect((str(config.address), 7000))
            local_ip = route.getsockname()[0]
            self.local_ip = local_ip
        finally:
            route.close()
        self.media = MediaSession(self.client, payload, f'http://{local_ip}:{self.media_port}')
        try:
            url = await self.media.prepare(f'http://127.0.0.1:{self.media_port}')
            await self.media.describe(url)
            self.atv = await asyncio.wait_for(pyatv.connect(config, asyncio.get_running_loop(), storage=self.storage), 15)
            position = float(payload.get('position') or 0)
            if not math.isfinite(position) or not 0 <= position <= 86400:
                raise ValueError('Invalid playback position.')
            self.playback_url = url
            self.timeline.update(position=position, duration=self.media.duration, state='loading')
            self.play_task = asyncio.create_task(self.protected_playback(self.play, url, position))
            self.message = 'Connecting to AirPlay…'
            return self.status()
        except Exception:
            await self.stop()
            raise

    async def cast_dlna(self, payload):
        key = self.settings.get('selected')
        device = next((d for d in self.dlna_devices if d['id'] == key), None)
        if not device:
            self.dlna_devices = await discover_dlna(self.client)
            device = next((d for d in self.dlna_devices if d['id'] == key), None)
        if not device:
            raise ValueError('DLNA TV not found. Turn it on and scan again.')
        position = seconds(payload.get('position') or 0)
        if position is None or position > 86400:
            raise ValueError('Invalid playback position.')
        if payload.get('kind') == 'dash':
            raise ValueError('DASH is not supported over DLNA. Choose an MP4 or HLS source.')
        await self.stop()
        self.settings.update(address=device['address'], selectedName=device['name'])
        self.message = 'Preparing DLNA playback…'
        self.error = None
        self.title = str(payload.get('title') or 'Web video')[:200]
        self.received.clear()
        route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            route.connect((device['address'], 1900))
            self.local_ip = route.getsockname()[0]
        finally:
            route.close()
        # Generic DLNA does not define interoperable external subtitle loading.
        # Keep native tracks in the stream and explain the external-track limit.
        subtitle = payload.get('subtitle')
        if subtitle and not subtitle.get('off'):
            self.playback_warning = 'DLNA does not transmit the selected external subtitles. Use embedded tracks on the TV.'
        if subtitle and subtitle.get('off'):
            self.playback_warning = 'DLNA. Turn off embedded subtitles in the TV player if needed.'
        self.media = MediaSession(self.client, {**payload, 'subtitle': None, 'subtitles': [], 'targetProtocol': 'dlna'}, f'http://{self.local_ip}:{self.media_port}')
        self.dlna = DlnaPlayer(self.client, device)
        try:
            loopback = f'http://127.0.0.1:{self.media_port}'
            formats = await self.dlna.formats()
            hls = self.media.kind == 'hls' or '.m3u8' in urlsplit(self.media.source).path.lower()
            compatible = hls and bool(formats) and not formats.intersection({'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl', 'audio/x-mpegurl'})
            ts_mime = 'video/mp2t' if 'video/mp2t' in formats else 'video/vnd.dlna.mpeg-tts' if 'video/vnd.dlna.mpeg-tts' in formats else 'video/mpeg' if 'video/mpeg' in formats else 'video/mp2t'
            if compatible:
                url = await self.media.prepare_dlna_compatible(loopback, ts_mime, position)
                mime = ts_mime
            else:
                url = await self.media.prepare(loopback)
                mime = 'application/vnd.apple.mpegurl' if url.endswith('.m3u8') else {'mp4': 'video/mp4', 'mkv': 'video/x-matroska', 'webm': 'video/webm'}.get(self.media.kind, 'video/mp4')
            try:
                await self.dlna.load(url, self.title, mime, max(0, self.media.duration - (position if compatible else 0)))
            except DlnaError as error:
                if not hls or compatible or error.code not in {'704', '714'}:
                    raise
                # Some receivers advertise generic support yet reject playlists.
                compatible = True
                url = await self.media.prepare_dlna_compatible(loopback, ts_mime, position)
                mime = ts_mime
                await self.dlna.load(url, self.title, mime, max(0, self.media.duration - position))
            if compatible:
                height = self.media.compat_info.get('height')
                quality = f' {height}p' if height else ''
                notice = f'DLNA compatibility mode. H.264{quality} + AAC in an MPEG-TS stream. Seeking is unavailable during this cast.'
                self.playback_warning = notice + (' ' + self.playback_warning if self.playback_warning else '')
            self.playback_url = url
            self.timeline.update(position=position if compatible else 0, duration=self.media.duration, state='loading')
            self.play_task = asyncio.create_task(self.protected_playback(self.monitor_dlna, 0 if compatible else position))
            self.message = 'Loading on ' + device['name']
            return self.status()
        except BaseException:
            await self.stop()
            raise

    async def monitor_dlna(self, initial_position=0):
        player = self.dlna
        started = time.monotonic()
        failures = 0
        try:
            while self.dlna is player:
                try:
                    info = await player.command('GetTransportInfo')
                    remote_state = info.get('CurrentTransportState')
                    if info.get('CurrentTransportStatus') == 'ERROR_OCCURRED':
                        raise ValueError('The TV cannot play this DLNA stream. Try an MP4 source.')
                    if remote_state in {'PLAYING', 'PAUSED_PLAYBACK'}:
                        self.confirmed = True
                        self.timeline.update(state='paused' if remote_state == 'PAUSED_PLAYBACK' else 'playing')
                        self.message = ('Paused on ' if remote_state == 'PAUSED_PLAYBACK' else 'Playing on ') + player.device['name']
                        if initial_position:
                            try:
                                await player.seek(initial_position)
                                self.timeline.update(position=initial_position)
                            except ValueError:
                                self.playback_warning = 'The TV cannot resume at the browser position. Playing from the beginning.'
                            initial_position = 0
                    elif remote_state in {'STOPPED', 'NO_MEDIA_PRESENT'}:
                        if self.confirmed:
                            self.message = 'Playback ended'
                            break
                        if time.monotonic() - started > 25:
                            raise ValueError('The TV did not start this DLNA stream. Try an MP4 source. This TV may reject HLS.')
                    if not self.confirmed and time.monotonic() - started > 35:
                        raise ValueError('DLNA loading timed out. Try another video source.')
                    try:
                        position = await player.command('GetPositionInfo')
                        remote_position = time_value(position.get('RelTime'))
                        remote_duration = time_value(position.get('TrackDuration'))
                        offset = self.media.compat_offset if self.media else 0
                        self.timeline.update(position=remote_position + offset if remote_position is not None else None, duration=remote_duration + offset if remote_duration else None)
                    except ValueError:
                        pass
                    failures = 0
                except ValueError as error:
                    failures += 1
                    if failures >= 3:
                        self.error = str(error)
                        self.message = 'DLNA playback interrupted'
                        break
                await asyncio.sleep(2)
        finally:
            self.confirmed = False
            self.timeline.update(state='stopped')

    async def protected_playback(self, playback, *args, **kwargs):
        # Own one assertion per session task. Cancellation, errors and natural
        # completion all release it; macOS also releases it if the process exits.
        guard = SleepGuard()
        guard.acquire()
        try:
            return await playback(*args, **kwargs)
        finally:
            guard.release()

    async def play(self, url, position, paused=False):
        pauser = asyncio.create_task(self.pause_after_reload(self.timeline)) if paused else None
        try:
            # play_url must remain alive for the whole movie: do NOT await it in
            # the short-lived Chrome Native Messaging process or RPC handler.
            await play_url(self.atv, url, position=position, timeline=self.timeline, title=self.title, artwork=self.media.artwork if self.media else None)
            self.message = 'Playback ended'
        except asyncio.CancelledError:
            raise
        except Exception as error:
            import re
            detail = re.sub(r'https?://\S+', '[media]', str(error))[:300]
            LOG.warning('AirPlay failed: %s (%s)', type(error).__name__, detail)
            self.error = 'Apple TV rejected or interrupted playback. Check the source and pairing.'
            self.message = 'Playback interrupted'
        finally:
            await self.cancel_auto_sync()
            if pauser:
                pauser.cancel()
                await asyncio.gather(pauser, return_exceptions=True)
            self.confirmed = False
            self.timeline.update(state='stopped')

    async def pause_after_reload(self, timeline):
        for _ in range(200):
            if self.timeline is not timeline:
                return
            if timeline.state == 'playing':
                # Let pyatv observe the initial playing event before pausing.
                await asyncio.sleep(1)
                if self.timeline is not timeline or not self.atv:
                    return
                try:
                    if timeline.controller:
                        response = await timeline.controller._send_url_command({'type':'setRate','rate':0.0})
                        if not 200 <= response.code < 300:
                            raise ValueError('Pause rejected')
                    else:
                        await self.atv.remote_control.pause()
                    timeline.update(state='paused')
                except Exception:
                    self.playback_warning = 'The offset was applied. Automatic pause failed. Pause the video if needed.'
                return
            await asyncio.sleep(.1)

    async def cancel_auto_sync(self, clear=False):
        if self.auto_task and not self.auto_task.done():
            self.auto_task.cancel()
            await asyncio.gather(self.auto_task,return_exceptions=True)
        self.auto_task = None
        if clear:
            self.auto_sync = {'state':'idle'}
        elif self.auto_sync.get('state') == 'running':
            self.auto_sync = {**self.auto_sync, 'state':'cancelled', 'message':'Analysis cancelled.'}

    async def start_auto_sync(self, session, index):
        if not self.atv or not self.media or not self.play_task or self.play_task.done():
            raise ValueError('Start casting to Apple TV with external subtitles before analyzing.')
        if session != self.media.id or isinstance(index,bool) or not isinstance(index,int) or not 0 <= index < len(self.media.subtitle_tracks):
            raise ValueError('The track or casting session changed. Refresh the controller.')
        await self.cancel_auto_sync(clear=True)
        media = self.media
        identifier = secrets.token_urlsafe(8)
        self.auto_sync = {'id':identifier,'session':session,'index':index,'state':'running','phase':'subtitle','message':'Preparing local analysis…'}
        self.auto_task = asyncio.create_task(self.run_auto_sync(media,index,identifier,self.timeline.current()))
        return self.status()

    async def run_auto_sync(self, media, index, identifier, position):
        def current():
            return self.media is media and self.auto_sync.get('id') == identifier
        def progress(phase,message):
            if current():self.auto_sync.update(phase=phase,message=message)
        try:
            result = await analyse_subtitles(media,index,position,f'http://127.0.0.1:{self.media_port}',progress)
            log_auto_sync_result(result)
            if current():
                if not self.play_task or self.play_task.done():
                    self.auto_sync.update(state='cancelled',message='The cast ended. Result not applied.')
                else:
                    self.auto_sync.update(result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOG.warning('Subtitle auto-sync failed: %s',type(error).__name__)
            if current():self.auto_sync.update(state='failed',message=str(error) if isinstance(error,ValueError) else 'Automatic analysis failed. Manual adjustment is available.')

    async def import_subtitle(self, session, identifier, provider="opensubtitles"):
        if not self.play_task or self.play_task.done() or not self.atv or not self.media or self.media.id != session:
            raise ValueError('The cast changed. Select the subtitle again.')
        media = self.media
        if not self.status()['subtitleImportable']:
            raise ValueError('This source needs to be cast again to add subtitles. The track is ready in the extension for the next cast.')
        service = self.subdl if provider == 'subdl' else self.opensubtitles
        loaded = service.downloads.get(identifier)
        if not loaded:
            raise ValueError('This track is no longer cached. Download it again.')
        track = loaded['track']
        index = next((i for i,t in enumerate(media.subtitle_tracks) if t.get('provider') == provider and t.get('fileId') == track['fileId']), None)
        if index is None:
            if len(media.subtitle_tracks)>=500:
                raise ValueError('The track limit for this cast has been reached.')
            timestamp = media.subtitle_timestamp
            if not media.subtitle_tracks:
                local = f'http://127.0.0.1:{self.media_port}/media/{media.id}'
                timing_url = local+'/file/video.m3u8' if media.directory else media.resource(media.source,'playlist').replace(media.base,local,1)
                timestamp = await media.timestamp(timing_url)
            vtt = to_webvtt(track['content'],timestamp)
            index = len(media.subtitle_tracks)
            media.subtitle_tracks.append({**track,'offset':0})
            media.subtitle_timestamp = timestamp
            media.cache_subtitle(index,vtt)
            if index == media.default_subtitle:
                # The first added track must go through the explicit reload too.
                media.default_subtitle = -1
        return await self.sync_subtitle(session,index,0)

    async def sync_subtitle(self, session, index, offset):
        offset = offset_value(offset)
        if not self.atv or not self.media or not self.play_task or self.play_task.done():
            raise ValueError('No active Apple TV cast with external subtitles.')
        media = self.media
        if session != media.id:
            raise ValueError('The cast changed. Refresh the controller before adjusting subtitles.')
        if isinstance(index,bool) or not isinstance(index,int) or not 0 <= index < len(media.subtitle_tracks):
            raise ValueError('Choose an external subtitle track to adjust.')
        if offset == media.subtitle_tracks[index].get('offset',0) and index == media.default_subtitle:
            return self.status()
        await self.cancel_auto_sync()
        # Fetch/validate before touching the active AirPlay session.
        raw = await media.subtitle_vtt(index)
        position = self.timeline.current()
        paused = self.timeline.state == 'paused'
        self.play_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(self.play_task,10)
        media.subtitle_tracks[index]['offset'] = offset
        media.default_subtitle = index
        media.vtt = raw
        media.subtitle_revision += 1
        self.timeline.started = False
        self.timeline.update(position=position,state='loading')
        self.confirmed = False
        self.error = None
        self.message = 'Applying subtitle offset…'
        self.playback_url = f'{media.base}/master.m3u8?subrev={media.subtitle_revision}'
        self.play_task = asyncio.create_task(self.protected_playback(self.play, self.playback_url, position, paused=paused))
        return self.status()

    async def seek(self, position):
        target = seconds(position)
        if target is None:
            raise ValueError('Invalid playback position.')
        if not (self.atv or self.dlna) or not self.playback_url or not self.play_task or self.play_task.done():
            raise ValueError('No AirPlay playback in progress.')
        if self.dlna and self.media and self.media.compat_mime:
            raise ValueError('Seeking is unavailable in the live DLNA compatibility stream.')
        if not self.timeline.duration:
            raise ValueError('The media duration is still unknown.')
        target = min(target, max(0, self.timeline.duration - 0.5))
        if self.media and self.media.directory:
            import re
            playlist = self.media.directory / 'video.m3u8'
            prepared = sum(float(n) for n in re.findall(r'#EXTINF:([\d.]+)', playlist.read_text())) if playlist.exists() else 0
            if target > prepared:
                raise ValueError('This part of the media is still being prepared. Try again shortly.')
        # Seek through the active remote session. Recreating play_url tears down
        # AirPlay and visibly reopens the player for every timeline click.
        paused = self.timeline.state == 'paused'
        try:
            if self.dlna:
                await self.dlna.seek(target)
            else:
                await asyncio.wait_for(self.atv.remote_control.set_position(target), 5)
        except Exception as error:
            LOG.warning('Seek rejected: %s', type(error).__name__)
            if self.dlna and isinstance(error, ValueError):
                raise
            raise ValueError('Seeking was rejected. Check the Apple TV remote pairing.') from error
        self.timeline.update(position=target, state='paused' if paused else 'playing')
        self.message = 'Playback position updated'
        self.error = None
        return self.status()

    async def poll(self):
        while True:
            await asyncio.sleep(2)
            if self.dlna and self.play_task and not self.play_task.done():
                if self.media and self.media.error:
                    self.error = self.media.error
                continue
            if self.atv and self.play_task and not self.play_task.done():
                if self.received.is_set():
                    self.message = 'Stream sent to Apple TV'
                    try:
                        playing = await asyncio.wait_for(self.atv.metadata.playing(), 2)
                        if playing.device_state == DeviceState.Playing:
                            self.confirmed = True
                            self.message = 'Playing on ' + self.settings.get('selectedName', 'Apple TV')
                    except Exception:
                        pass
                if self.timeline.started:
                    self.confirmed = True
                    self.message = ('Paused on ' if self.timeline.state == 'paused' else 'Playing on ') + self.settings.get('selectedName', 'Apple TV')
                if self.media and self.media.error:
                    self.error = self.media.error
            elif self.media and not self.action_lock.locked() and time.monotonic() - self.media.last_access > 60:
                await self.stop()

    async def dispatch(self, message):
        action = message.get('action')
        if action == 'status':
            return self.status()
        if action == 'video_download_start':
            return self.download.start(self.client, message.get('media'), f'http://127.0.0.1:{self.control_port}')
        if action == 'video_download_cancel':
            return await self.download.cancel()
        if action == 'sd_configure':
            return await self.subdl.configure(self.client,message)
        if action == 'sd_search':
            return await self.subdl.search(self.client,message)
        if action == 'sd_download':
            return await self.subdl.download(self.client,message.get('fileId'))
        if action == 'os_configure':
            return await self.opensubtitles.configure(self.client,message)
        if action == 'os_search':
            return await self.opensubtitles.search(self.client,message)
        if action == 'os_download':
            return await self.opensubtitles.download(self.client,message.get('fileId'))
        async with self.action_lock:
            if action == 'scan':
                return await self.scan()
            if action == 'select':
                key = message.get('id')
                if key not in self.devices and not any(d['id'] == key for d in self.dlna_devices):
                    raise ValueError('Device not found. Scan again.')
                if self.play_task and not self.play_task.done():
                    await self.stop()
                self.select(message.get('id'))
            elif action == 'pair_forget':
                return await self.pair_forget(message.get('id'))
            elif action == 'pair_begin':
                return await self.pair_begin()
            elif action == 'pair_finish':
                await self.pair_finish(message.get('pin'))
            elif action == 'pair_cancel':
                if self.pairing:
                    await self.pairing.close()
                    self.pairing = None
            elif action == 'cast':
                return await self.cast(message.get('media'))
            elif action == 'stop':
                await self.stop(strict=True)
            elif action == 'subtitle_auto_start':
                return await self.start_auto_sync(message.get('session'),message.get('index'))
            elif action == 'subtitle_auto_cancel':
                await self.cancel_auto_sync()
                return self.status()
            elif action == 'sd_apply':
                return await self.import_subtitle(message.get('session'),message.get('id'),'subdl')
            elif action == 'os_apply':
                return await self.import_subtitle(message.get('session'),message.get('id'))
            elif action == 'subtitle_sync':
                return await self.sync_subtitle(message.get('session'), message.get('index'), message.get('offset'))
            elif action == 'seek':
                return await self.seek(message.get('position'))
            elif action == 'toggle':
                if self.dlna:
                    paused = self.timeline.state == 'paused'
                    await self.dlna.toggle(paused)
                    self.timeline.update(state='playing' if paused else 'paused')
                    return self.status()
                if not self.atv:
                    raise ValueError('No AirPlay playback in progress.')
                if self.timeline.controller:
                    paused = self.timeline.state != 'paused'
                    response = await self.timeline.controller._send_url_command({'type': 'setRate', 'rate': 0.0 if paused else 1.0})
                    if not 200 <= response.code < 300:
                        raise ValueError('Apple TV rejected the Play / Pause command.')
                    self.timeline.update(state='paused' if paused else 'playing')
                else:
                    await self.atv.remote_control.play_pause()
            else:
                raise ValueError('Unknown command.')
            return self.status()

    async def rpc(self, request):
        if not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + self.token):
            raise web.HTTPForbidden()
        # Control is a loopback-only API, with no CORS or browser origins.
        if request.headers.get('Origin'):
            raise web.HTTPForbidden()
        message = {}
        try:
            message = await request.json()
            if not isinstance(message, dict):
                raise ValueError('Invalid command.')
            result = await asyncio.wait_for(self.dispatch(message), 85)
            return web.json_response({'ok': True, 'result': result})
        except Exception as error:
            LOG.warning('Command failed: %s', type(error).__name__)
            safe = str(error) if isinstance(error, ValueError) else {
                'AuthenticationError': 'Pairing rejected. Pair the Apple TV again.',
                'PairingError': 'Pairing failed. Select “Pair Apple TV” again and use the new code.',
                'NotSupportedError': 'Apple TV does not support this command or format.',
                'TimeoutError': 'Apple TV is not responding. Check that it is on and connected to the same network.',
            }.get(type(error).__name__, 'Cannot connect. Check the local network and Apple TV pairing.')
            action = str(message.get('action', '')) if isinstance(message, dict) else ''
            isolated = action.startswith(('os_', 'sd_', 'video_download_'))
            if isolated and not isinstance(error, ValueError):
                safe = 'Cannot manage the video download.' if action.startswith('video_download_') else 'The subtitle catalog request failed. Try again or check its configuration in Settings.'
            if not isolated:
                self.error = safe
            return web.json_response({'ok': False, 'error': safe})

    async def relay(self, request):
        if not self.media or request.match_info['session'] != self.media.id:
            raise web.HTTPNotFound()
        if request.remote not in {'127.0.0.1', '::1', self.local_ip, self.settings.get('address')}:
            raise web.HTTPForbidden()
        if request.remote == self.settings.get('address') and request.method == 'GET':
            self.received.set()
        try:
            return await self.media.serve(request)
        except ValueError as error:
            if request.remote not in {'127.0.0.1','::1'}:
                self.error = str(error)
            raise web.HTTPBadGateway(text='Media unavailable.') from None
        except (aiohttp.ClientError, TimeoutError):
            if request.remote not in {'127.0.0.1','::1'}:
                self.error = 'The video server stopped responding. Restart the video in Chrome.'
            raise web.HTTPBadGateway(text='Media unavailable.') from None


async def serve():
    folder = data_directory()
    lock = (folder / 'engine.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    companion = Companion()
    await companion.start()
    control = web.Application(client_max_size=1024 * 1024)
    control.router.add_post('/rpc', companion.rpc)
    control.router.add_get('/media/{session}/{tail:.*}', companion.download.serve)
    media = web.Application()
    media.router.add_get('/media/{session}/{tail:.*}', companion.relay)
    async def dlna_headers(request, response):
        if companion.dlna:
            response.headers['transferMode.dlna.org'] = 'Streaming'
            if request.headers.get('getcontentFeatures.dlna.org') == '1':
                byte_seek = response.headers.get('Accept-Ranges', '').lower() == 'bytes'
                response.headers['contentFeatures.dlna.org'] = 'DLNA.ORG_OP=' + ('01' if byte_seek else '00') + ';DLNA.ORG_CI=0'
    media.on_response_prepare.append(dlna_headers)
    control_runner = web.AppRunner(control, access_log=None)
    media_runner = web.AppRunner(media, access_log=None)
    await control_runner.setup()
    await media_runner.setup()
    await web.TCPSite(media_runner, '0.0.0.0', 0).start()
    companion.media_port = media_runner.addresses[0][1]
    await web.TCPSite(control_runner, '127.0.0.1', 0).start()
    companion.control_port = control_runner.addresses[0][1]
    write_json(folder / 'runtime.json', {'port': companion.control_port, 'token': companion.token, 'pid': os.getpid()})
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    poller = asyncio.create_task(companion.poll())
    try:
        await stop.wait()
    finally:
        poller.cancel()
        await companion.download.cancel()
        await companion.stop()
        if companion.pairing:
            await companion.pairing.close()
        await companion.client.close()
        await control_runner.cleanup()
        await media_runner.cleanup()
        (folder / 'runtime.json').unlink(missing_ok=True)
        lock.close()


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(serve())
