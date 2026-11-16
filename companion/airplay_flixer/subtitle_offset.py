"""Shift WebVTT cues without changing their original MPEGTS clock mapping."""
import math
import re

CUE = re.compile(r'^(?P<a>(?:\d+:)?\d{2}:\d{2}[.,]\d{3})(?P<arrow>\s+-->\s+)(?P<b>(?:\d+:)?\d{2}:\d{2}[.,]\d{3})(?P<settings>[^\n]*)$',re.M)


def offset_value(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -600 <= value <= 600:
        raise ValueError('The offset must be between −600 and +600 seconds.')
    return round(float(value),3)


def cue_ms(value):
    parts=value.replace(',','.').split(':')
    return round((float(parts[-1])+int(parts[-2])*60+(int(parts[-3])*3600 if len(parts)==3 else 0))*1000)


def stamp(value):
    hours,rest=divmod(max(0,value),3600000);minutes,rest=divmod(rest,60000);seconds,ms=divmod(rest,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}.{ms:03}'


def shift_webvtt(text,offset):
    delta=round(offset_value(offset)*1000)
    if not delta:return text
    output=[]
    for block in re.split(r'\n\s*\n',text):
        if block.startswith(('WEBVTT','NOTE','STYLE','REGION')):
            output.append(block);continue
        match=CUE.search(block)
        if match:
            start,end=cue_ms(match['a'])+delta,cue_ms(match['b'])+delta
            if end<=0:continue
            replacement=f'{stamp(start)}{match["arrow"]}{stamp(end)}{match["settings"]}'
            block=block[:match.start()]+replacement+block[match.end():]
        output.append(block)
    return '\n\n'.join(output).rstrip()+'\n'
