"""One local video download, independent of the casting session."""
from __future__ import annotations

import asyncio
import contextlib
from html import unescape
import logging
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

from .dlna_compat import attributes, choose_manifest
from .media import MediaSession, binary, hls_probe_options
from .power import SleepGuard
from .subtitle_offset import CUE, cue_ms, stamp

LOG = logging.getLogger(__name__)
FORMATS = {'hls': '.mp4', 'mp4': '.mp4', 'mkv': '.mkv', 'webm': '.webm'}


def to_srt(text):
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    cues = []
    for block in re.split(r'\n[ \t]*\n', text):
        if block.startswith(('WEBVTT', 'NOTE', 'STYLE', 'REGION')):
            continue
        match = CUE.search(block)
        if not match:
            continue
        body = block[match.end():].strip()
        if not body:
            continue
        if text.startswith('WEBVTT'):
            body = unescape(re.sub(r'<(?!/?[biu]>)[^>]*>', '', body))
        start, end = (stamp(cue_ms(match[key])).replace('.', ',') for key in ('a', 'b'))
        cues.append(f'{len(cues) + 1}\n{start} --> {end}\n{body}')
    if not cues:
        raise ValueError('Aucun sous-titre SRT ou WebVTT exploitable.')
    return '\n\n'.join(cues) + '\n'


def check_playlist(text):
    lines = {line.strip() for line in text.splitlines()}
    if '#EXTM3U' not in lines:
        raise ValueError('Manifeste HLS invalide.')
    for line in lines:
        if line.startswith(('#EXT-X-KEY:', '#EXT-X-SESSION-KEY:')):
            key = attributes(line)
            if key.get('METHOD') not in {'NONE', 'AES-128'} or key.get('KEYFORMAT', 'identity') != 'identity':
                raise ValueError('Ce flux HLS utilise une protection non prise en charge.')
    if not any(line.startswith('#EXT-X-STREAM-INF:') for line in lines) and '#EXT-X-ENDLIST' not in lines:
        raise ValueError('Ce flux est en direct ou encore incomplet. Seules les vidéos à la demande sont téléchargeables.')


class DownloadRelay(MediaSession):
    async def serve(self, request):
        key = request.match_info['tail'].removeprefix('r/')
        if self.resource_types.get(key) != 'playlist':
            return await super().serve(request)
        text, source = await self.fetch_text(self.resources[key])
        check_playlist(text)
        text, _ = choose_manifest(text, source, highest_quality=True)
        return web.Response(text=self.rewrite(text, source), content_type='application/vnd.apple.mpegurl')

    async def upstream(self, *args, **kwargs):
        response = await super().upstream(*args, **kwargs)
        if response.status >= 400:
            self.error = f'Le serveur vidéo refuse une ressource (HTTP {response.status}). Recharge la vidéo puis réessaie.'
        return response


