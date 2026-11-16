"""Per-playback media relay: URLs and browser credentials never enter logs."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
from functools import lru_cache
import json
import logging
import math
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp import web

from .config import origin, web_url
from .telemetry import StreamTelemetry
from .dlna_compat import choose_manifest
from .subtitle_tracks import with_subtitle_tracks
from .now_playing import clean_title, with_title, decode_artwork
from .subtitle_offset import offset_value, shift_webvtt

URI = re.compile(r'URI="([^"]+)"')
TIMING = re.compile(r'(\d{1,2}:\d{2}:\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2})[,.](\d{3})')
MAX_TEXT = 4 * 1024 * 1024
MAX_SUBTITLE_CACHE = 32 * 1024 * 1024
LOG = logging.getLogger(__name__)


async def read_text_body(response):
    data = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        data.extend(chunk)
        if len(data) > MAX_TEXT:
            raise ValueError('Manifest or subtitles too large.')
    return bytes(data).decode('utf-8-sig')


def binary(name: str) -> str | None:
    return shutil.which(name) or next((str(p) for p in (Path('/opt/homebrew/bin') / name, Path('/usr/local/bin') / name) if p.exists()), None)


@lru_cache(maxsize=2)
def hls_probe_options(probe: str) -> tuple[str, ...]:
    # The relay accepts only resources discovered in the selected manifest.
    # Their opaque URLs need not carry the upstream file extension.
    options = ['-allowed_extensions', 'ALL']
    supported = subprocess.run([probe, '-hide_banner', '-h', 'demuxer=hls'], capture_output=True, text=True, timeout=5, check=True).stdout
    if '-extension_picky' in supported:
        options += ['-extension_picky', '0']
    if '-allowed_segment_extensions' in supported:
        options += ['-allowed_segment_extensions', 'ALL']
    return tuple(options)


def to_webvtt(text: str, timestamp: int) -> str:
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    if not text.startswith('WEBVTT'):
        if not TIMING.search(text):
            raise ValueError('Subtitles not recognized. Use an SRT or WebVTT track.')
        text = TIMING.sub(lambda m: f'{m[1]}.{m[2]} --> {m[3]}.{m[4]}', text)
        text = 'WEBVTT\n\n' + text.strip() + '\n'
    if 'X-TIMESTAMP-MAP=' not in text:
        header, _, body = text.partition('\n')
        text = f'{header}\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:{timestamp % (1 << 33)}\n{body}'
    return text


def with_subtitles(playlist: str, subtitle_url: str | None, label='French', language='fr', off=False) -> str:
    lines = playlist.splitlines()
    if not subtitle_url and not off:
        return playlist
    # Replace existing groups when an explicit external track or "off" is selected.
    lines = [line for line in lines if not (line.startswith('#EXT-X-MEDIA:') and 'TYPE=SUBTITLES' in line)]
    lines = [re.sub(r',?SUBTITLES="[^"]*"', '', line).replace(':,', ':').rstrip(',') if line.startswith('#EXT-X-STREAM-INF:') else line for line in lines]
    if subtitle_url:
        safe_label = re.sub(r'["\r\n]', '', label)[:80] or 'Subtitles'
        safe_language = re.sub(r'[^a-zA-Z0-9-]', '', language)[:20] or 'und'
        lines.insert(1, f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="flixer-subs",NAME="{safe_label}",LANGUAGE="{safe_language}",DEFAULT=YES,AUTOSELECT=YES,FORCED=NO,URI="{subtitle_url}"')
        lines = [line + ',SUBTITLES="flixer-subs"' if line.startswith('#EXT-X-STREAM-INF:') else line for line in lines]
    return '\n'.join(lines) + '\n'


class MediaSession:
    def __init__(self, client: aiohttp.ClientSession, payload: dict, base_url: str):
        self.client = client
        self.source = web_url(payload.get('url'))
        self.title = clean_title(payload.get('title'))
        self.artwork = decode_artwork(payload.get('artwork'))
        self.kind = payload.get('kind', '')
        self.target_protocol = payload.get('targetProtocol', 'airplay')
        self.subtitle = payload.get('subtitle')
        candidates = payload.get('subtitles')
        if candidates is not None and (not isinstance(candidates, list) or len(candidates) > 500):
            raise ValueError('Invalid or oversized subtitle list.')
        self.subtitle_tracks = []
        if not (isinstance(self.subtitle, dict) and self.subtitle.get('off')):
            for track in candidates or ([self.subtitle] if isinstance(self.subtitle, dict) else []):
                if not isinstance(track, dict) or not (track.get('url') or track.get('content')):
                    continue
                if track.get('url') and any(t.get('url') == track['url'] for t in self.subtitle_tracks):
                    continue
                self.subtitle_tracks.append({**track, "offset": offset_value(track.get("offset", 0))})
        self.default_subtitle = next((i for i,t in enumerate(self.subtitle_tracks) if t.get('default')), 0)
        self.vtt_tracks = {}
        self.subtitle_cache_sizes = {}
        self.subtitle_timestamp = 0
        self.subtitle_revision = 0
        self.subtitle_tasks = {}
        self.subtitle_semaphore = asyncio.Semaphore(3)
        self.id = secrets.token_urlsafe(24)
        self.base = f'{base_url}/media/{self.id}'
        self.telemetry = StreamTelemetry()
        self.resources: dict[str, str] = {}
        self.resource_types: dict[str, str] = {}
        self.header_map = {}
        self.duration = max(0, min(float(payload.get('duration') or 0), 86400))
        self.directory: Path | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.monitor: asyncio.Task | None = None
        self.last_access = time.monotonic()
        self.error: str | None = None
        self.vtt = ''
        self.root = self.source
        self.compat_directory = None
        self.compat_process = None
        self.compat_monitor = None
        self.compat_input = None
        self.compat_input_source = None
        self.compat_mime = None
        self.compat_info = {}
        self.compat_offset = 0
        for item_origin, values in list((payload.get('headersByOrigin') or {}).items())[:40]:
            try:
                host = origin(item_origin)
            except ValueError:
                continue
            if not isinstance(values, dict):
                continue
            allowed = {'referer', 'origin', 'user-agent', 'cookie', 'authorization'}
            self.header_map[host] = {str(k): str(v) for k, v in values.items() if str(k).lower() in allowed and len(str(v)) <= 16384 and '\r' not in str(v) and '\n' not in str(v)}

    def resource(self, url: str, role: str = '') -> str:
        url = web_url(url)
        key = hashlib.sha256(url.encode()).hexdigest()[:32]
        extension = Path(urlsplit(url).path).suffix.lower()
        if extension in {'.m3u8', '.ts', '.m4s', '.mp4', '.m4v', '.mov', '.aac', '.ac3', '.vtt', '.key'}:
            key += extension
        elif role:
            key += {'playlist': '.m3u8', 'segment': '.ts', 'fragment': '.m4s', 'init': '.mp4', 'key': '.key'}.get(role, '')
        self.resources[key] = url
        self.resource_types[key] = role
        return f'{self.base}/r/{key}'

    async def describe(self, url: str):
        if len(self.subtitle_tracks) > 1:
            return  # Do not make a diagnostic probe download every rendition.
        probe = binary('ffprobe')
        if not probe:
            return
        process = await asyncio.create_subprocess_exec(probe, '-v', 'error', *hls_probe_options(probe), '-read_intervals', '%+#1', '-show_entries', 'stream=codec_type,codec_name,profile,pix_fmt:format=format_name', '-of', 'json', url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 15)
            data = json.loads(output)
            LOG.warning('Media inspection: kind=%s result=%s streams=%s', self.kind, process.returncode, json.dumps(data.get('streams', [])))
            if self.kind == 'hls':
                leaf, actual = await self.media_playlist(self.source)
                first = next((line.strip() for line in leaf.splitlines() if line.strip() and not line.startswith('#')), None)
                if first:
                    response = await self.upstream(urljoin(actual, first), extra_headers={'Range': 'bytes=0-511'})
                    try:
                        sample = await response.content.read(512)
                        LOG.warning('HLS segment inspection: HTTP=%s MIME=%s prefix=%s fragmented=%s', response.status, response.headers.get('Content-Type', '').split(';')[0], sample[:8].hex(), '#EXT-X-MAP:' in leaf)
                    finally:
                        response.close()
        except (ValueError, TimeoutError, aiohttp.ClientError):
            if process.returncode is None:
                process.kill()
                await process.wait()
            LOG.warning('Media inspection unavailable: kind=%s', self.kind)

    async def upstream(self, url: str, method='GET', extra_headers=None):
        for _ in range(6):
            headers = dict(self.header_map.get(origin(url), {}))
            # Referer/Origin may be needed on a separate segment CDN. Cookies and
            # Authorization are only forwarded to their own captured origin.
            for key, value in self.header_map.get(origin(self.source), {}).items():
                if key.lower() in {'referer', 'origin', 'user-agent'}:
                    headers.setdefault(key, value)
            headers.update(extra_headers or {})
            headers['Accept-Encoding'] = 'identity'
            response = await self.client.request(method, url, headers=headers, allow_redirects=False, auto_decompress=False)
            if response.status not in {301, 302, 303, 307, 308}:
                return response
            target = response.headers.get('Location')
            response.release()
            if not target:
                raise ValueError('Invalid media redirect.')
            url = web_url(urljoin(url, target))
        raise ValueError('Too many media redirects.')

    async def fetch_text(self, url: str):
        response = await self.upstream(url)
        try:
            if response.status >= 400:
                raise ValueError(f'The media server refused playback (HTTP {response.status}). Restart the video in Chrome.')
            return await read_text_body(response), str(response.url)
        finally:
            response.close()

    def rewrite(self, text: str, source: str):
        if re.search(r'KEYFORMAT="(?!identity)[^"]+"|METHOD=SAMPLE-AES', text):
            raise ValueError('This stream uses DRM protection unsupported by this relay.')
        self.telemetry.manifest(text, source)
        output = []
        master = '#EXT-X-STREAM-INF:' in text
        segment_role = 'fragment' if '#EXT-X-MAP:' in text else 'segment'
        for line in text.splitlines():
            if line.startswith('#'):
                role = 'key' if line.startswith(('#EXT-X-KEY:', '#EXT-X-SESSION-KEY:')) else 'init' if line.startswith('#EXT-X-MAP:') else segment_role if line.startswith('#EXT-X-PART:') else 'playlist'
                line = URI.sub(lambda match: 'URI="' + self.resource(urljoin(source, match[1]), role) + '"', line)
            elif line.strip():
                line = self.resource(urljoin(source, line.strip()), 'playlist' if master else segment_role)
            output.append(line)
        return '\n'.join(output) + '\n'

    async def media_playlist(self, url: str):
        for _ in range(5):
            text, actual_url = await self.fetch_text(url)
            if not text.lstrip().startswith('#EXTM3U'):
                raise ValueError('Invalid HLS manifest.')
            if '#EXT-X-STREAM-INF:' not in text:
                return text, actual_url
            selected, _ = choose_manifest(text, actual_url)
            variants = [line.strip() for line in selected.splitlines() if line.strip() and not line.startswith('#')]
            if not variants:
                raise ValueError('No video variant in the manifest.')
            url = urljoin(actual_url, variants[-1])
        raise ValueError('HLS manifest nesting is too deep.')

    async def timestamp(self, local_url: str) -> int:
        probe = binary('ffprobe')
        if not probe:
            raise ValueError('FFmpeg is required to sync external subtitles. Install it with brew install ffmpeg.')
        if self.kind == 'hls' and not self.directory:
            # A multivariant probe can encounter WebVTT/audio before any video
            # timestamp. Probe one actual video playlist, without subtitle groups.
            _, actual = await self.media_playlist(self.source)
            local = urlsplit(local_url)
            loopback = f'{local.scheme}://{local.netloc}/media/{self.id}'
            local_url = self.resource(actual, 'playlist').replace(self.base, loopback, 1)
        proc = await asyncio.create_subprocess_exec(probe, '-v', 'error', *hls_probe_options(probe), '-select_streams', 'v:0', '-read_intervals', '%+#32', '-show_entries', 'stream=start_time:packet=pts_time,dts_time:format=start_time', '-of', 'json', local_url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
            data = json.loads(output)
            def finite(value):
                try:
                    number = float(value)
                    return number if math.isfinite(number) else None
                except (TypeError, ValueError):
                    return None
            starts = [n for stream in data.get('streams', []) if (n := finite(stream.get('start_time'))) is not None]
            packets = [n for packet in data.get('packets', []) if (n := finite(packet.get('pts_time'))) is not None]
            fallback = finite(data.get('format', {}).get('start_time'))
            start = min(starts) if starts else min(packets) if packets else fallback if data.get('streams') else None
            if proc.returncode or start is None:
                LOG.warning('Subtitle timing unavailable: result=%s videoStreams=%s packetTimes=%s', proc.returncode, len(data.get('streams', [])), len(packets))
                raise ValueError('Video timestamp unavailable.')
            return round(start * 90000)
        except (TimeoutError, ValueError, KeyError) as error:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            LOG.warning('Subtitle timing failed: %s', type(error).__name__)
            raise ValueError('Cannot read the video timestamp to sync external subtitles. Try another source or an embedded track.') from error

    async def prepare(self, loopback_base: str):
        if self.kind == 'dash':
            raise ValueError('DASH streams are not supported yet. Choose an HLS or MP4 source.')
        external = bool(self.subtitle_tracks)
        is_hls = self.kind == 'hls' or '.m3u8' in urlsplit(self.source).path.lower()
        if not is_hls and (external or self.kind in {'mkv', 'webm'}) and self.target_protocol != 'dlna':
            await self.start_remux(loopback_base)
            is_hls = True
        if not is_hls:
            return self.resource(self.source)
        if self.directory:
            text = (self.directory / 'video.m3u8').read_text()
        else:
            text, _ = await self.media_playlist(self.source)
        if self.subtitle and self.subtitle.get('off'):
            external = False
        if external:
            if '#EXT-X-DISCONTINUITY' in text:
                raise ValueError('External subtitles are not supported on streams with timeline discontinuities. Choose an embedded track or turn subtitles off.')
            track = self.subtitle_tracks[self.default_subtitle]
            subtitle_text = await self.subtitle_text(track)
            self.duration = max(self.duration, sum(float(n) for n in re.findall(r'#EXTINF:([\d.]+)', text)))
            local = f'{loopback_base}/media/{self.id}/' + ('file/video.m3u8' if self.directory else 'r/' + self.resource(self.source).rsplit('/', 1)[-1])
            self.subtitle_timestamp = 0 if len(self.subtitle_tracks) == 1 and 'X-TIMESTAMP-MAP=' in subtitle_text else await self.timestamp(local)
            self.vtt = to_webvtt(subtitle_text, self.subtitle_timestamp)
            self.cache_subtitle(self.default_subtitle, self.vtt)
            for hours, minutes, seconds in re.findall(r'-->\s*(?:(\d+):)?(\d{2}):(\d{2}(?:\.\d+)?)', self.vtt):
                self.duration = max(self.duration, int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds))
        return f'{self.base}/master.m3u8'

    async def subtitle_text(self, track):
        text = track.get('content')
        if not text:
            text, _ = await asyncio.wait_for(self.fetch_text(web_url(track['url'])), 12)
        if not isinstance(text, str) or len(text.encode()) > MAX_TEXT:
            raise ValueError('Subtitle track too large or invalid.')
        return text

    def cache_subtitle(self, index, text):
        size = len(text.encode())
        total = sum(self.subtitle_cache_sizes.values()) - self.subtitle_cache_sizes.get(index, 0)
        for old in list(self.vtt_tracks):
            if total + size <= MAX_SUBTITLE_CACHE:
                break
            if old not in {index, self.default_subtitle}:
                total -= self.subtitle_cache_sizes.pop(old, 0)
                self.vtt_tracks.pop(old, None)
        self.vtt_tracks[index] = text
        self.subtitle_cache_sizes[index] = size

    async def subtitle_vtt(self, index):
        if index in self.vtt_tracks:
            return self.vtt_tracks[index]
        async def load():
            async with self.subtitle_semaphore:
                text = await self.subtitle_text(self.subtitle_tracks[index])
                self.cache_subtitle(index, to_webvtt(text, self.subtitle_timestamp))
                return self.vtt_tracks[index]
        if index not in self.subtitle_tasks:
            self.subtitle_tasks[index] = asyncio.create_task(load())
        task = self.subtitle_tasks[index]
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self.subtitle_tasks.pop(index, None)

    def subtitle_playlist(self, segment):
        duration = max(1, self.duration)
        return f'#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:{math.ceil(duration)}\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:{duration:.3f},\n{segment}\n#EXT-X-ENDLIST\n'

    async def prepare_dlna_compatible(self, loopback_base, mime, position=0):
        ffmpeg, probe = binary('ffmpeg'), binary('ffprobe')
        if not ffmpeg or not probe:
            raise ValueError('This TV requires FFmpeg to play this HLS stream. Install it with brew install ffmpeg.')
        text, actual = await self.fetch_text(self.source)
        self.compat_input, self.compat_info = choose_manifest(text, actual)
        self.compat_input_source = actual
        local_url = f'{loopback_base}/media/{self.id}/compat-input.m3u8'
        proc = await asyncio.create_subprocess_exec(probe, '-v', 'error', *hls_probe_options(probe), '-read_intervals', '%+#1', '-show_entries', 'stream=codec_type,codec_name,width,height,pix_fmt', '-of', 'json', local_url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), 15)
            streams = json.loads(output).get('streams', [])
            video = next((s for s in streams if s.get('codec_type') == 'video'), None)
            if proc.returncode or not video:
                raise ValueError('Cannot prepare the HLS video for the TV. Restart the player and choose the master stream.')
        except BaseException:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        copy = video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p' and 0 < video.get('height', 0) <= 1080
        self.compat_directory = Path(tempfile.mkdtemp(prefix='airplay-flixer-dlna-'))
        self.compat_offset = position
        self.compat_mime = mime
        path = self.compat_directory / 'video.ts'
        options = ['-c:v', 'copy'] if copy else ['-vf', "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2", '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-crf', '22']
        if copy:
            self.compat_info = {'width': video.get('width'), 'height': video.get('height')}
        else:
            # The actual scaled dimensions are only exposed if known.
            w, h = video.get('width', 0), video.get('height', 0)
            if w and h:
                ratio = min(1, 1920 / w, 1080 / h)
                self.compat_info = {'width': int(w * ratio) // 2 * 2, 'height': int(h * ratio) // 2 * 2}
        self.compat_process = await asyncio.create_subprocess_exec(
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            *hls_probe_options(probe), *(['-ss', str(position)] if position else []), '-i', local_url,
            '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn', *options,
            '-c:a', 'aac', '-ac', '2', '-ar', '48000', '-b:a', '160k',
            '-f', 'mpegts', '-mpegts_flags', '+resend_headers', '-muxdelay', '0',
            *(['-mpegts_m2ts_mode', '1'] if mime == 'video/vnd.dlna.mpeg-tts' else []),
            str(path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        self.compat_monitor = asyncio.create_task(self.watch_compatible())
        for _ in range(250):
            size = path.stat().st_size if path.exists() else 0
            if size >= 65536 or size and self.compat_process.returncode == 0:
                return f'{self.base}/compatible.ts'
            if self.compat_process.returncode is not None:
                raise ValueError('Could not prepare a compatible DLNA stream. Choose a master with video and audio.')
            await asyncio.sleep(.1)
        raise ValueError('The compatible DLNA stream is not ready. Check the video source and try again.')

    async def watch_compatible(self):
        while self.compat_process and self.compat_process.returncode is None:
            path = self.compat_directory / 'video.ts'
            if path.exists() and path.stat().st_size > 6 * 1024**3:
                self.error = 'The temporary DLNA cache has reached its 6 GB limit.'
                self.compat_process.terminate()
                return
            await asyncio.sleep(1)
        if self.compat_process and self.compat_process.returncode:
            self.error = self.error or 'DLNA stream preparation stopped. Start casting again.'

    async def serve_compatible(self, request):
        path = self.compat_directory / 'video.ts'
        complete = self.compat_process.returncode == 0
        if self.compat_process.returncode not in {None, 0} or self.error:
            raise web.HTTPBadGateway(text='DLNA stream unavailable.')
        headers = {'Content-Type': self.compat_mime, 'Accept-Ranges': 'bytes' if complete else 'none', 'Cache-Control': 'no-store'}
        start, remaining, status = 0, None, 200
        if complete:
            size = path.stat().st_size
            end = size
            if 'Range' in request.headers:
                try:
                    bounds = request.http_range
                    start = bounds.start or 0
                    if start < 0:
                        start = max(0, size + start)
                    end = min(size, bounds.stop if bounds.stop is not None else size)
                    if start >= size or end <= start:
                        raise ValueError()
                except ValueError:
                    raise web.HTTPRequestRangeNotSatisfiable(headers={'Content-Range': f'bytes */{size}'}) from None
                status = 206
                headers['Content-Range'] = f'bytes {start}-{end-1}/{size}'
            remaining = end - start
            headers['Content-Length'] = str(remaining)
        # No invented length or byte ranges while the file is still growing.
        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)
        if request.method == 'HEAD':
            return response
        with path.open('rb') as source:
            source.seek(start)
            while remaining is None or remaining > 0:
                chunk = source.read(min(128 * 1024, remaining) if remaining is not None else 128 * 1024)
                if chunk:
                    try:
                        await response.write(chunk)
                    except ConnectionError:
                        return response
                    if remaining is not None:
                        remaining -= len(chunk)
                    self.telemetry.current = self.compat_info
                    self.telemetry.last_video = time.monotonic()
                    self.telemetry.delivered(len(chunk), '')
                elif self.compat_process.returncode is not None:
                    break
                else:
                    await asyncio.sleep(.1)
        with contextlib.suppress(ConnectionError):
            await response.write_eof()
        return response

    async def start_remux(self, loopback_base: str):
        ffmpeg = binary('ffmpeg')
        if not ffmpeg:
            raise ValueError('This format requires FFmpeg. Install it with brew install ffmpeg.')
        self.directory = Path(tempfile.mkdtemp(prefix='airplay-flixer-'))
        url = self.resource(self.source).replace(self.base, f'{loopback_base}/media/{self.id}', 1)
        self.process = await asyncio.create_subprocess_exec(
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', url,
            '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-f', 'hls', '-hls_time', '6', '-hls_list_size', '0', '-hls_playlist_type', 'event',
            '-hls_flags', 'temp_file', '-hls_segment_filename', str(self.directory / 'segment-%06d.ts'),
            str(self.directory / 'video.m3u8'), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self.monitor = asyncio.create_task(self.watch_remux())
        for _ in range(150):
            if (self.directory / 'video.m3u8').exists():
                return
            if self.process.returncode is not None:
                raise ValueError('Cannot convert the container. Try another HLS or MP4 source.')
            await asyncio.sleep(0.2)
        raise ValueError('Media preparation timed out.')

    async def watch_remux(self):
        while self.process and self.process.returncode is None:
            if self.directory and sum(p.stat().st_size for p in self.directory.glob('*') if p.is_file()) > 6 * 1024**3:
                self.error = 'The temporary cache has reached its 6 GB limit.'
                self.process.terminate()
                return
            await asyncio.sleep(5)

    async def master(self):
        if self.directory:
            playlist = '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=12000000\n' + f'{self.base}/file/video.m3u8\n'
        else:
            text, actual_url = await self.fetch_text(self.source)
            if '#EXT-X-STREAM-INF:' in text:
                playlist = self.rewrite(text, actual_url)
            else:
                playlist = '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=12000000\n' + self.resource(self.source) + '\n'
        if self.target_protocol == 'airplay':
            playlist = with_title(playlist, self.title, f'{self.base}/artwork.jpg' if self.artwork else None)
        if self.subtitle_tracks:
            tracks = [{**track, 'url': f'{self.base}/subtitles/{i}.m3u8?rev={self.subtitle_revision}', 'default': i == self.default_subtitle} for i,track in enumerate(self.subtitle_tracks)]
            return with_subtitle_tracks(playlist, tracks)
        return with_subtitles(playlist, None, off=bool((self.subtitle or {}).get('off')))

    async def serve(self, request: web.Request):
        self.last_access = time.monotonic()
        tail = request.match_info['tail']
        if tail == 'compat-input.m3u8' and self.compat_input:
            if request.remote not in {'127.0.0.1', '::1'}:
                raise web.HTTPForbidden()
            return web.Response(text=self.rewrite(self.compat_input, self.compat_input_source), content_type='application/vnd.apple.mpegurl')
        if tail == 'compatible.ts' and self.compat_directory:
            return await self.serve_compatible(request)
        if tail == 'artwork.jpg' and self.artwork:
            return web.Response(body=self.artwork, content_type='image/jpeg', headers={'Cache-Control':'private, max-age=3600'})
        if tail == 'master.m3u8':
            return web.Response(text=await self.master(), content_type='application/vnd.apple.mpegurl')
        match = re.fullmatch(r'subtitles/(\d+)\.(m3u8|vtt)', tail)
        if match:
            index = int(match[1])
            if index >= len(self.subtitle_tracks):
                raise web.HTTPNotFound()
            try:
                vtt = await self.subtitle_vtt(index)
            except (ValueError, TimeoutError, aiohttp.ClientError):
                LOG.warning('Subtitle rendition unavailable: track=%s', index)
                raise web.HTTPNotFound(text='Subtitle track unavailable.') from None
            if match[2] == 'vtt':
                return web.Response(text=shift_webvtt(vtt, self.subtitle_tracks[index]['offset']), content_type='text/vtt', headers={'Cache-Control':'no-store'})
            return web.Response(text=self.subtitle_playlist(f'{self.base}/subtitles/{index}.vtt?rev={self.subtitle_revision}'), content_type='application/vnd.apple.mpegurl')
        if tail == 'subtitles.m3u8' and self.vtt:
            return web.Response(text=self.subtitle_playlist(f'{self.base}/subtitles.vtt'), content_type='application/vnd.apple.mpegurl')
        if tail == 'subtitles.vtt' and self.vtt:
            return web.Response(text=shift_webvtt(self.vtt, self.subtitle_tracks[self.default_subtitle]['offset']), content_type='text/vtt', headers={'Cache-Control':'no-store'})
        if tail.startswith('file/') and self.directory:
            name = tail[5:]
            if not re.fullmatch(r'video\.m3u8|segment-\d+\.ts', name):
                raise web.HTTPNotFound()
            path = self.directory / name
            if not path.exists():
                raise web.HTTPNotFound()
            if name.endswith('.m3u8'):
                text = re.sub(r'(?m)^(segment-\d+\.ts)$', lambda m: f'{self.base}/file/{m[1]}', path.read_text())
                return web.Response(text=text, content_type='application/vnd.apple.mpegurl')
            return web.FileResponse(path, headers={'Content-Type': 'video/mp2t'})
        if not tail.startswith('r/') or tail[2:] not in self.resources:
            raise web.HTTPNotFound()
        url = self.resources[tail[2:]]
        forwarded = {name: request.headers[name] for name in ('Range', 'If-Range') if name in request.headers}
        known_hls = self.resource_types.get(tail[2:]) == 'playlist' or '.m3u8' in urlsplit(url).path.lower() or (url == self.source and self.kind == 'hls')
        if known_hls:
            forwarded = {}
        method = 'GET' if known_hls else request.method
        response = await self.upstream(url, method, forwarded)
        try:
            content_type = response.headers.get('Content-Type', '').lower()
            if (request.method == 'HEAD' or response.status == 206) and not known_hls and 'mpegurl' in content_type:
                response.close()
                response = await self.upstream(url)
            if response.status >= 400:
                if request.remote not in {'127.0.0.1','::1'}:
                    self.error = f'The video server refused a resource (HTTP {response.status}). Reload the page and restart the video.'
                LOG.warning('Media upstream rejected: HTTP=%s type=%s', response.status, content_type.split(';')[0])
            if response.status < 400 and ('mpegurl' in content_type or '.m3u8' in urlsplit(str(response.url)).path.lower()):
                text = await read_text_body(response)
                return web.Response(text=self.rewrite(text, str(response.url)), content_type='application/vnd.apple.mpegurl')
            headers = {k: v for k, v in response.headers.items() if k.lower() in {'content-type', 'content-length', 'content-range', 'accept-ranges', 'content-encoding'}}
            role = self.resource_types.get(tail[2:], '')
            if self.target_protocol == 'dlna' and url == self.source and self.kind != 'hls' and not content_type.startswith(('video/', 'audio/', 'text/vtt', 'application/x-subrip')):
                headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
                headers['Content-Type'] = {'mp4': 'video/mp4', 'mkv': 'video/x-matroska', 'webm': 'video/webm'}.get(self.kind, 'application/octet-stream')
            if role in {'segment', 'fragment', 'init'} and not content_type.startswith(('video/', 'audio/', 'text/vtt', 'application/x-subrip')):
                headers = {k: v for k, v in headers.items() if k.lower() != 'content-type'}
                headers['Content-Type'] = 'video/mp4' if role == 'init' else 'video/iso.segment' if role == 'fragment' else 'video/mp2t'
            # Only bytes successfully written to a remote receiver count. Local
            # ffprobe/remux reads must never change the displayed variant.
            remote = request.remote not in {None, '127.0.0.1', '::1'}
            measured = remote and response.status in {200, 206} and (role in {'segment', 'fragment'} or url == self.source and self.kind != 'hls')
            total = 0
            stream = web.StreamResponse(status=response.status, headers=headers)
            await stream.prepare(request)
            if request.method != 'HEAD':
                async for chunk in response.content.iter_chunked(128 * 1024):
                    try:
                        await stream.write(chunk)
                        total += len(chunk)
                        if measured:
                            self.telemetry.delivered(len(chunk), url)
                    except ConnectionError:
                        # Probes and players routinely close a range/segment
                        # once buffered. This is not an upstream media failure.
                        return stream
            if measured and request.method != 'HEAD':
                self.telemetry.delivered(0, url, complete=response.status == 200, total=total)
            try:
                await stream.write_eof()
            except ConnectionError:
                pass
            return stream
        finally:
            response.close()

    async def close(self):
        tasks = list(self.subtitle_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.subtitle_tasks.clear()
        if self.compat_monitor:
            self.compat_monitor.cancel()
        if self.compat_process and self.compat_process.returncode is None:
            self.compat_process.terminate()
            try:
                await asyncio.wait_for(self.compat_process.wait(), 3)
            except TimeoutError:
                self.compat_process.kill()
                await self.compat_process.wait()
        if self.compat_directory:
            shutil.rmtree(self.compat_directory, ignore_errors=True)
        if self.monitor:
            self.monitor.cancel()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.directory:
            shutil.rmtree(self.directory, ignore_errors=True)
