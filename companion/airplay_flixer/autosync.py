"""Bounded local audio sampling and ffsubsync-based constant-offset proposals."""
from __future__ import annotations
import asyncio
import json
import logging
import math
from pathlib import Path
import re
import sys
import tempfile
import wave
from urllib.parse import urljoin

from .subtitle_offset import CUE, cue_ms

RATE = 100
MAX_OFFSET = 60
WINDOW = 240


def cue_intervals(text):
    return [(cue_ms(m['a'])/1000, cue_ms(m['b'])/1000) for m in CUE.finditer(text) if cue_ms(m['b']) > cue_ms(m['a'])]


def window_start(position, duration):
    return max(0, min(max(0,position-60),max(0,duration-WINDOW))) if duration else max(0,position-60)


def phi_score(reference, subtitles):
    import numpy as np
    r,s=reference.astype(bool),subtitles.astype(bool)
    tp=float(np.sum(r&s));tn=float(np.sum(~r&~s));fp=float(np.sum(~r&s));fn=float(np.sum(r&~s))
    denominator=math.sqrt((tp+fp)*(tp+fn)*(tn+fp)*(tn+fn))
    return ((tp*tn-fp*fn)/denominator if denominator else 0, 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)


def shifted(samples, delta):
    import numpy as np
    result=np.zeros_like(samples)
    if delta>=0:
        if delta < len(samples):result[delta:]=samples[:len(samples)-delta]
    elif -delta<len(samples):result[:delta]=samples[-delta:]
    return result


