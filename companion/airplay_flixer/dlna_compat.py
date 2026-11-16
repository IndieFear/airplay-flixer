"""Choose one HLS video rendition together with its matching audio group."""
import re
from urllib.parse import urljoin

ATTR = re.compile(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))')


def attributes(line):
    return {m[1]: m[2] if m[2] is not None else m[3] for m in ATTR.finditer(line.partition(':')[2])}


def choose_manifest(text, source, *, highest_quality=False):
    lines = [line.strip() for line in text.splitlines()]
    variants = []
    for i, line in enumerate(lines):
        if not line.startswith('#EXT-X-STREAM-INF:'):
            continue
        a = attributes(line)
        target = next((v for v in lines[i+1:] if v and not v.startswith('#')), None)
        if not target:
            continue
        try:
            width, height = map(int, a.get('RESOLUTION', '0x0').split('x'))
        except ValueError:
            width, height = 0, 0
        codecs = a.get('CODECS', '').lower()
        if not height and codecs and not any(v in codecs for v in ('avc', 'hvc', 'hev', 'av01', 'vp0')):
            continue  # Audio-only variant.
        variants.append((a, urljoin(source, target), width, height))
    if not variants:
        if '#EXT-X-STREAM-INF:' in text:
            raise ValueError('Aucune variante vidéo exploitable dans ce master HLS.')
        return text, {}

    def score(v):
        a, _, _, height = v
        if highest_quality:
            bandwidth = a.get('AVERAGE-BANDWIDTH', a.get('BANDWIDTH', ''))
            return height, int(bandwidth) if bandwidth.isdecimal() else 0
        avc = 'avc1' in a.get('CODECS', '').lower() or 'avc3' in a.get('CODECS', '').lower()
        return (avc, 0 < height <= 1080, height if height <= 1080 else -height)

    a, target, width, height = max(variants, key=score)
    output = ['#EXTM3U', '#EXT-X-VERSION:3']
    audio = [line for line in lines if line.startswith('#EXT-X-MEDIA:') and attributes(line).get('TYPE') == 'AUDIO' and attributes(line).get('GROUP-ID') == a.get('AUDIO')]
    if audio:
        chosen = next((line for line in audio if attributes(line).get('DEFAULT') == 'YES'), audio[0])
        chosen = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + urljoin(source, m[1]) + '"', chosen)
        output.append(chosen)
    # Retain the complete audio group linkage; never select just the silent leaf.
    fields = []
    for key in ('BANDWIDTH', 'AVERAGE-BANDWIDTH', 'RESOLUTION', 'FRAME-RATE', 'CODECS', 'AUDIO'):
        if key in a:
            fields.append(f'{key}="{a[key]}"' if key in {'CODECS', 'AUDIO'} else f'{key}={a[key]}')
    output.extend(['#EXT-X-STREAM-INF:' + ','.join(fields), target])
    return '\n'.join(output) + '\n', {'width': width, 'height': height}