class VideoDownload:
    def __init__(self):
        self.task = None
        self.relay = None
        self.partial = None
        self.state = {'state': 'idle'}

    def status(self):
        with contextlib.suppress(OSError):
            if self.partial:
                self.state['bytes'] = self.partial.stat().st_size
        return dict(self.state)

    def start(self, client, payload, base_url):
        if self.task and not self.task.done():
            raise ValueError('Un téléchargement est déjà en cours.')
        if not isinstance(payload, dict) or payload.get('kind') not in FORMATS:
            raise ValueError('Choisis une source vidéo HLS, MP4, MKV ou WebM.')
        media = DownloadRelay(client, payload, base_url)
        suffix = FORMATS[media.kind]
        if media.kind == 'mp4' and Path(urlsplit(media.source).path).suffix.lower() in {'.mov', '.m4v'}:
            suffix = Path(urlsplit(media.source).path).suffix.lower()
        title = re.sub(r'[\\/:\x00-\x1f\x7f]', ' ', media.title or '').strip('. ')[:60].strip() or 'Vidéo web'
        self.state = {'state': 'running', 'title': title, 'bytes': 0}
        self.task = asyncio.create_task(self.run(media, title, suffix))
        return self.status()

    async def cancel(self):
        task = self.task
        if task and not task.done():
            if not task.cancelling():
                task.cancel()
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            if self.task is task and self.state['state'] == 'running':
                self.state['state'] = 'cancelled'
        return self.status()

    async def serve(self, request):
        media = self.relay
        if request.remote != '127.0.0.1' or not media or request.match_info['session'] != media.id:
            raise web.HTTPNotFound()
        try:
            return await media.serve(request)
        except ValueError as error:
            media.error = str(error)
        except (aiohttp.ClientError, TimeoutError):
            media.error = 'Le serveur vidéo ne répond plus.'
        raise web.HTTPBadGateway(text='Média indisponible.')

    async def run(self, media, title, suffix):
        guard = SleepGuard('Airplay Flixer - téléchargement en cours')
        guard.acquire()
        subtitle_file = None
        try:
            folder = Path.home() / 'Downloads'
            folder.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=folder, prefix='.airplay-flixer-', suffix='.part', delete=False) as temporary:
                self.partial = Path(temporary.name)
            if media.subtitle_tracks:
                subtitle_file = self.partial.with_suffix('.srt')
                try:
                    text = await media.subtitle_text(media.subtitle_tracks[0])
                    subtitle_file.write_text(to_srt(text), encoding='utf-8')
                except (ValueError, aiohttp.ClientError, OSError, TimeoutError):
                    with contextlib.suppress(OSError):
                        subtitle_file.unlink(missing_ok=True)
                    subtitle_file = None
                    self.state['subtitleWarning'] = 'La piste choisie n’a pas pu être enregistrée.'
                finally:
                    media.error = None
            if media.kind == 'hls':
                await self.remux(media)
            else:
                await self.copy(media)
            size = self.partial.stat().st_size
            if not size:
                raise ValueError('Le fichier vidéo reçu est vide.')
            index = 1
            while True:
                stem = title + (f' ({index})' if index > 1 else '')
                name = stem + suffix
                target = folder / name
                try:
                    target.hardlink_to(self.partial)
                except FileExistsError:
                    index += 1
                    continue
                if subtitle_file:
                    try:
                        (folder / (stem + '.srt')).hardlink_to(subtitle_file)
                        self.state['subtitleFilename'] = stem + '.srt'
                    except FileExistsError:
                        target.unlink()
                        index += 1
                        continue
                    except OSError:
                        self.state['subtitleWarning'] = 'La piste choisie n’a pas pu être enregistrée.'
                break
            self.state.update(state='done', filename=name, bytes=size)
        except asyncio.CancelledError:
            self.state['state'] = 'cancelled'
            raise
        except (aiohttp.ClientError, TimeoutError):
            self.state.update(state='error', error='Le transfert a été interrompu. Recharge la vidéo puis réessaie.')
        except PermissionError:
            self.state.update(state='error', error='Autorise Airplay Flixer à écrire dans le dossier Téléchargements dans les réglages de macOS.')
        except ValueError as error:
            self.state.update(state='error', error=str(error))
        except Exception as error:
            LOG.warning('Video download failed: %s', type(error).__name__)
            self.state.update(state='error', error='Impossible d’enregistrer la vidéo. Vérifie l’espace libre dans Téléchargements.')
        finally:
            guard.release()
            self.relay = None
            if subtitle_file:
                with contextlib.suppress(OSError):
                    subtitle_file.unlink(missing_ok=True)
            if self.partial:
                with contextlib.suppress(OSError):
                    self.partial.unlink(missing_ok=True)
                self.partial = None

    async def copy(self, media):
        async with await media.upstream(media.source) as response:
            if response.status != 200:
                raise ValueError(media.error or 'Le serveur ne fournit pas le fichier vidéo complet.')
            mime = response.headers.get('Content-Type', '').split(';')[0].lower()
            if mime and mime not in {'application/octet-stream', 'binary/octet-stream', 'application/mp4'} and not mime.startswith('video/'):
                raise ValueError('Le serveur a renvoyé une réponse qui n’est pas un fichier vidéo.')
            self.state['total'] = response.content_length
            with self.partial.open('wb') as target:
                async for chunk in response.content.iter_chunked(256 * 1024):
                    target.write(chunk)

    async def remux(self, media):
        ffmpeg = binary('ffmpeg')
        if not ffmpeg:
            raise ValueError('FFmpeg est nécessaire pour télécharger une vidéo HLS. Installe-le avec brew install ffmpeg.')
        options = await asyncio.to_thread(hls_probe_options, ffmpeg)
        self.relay = media
        launch = asyncio.create_task(asyncio.create_subprocess_exec(
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror', '-y',
            '-protocol_whitelist', 'http,tcp,crypto', '-f', 'hls', *options,
            '-i', media.resource(media.source, 'playlist'), '-map', '0:v:0', '-map', '0:a:0?',
            '-c', 'copy', '-movflags', '+faststart', '-f', 'mp4', str(self.partial),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        ))
        try:
            process = await asyncio.shield(launch)
            await process.wait()
        except asyncio.CancelledError:
            process = await launch
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            raise
        if process.returncode or media.error:
            raise ValueError(media.error or 'Impossible d’assembler ce flux HLS en MP4. Essaie une autre source vidéo.')
