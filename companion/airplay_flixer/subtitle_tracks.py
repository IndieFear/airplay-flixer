"""HLS alternate subtitle renditions with unique names and one default."""
import re


def with_subtitle_tracks(playlist, tracks):
    if not tracks:
        return playlist
    lines = playlist.splitlines()
    originals = [line for line in lines if line.startswith('#EXT-X-MEDIA:') and 'TYPE=SUBTITLES' in line]
    lines = [line for line in lines if line not in originals]
    lines = [re.sub(r',?SUBTITLES="[^"]*"', '', line).replace(':,', ':').rstrip(',') + ',SUBTITLES="flixer-subs"' if line.startswith('#EXT-X-STREAM-INF:') else line for line in lines]
    names = set()
    def unique(label):
        label = re.sub(r'["\r\n\x00-\x1f]', '', str(label))[:95] or 'Subtitles'
        candidate, index = label, 2
        while candidate in names:
            candidate = f'{label} ({index})'; index += 1
        names.add(candidate)
        return candidate
    entries = []
    default = next((i for i,t in enumerate(tracks) if t.get('default')), 0)
    for i, track in enumerate(tracks):
        label = unique(track.get('label') or f'Track {i+1}')
        language = re.sub(r'[^a-zA-Z0-9-]', '', str(track.get('language') or 'und'))[:20] or 'und'
        selected = 'YES' if i == default else 'NO'
        entries.append(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="flixer-subs",NAME="{label}",LANGUAGE="{language}",DEFAULT={selected},AUTOSELECT={selected},FORCED=NO,URI="{track["url"]}"')
    # Keep native renditions available in the same group alongside external ones.
    seen = set()
    for line in originals:
        uri = re.search(r'URI="([^"]+)"', line)
        if not uri or uri[1] in seen:
            continue
        seen.add(uri[1])
        name = re.search(r'NAME="([^"]*)"', line)
        line = re.sub(r'GROUP-ID="[^"]*"', 'GROUP-ID="flixer-subs"', line)
        label = unique(name[1] if name else 'Embedded track')
        line = re.sub(r'NAME="[^"]*"', lambda _: f'NAME="{label}"', line)
        line = re.sub(r',?(?:DEFAULT|AUTOSELECT)=(?:YES|NO)', '', line)
        entries.append(line + ',DEFAULT=NO,AUTOSELECT=NO')
    lines[1:1] = entries
    return '\n'.join(lines) + '\n'
