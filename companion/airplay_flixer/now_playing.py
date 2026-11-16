"""Small, bounded playback metadata shared by the relay and AirPlay session."""
import base64
import binascii
import re


def clean_title(value):
    return ' '.join(re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '')).split())[:200]


def decode_artwork(value):
    if not isinstance(value, str) or len(value) > 240000:
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    return data if 4 <= len(data) <= 180000 and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9') else None


def with_title(playlist, title, poster=None):
    title = clean_title(title)
    if not title and not poster:
        return playlist
    # HLS quoted strings cannot contain a double quote or line break.
    title = title.replace('"', '”')
    lines = [line for line in playlist.splitlines() if not (line.startswith('#EXT-X-SESSION-DATA:') and 'DATA-ID="com.apple.hls.title"' in line)]
    for i, line in enumerate(lines):
        if line.startswith('#EXT-X-VERSION:'):
            lines[i] = f'#EXT-X-VERSION:{max(7, int(line.split(":", 1)[1]))}'
            break
    else:
        lines.insert(1, '#EXT-X-VERSION:7')
    if title:
        lines.insert(2, f'#EXT-X-SESSION-DATA:DATA-ID="com.apple.hls.title",VALUE="{title}"')
    if poster:
        lines = [line for line in lines if not (line.startswith('#EXT-X-SESSION-DATA:') and 'DATA-ID="com.apple.hls.poster"' in line)]
        lines.insert(2, f'#EXT-X-SESSION-DATA:DATA-ID="com.apple.hls.poster",VALUE="{poster}"')
    return '\n'.join(lines) + '\n'
