"""Measurements from media bytes delivered to the receiver, excluding local probes."""
from collections import deque
import re
import time
from urllib.parse import urljoin

ATTR = re.compile(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))')


class StreamTelemetry:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.variants = {}
        self.segments = {}
        self.transfers = deque()
        self.samples = deque(maxlen=5)
        self.current = {}
        self.last_video = None
        self.started = None

    def manifest(self, text, source):
        lines = text.splitlines()
        duration = None
        variant = None
        for line in lines:
            if line.startswith('#EXT-X-STREAM-INF:'):
                a = {m[1]: m[2] if m[2] is not None else m[3] for m in ATTR.finditer(line)}
                resolution = a.get('RESOLUTION', '')
                match = re.fullmatch(r'(\d+)x(\d+)', resolution)
                variant = {'width': int(match[1]), 'height': int(match[2])} if match else {}
                try:
                    variant['declaredBitrate'] = int(a.get('AVERAGE-BANDWIDTH') or a.get('BANDWIDTH') or 0)
                except ValueError:
                    pass
                variant['video'] = bool(match) or bool(re.search(r'avc|hvc|hev|av01|vp0', a.get('CODECS', ''), re.I))
            elif line.startswith('#EXTINF:'):
                try:
                    duration = float(line.split(':', 1)[1].split(',')[0])
                except ValueError:
                    duration = None
            elif line.strip() and not line.startswith('#'):
                url = urljoin(source, line.strip())
                if variant is not None:
                    self.variants[url] = variant
                    variant = None
                elif duration and duration > 0:
                    self.segments[url] = (duration, dict(self.variants.get(source, {})))
                    duration = None
        # Live playlists rotate; keep a bounded recent resource index.
        if len(self.segments) > 20000:
            self.segments = dict(list(self.segments.items())[-10000:])

    def delivered(self, size, url, complete=False, total=0):
        now = self.clock()
        if size:
            self.started = self.started if self.started is not None else now
            self.transfers.append((now, size))
        self._prune(now)
        segment = self.segments.get(url)
        if segment:
            duration, info = segment
            if info.get('video'):
                self.current = info
                self.last_video = now
            if complete and total and duration:
                self.samples.append((now, total * 8 / duration, bool(info.get('video'))))

    def _prune(self, now):
        while self.transfers and now - self.transfers[0][0] > 10:
            self.transfers.popleft()

    def snapshot(self):
        now = self.clock()
        self._prune(now)
        elapsed = min(10, now - self.started) if self.started is not None else 0
        recent = [s for s in self.samples if now - s[0] < 30 and s[2]]
        return {
            **self.current,
            'transferBitrate': round(sum(s for _, s in self.transfers) * 8 / elapsed) if elapsed >= 1 else None,
            'segmentBitrate': round(sum(s[1] for s in recent) / len(recent)) if recent else None,
            'sampleAge': round(now - self.last_video, 1) if self.last_video is not None else None,
            'basis': 'delivered',
        }