def propose(reference, subtitles):
    import numpy as np
    from ffsubsync.aligners import FFTAligner
    r=np.asarray(reference,dtype=float);s=np.asarray(subtitles,dtype=float)
    n=min(len(r),len(s));r,s=r[:n],s[:n]
    diagnostic={'sampleSeconds':round(n/RATE),'voiceRatio':round(float(np.mean(r)),3) if n else 0,'subtitleRatio':round(float(np.mean(s)),3) if n else 0}
    def uncertain(reason,message):
        return {'state':'uncertain','reason':reason,'message':message,'sampleSeconds':round(n/RATE),'diagnostics':dict(diagnostic)}
    if n<80*RATE or np.sum(r)<8*RATE or not .03<float(np.mean(r))<.9:
        return uncertain('insufficient_speech','Pas assez de dialogue exploitable dans cet extrait.')
    if not .03<float(np.mean(s))<.95:
        return uncertain('insufficient_captions','Les sous-titres ne couvrent pas suffisamment cet extrait.')
    limit=min(MAX_OFFSET*RATE,n//3)
    delta=int(FFTAligner(max_offset_samples=limit).fit(r,s).transform())
    diagnostic['candidateOffset']=round(delta/RATE,2)
    if abs(delta)>=limit-10:
        return uncertain('search_boundary','Le décalage semble dépasser la plage de recherche automatique.')
    aligned=shifted(s,delta)
    correlation,f1=phi_score(r,aligned)
    diagnostic.update(correlation=round(correlation,3),overlap=round(f1,3))
    if correlation<.12 or f1<.4:
        return uncertain('weak_match','La correspondance entre les voix et cette piste est trop faible pour confirmer une correction.')
    checks=[]
    for start,end in [(0,n//2),(n//2,n)]:
        a,b=r[start:end],aligned[start:end]
        if np.sum(a)<3*RATE or not .03<float(np.mean(b))<.95:
            checks.append({'informative':False});continue
        score,overlap=phi_score(a,b)
        # Validate the SAME global shift with full subtitle context. A new broad
        # FFT on each cropped half creates false independent matches at its edges.
        best_score,best_delta=score,delta
        for local_delta in range(max(-limit,delta-300),min(limit,delta+300)+1,10):
            candidate=shifted(s,local_delta)[start:end]
            candidate_score,_=phi_score(a,candidate)
            if candidate_score>best_score:
                best_score,best_delta=candidate_score,local_delta
        checks.append({'informative':True,'correlation':round(score,3),'overlap':round(overlap,3),'nearbyOffset':round(best_delta/RATE,2),'nearbyGain':round(best_score-score,3)})
    diagnostic['windows']=checks
    if any(c.get('informative') and abs(c['nearbyOffset']-delta/RATE)>1.5 and c['nearbyGain']>.1 for c in checks):
        return uncertain('inconsistent_windows','Les passages donnent des corrections différentes ; un décalage constant ne peut pas être confirmé.')
    if len([c for c in checks if c.get('informative') and c['correlation']>=.10 and c['overlap']>=.4])<2:
        return uncertain('insufficient_validation','Un des passages est trop peu concluant pour confirmer le décalage. Cela ne prouve pas une différence de montage.')
    return {'state':'proposed','offset':round(delta/RATE,2),'quality':'consistent','message':'La même correction est confirmée sur les deux parties de l’extrait.','sampleSeconds':round(n/RATE),'correlation':round(correlation,3),'overlap':round(f1,3),'diagnostics':diagnostic}


def analyse_files(audio, intervals, start):
    import numpy as np
    import webrtcvad
    with wave.open(str(audio),'rb') as source:
        if source.getnchannels()!=1 or source.getsampwidth()!=2 or source.getframerate()!=16000 or source.getnframes()>16000*(WINDOW+5):
            raise ValueError('Extrait audio invalide.')
        pcm=source.readframes(source.getnframes())
    detector=webrtcvad.Vad(3)
    step=320 # 10 ms of mono PCM16 at 16 kHz.
    reference=np.array([detector.is_speech(pcm[i:i+step],16000) for i in range(0,len(pcm)-step+1,step)],dtype=float)
    captions=np.zeros(len(reference),dtype=float)
    count=0
    for begin,end in intervals:
        a,b=max(0,round((begin-start)*RATE)),min(len(reference),round((end-start)*RATE))
        if b>a:captions[a:b]=1;count+=1
    if count<8:
        return {'state':'uncertain','reason':'insufficient_captions','sampleSeconds':round(len(reference)/RATE),'message':'Trop peu de répliques dans cet extrait pour synchroniser automatiquement cette piste.'}
    return propose(reference,captions)


def worker_main(filename):
    logging.disable(logging.CRITICAL)
    try:
        config=json.loads(Path(filename).read_text())
        result=analyse_files(config['audio'],config['intervals'],config['start'])
    except Exception:
        result={'state':'failed','message':'L’analyse locale des voix a échoué. Le réglage manuel reste disponible.'}
    print(json.dumps(result),flush=True)


async def run_process(arguments, timeout):
    process=await asyncio.create_subprocess_exec(*arguments,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        output,_=await asyncio.wait_for(process.communicate(),timeout)
        if process.returncode:
            raise ValueError('L’extrait audio ne peut pas être analysé. Vérifie la piste sonore et réessaie.')
        return output
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def analyse(media,index,position,loopback,progress):
    from .media import binary,hls_probe_options
    from .dlna_compat import choose_manifest,attributes
    ffmpeg,probe=binary('ffmpeg'),binary('ffprobe')
    if not ffmpeg or not probe:
        raise ValueError('FFmpeg est nécessaire pour la synchronisation automatique.')
    progress('subtitle','Lecture de la piste de sous-titres…')
    raw=await media.subtitle_vtt(index)
    intervals=cue_intervals(raw)
    if len(intervals)<8:
        return {'state':'uncertain','message':'Cette piste contient trop peu de répliques pour une analyse fiable.'}
    source=media.source
    hls=media.kind=='hls' or '.m3u8' in source.split('?',1)[0]
    if media.directory:
        local=f'{loopback}/media/{media.id}/file/video.m3u8';hls=True
    elif hls:
        text,actual=await media.fetch_text(source)
        selected,_=choose_manifest(text,actual)
        audio=next((attributes(line).get('URI') for line in selected.splitlines() if line.startswith('#EXT-X-MEDIA:') and attributes(line).get('TYPE')=='AUDIO' and attributes(line).get('URI')),None)
        video=next((line for line in reversed(selected.splitlines()) if line and not line.startswith('#')),None)
        source=urljoin(actual,audio or video or actual) if '#EXT-X-STREAM-INF:' in text else actual
        local=media.resource(source,'playlist').replace(media.base,f'{loopback}/media/{media.id}',1)
    else:
        local=media.resource(source).replace(media.base,f'{loopback}/media/{media.id}',1)
    # Translate original VTT cue times into the movie clock, retaining the
    # existing MPEGTS mapping rather than assuming every input starts at zero.
    mapline=re.search(r'X-TIMESTAMP-MAP=([^\n]+)',raw)
    local_time=re.search(r'LOCAL:([\d:.]+)',mapline[1]) if mapline else None
    mpeg_time=re.search(r'MPEGTS:(\d+)',mapline[1]) if mapline else None
    if local_time and mpeg_time:
        base=media.subtitle_timestamp
        ticks=int(mpeg_time[1])
        if ticks != round(base)%(1<<33):
            timing_url=f'{loopback}/media/{media.id}/file/video.m3u8' if media.directory else media.resource(media.source,'playlist').replace(media.base,f'{loopback}/media/{media.id}',1)
            base=await media.timestamp(timing_url)
        difference=((ticks-base+(1<<32))%(1<<33)-(1<<32))/90000-cue_ms(local_time[1])/1000
        intervals=[(a+difference,b+difference) for a,b in intervals]
    first=window_start(position,media.duration)
    starts=[first]
    second=first-WINDOW if first>=WINDOW else min(first+WINDOW,max(0,media.duration-WINDOW))
    if abs(second-first)>=60:starts.append(second)
    best=None;analysed=0
    with tempfile.TemporaryDirectory(prefix='airplay-flixer-autosync-') as directory:
        folder=Path(directory)
        for attempt,start in enumerate(starts,1):
            audio=folder/f'sample-{attempt}.wav'
            progress('audio',f'Extraction de l’extrait audio {attempt}/{len(starts)}…' if attempt==1 else 'Premier extrait peu concluant : analyse d’un autre passage…')
            await run_process([ffmpeg,'-v','error','-nostdin','-y',*(hls_probe_options(probe) if hls else ()), '-ss',str(start),'-i',local,'-t',str(WINDOW),'-map','0:a:0','-vn','-sn','-dn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(audio)],120)
            if not audio.exists() or audio.stat().st_size>16000*2*(WINDOW+5)+4096:
                raise ValueError('L’extrait audio est invalide ou trop volumineux.')
            config=folder/'job.json';config.write_text(json.dumps({'audio':str(audio),'intervals':intervals,'start':start}))
            progress('align',f'Comparaison des voix avec les sous-titres (extrait {attempt})…')
            command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve().parents[1]/'entry.py')]
            output=await run_process([*command,'--autosync-worker',str(config)],45)
            result=json.loads(output)
            if result.get('state') not in {'proposed','uncertain','failed'}:
                raise ValueError('Résultat d’analyse automatique invalide.')
            analysed+=result.get('sampleSeconds',0)
            result.update(attempts=attempt,analysedSeconds=analysed)
            if result['state'] in {'proposed','failed'}:return result
            if best is None or result.get('diagnostics',{}).get('correlation',0)>best.get('diagnostics',{}).get('correlation',0):best=result
        best.update(attempts=len(starts),analysedSeconds=analysed)
        return best


def log_result(result):
    logger=logging.getLogger('airplay_flixer.autosync')
    logger.setLevel(logging.INFO)
    summary={key:result.get(key) for key in ['state','reason','offset','attempts','analysedSeconds','diagnostics'] if key in result}
    logger.info('Subtitle auto-sync result: %s',json.dumps(summary,ensure_ascii=True))


def self_check():
    logging.disable(logging.CRITICAL)
    import numpy as np
    import webrtcvad
    reference=np.zeros(24000);rng=np.random.default_rng(42);i=800
    while i<23000:
        size=int(rng.integers(90,350));reference[i:i+size]=1;i+=size+int(rng.integers(80,400))
    result=propose(reference,shifted(reference,350))
    assert result.get('state')=='proposed' and result['offset']==-3.5
    assert not webrtcvad.Vad(2).is_speech(b'\0'*320,16000)
    print('Auto-sync FFT and voice detector: OK')
