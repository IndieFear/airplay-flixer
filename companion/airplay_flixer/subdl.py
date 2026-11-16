"""SubDL search and bounded subtitle downloads, with optional safe ZIP reading."""
import asyncio
import hashlib
import io
import json
import re
import secrets
import zipfile
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, parse_qsl

import aiohttp
from . import VERSION
from .config import read_json, write_json
from .opensubtitles import limited, LANGUAGES


def file_url(value):
    if not isinstance(value,str):raise ValueError('Invalid SubDL URL.')
    url=urlsplit(urljoin('https://dl.subdl.com',value))
    if url.scheme!='https' or url.hostname!='dl.subdl.com' or url.port not in (None,443) or url.username or url.password or not url.path.startswith('/subtitle/'):
        raise ValueError('Unauthorized SubDL download URL.')
    return urlunsplit((url.scheme,url.netloc,url.path,urlencode([(k,v) for k,v in parse_qsl(url.query) if k.lower()!='api_key']),''))


def subtitle_text(content, season=None, episode=None):
    if content.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries=archive.infolist()
                if len(entries)>100 or sum(f.file_size for f in entries)>20_000_000:
                    raise ValueError('SubDL archive too large.')
                files=[f for f in entries if not f.is_dir() and f.filename.lower().endswith(('.srt','.vtt')) and not f.filename.startswith('__MACOSX/')]
                if episode is not None:
                    pattern=rf'(?i)(?<!\d)(?:s0*{season}[ ._-]*e0*{episode}|0*{season}x0*{episode})(?!\d)'
                    matched=[f for f in files if re.search(pattern,f.filename)]
                    if matched:files=matched
                    elif len(files)>1 or any(re.search(r'(?i)\bs\d+e\d+|\d+x\d+',f.filename) for f in files):raise ValueError('Cannot identify this episode in the archive. Choose a specific track.')
                if len(files)!=1:raise ValueError('This archive contains multiple tracks or no SRT/WebVTT file. Choose a specific track.')
                member=files[0]
                if member.file_size>500000 or member.flag_bits & 1:raise ValueError('SubDL track too large or archive password protected.')
                with archive.open(member) as stream:content=stream.read(500001)
        except (zipfile.BadZipFile,RuntimeError,NotImplementedError,OSError):
            raise ValueError('Invalid SubDL subtitle archive.') from None
    if len(content)>500000:raise ValueError('This track exceeds the 500 KB limit.')
    try:text=content.decode('utf-16' if content.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
    except UnicodeDecodeError:text=content.decode('cp1252',errors='replace')
    if len(text.encode())>500000 or not re.search(r'\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->',text):
        raise ValueError('The SubDL track contains no valid SRT/WebVTT subtitles.')
    return text


class SubDL:
    def __init__(self,folder):
        self.path=folder/'subdl.json';self.config=read_json(self.path)
        self.results={};self.downloads={};self.lock=asyncio.Lock()

    def status(self):return {'configured':bool(self.config.get('key'))}

    async def configure(self,client,message):
        async with self.lock:
            if message.get('clear'):
                self.path.unlink(missing_ok=True);self.config={}
            else:
                key=message.get('key') or self.config.get('key','')
                if not isinstance(key,str) or not 1<=len(key)<=512 or any(ord(c)<33 or ord(c)>126 for c in key):raise ValueError('Enter a valid SubDL API key.')
                write_json(self.path,{'key':key});self.config={'key':key}
            self.results.clear();self.downloads.clear()
            return self.status()

    async def request(self,client,params):
        if not self.config.get('key'):raise ValueError('Add your SubDL API key in Settings.')
        url='https://api.subdl.com/api/v1/subtitles?'+urlencode(sorted({**params,'api_key':self.config['key']}.items()))
        try:
            async with client.get(url,headers={'User-Agent':f'Airplay Flixer v{VERSION}','Accept':'application/json'},allow_redirects=False,timeout=aiohttp.ClientTimeout(total=20)) as response:
                if response.status in {401,403}:raise ValueError('SubDL API key rejected. Check it in Settings.')
                if response.status==429:raise ValueError('SubDL quota or request limit reached. Try again later.')
                if response.status!=200:raise ValueError(f'SubDL est indisponible (HTTP {response.status}).')
                body=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body)>2_000_000:raise ValueError('SubDL response too large.')
                data=json.loads(body)
                if not isinstance(data,dict) or data.get('status') is not True:raise ValueError('SubDL refused the search. Check your key and search criteria.')
                return data
        except (aiohttp.ClientError,TimeoutError,json.JSONDecodeError):
            # Exceptions may contain the API key in the URL: never expose them.
            raise ValueError('Cannot reach SubDL. Try again shortly.') from None

    async def search(self,client,message):
        async with self.lock:
            query=limited(message.get('query'));language=message.get('language','fr');kind=message.get('type','movie')
            if len(query)<2 or language not in LANGUAGES or kind not in {'movie','episode'}:raise ValueError('Enter a title or IMDb ID and choose a language.')
            params={'languages':'BR' if language=='pt-br' else language.upper(),'type':'tv' if kind=='episode' else 'movie','unpack':1,'releases':1,'subs_per_page':30,'client':'custom_integration'}
            params['imdb_id' if re.fullmatch(r'tt\d+',query,re.I) else 'film_name']=query
            season=episode=None
            if kind=='episode':
                season,episode=message.get('season_number'),message.get('episode_number')
                for name,value,minimum in [('season_number',season,0),('episode_number',episode,1)]:
                    if isinstance(value,bool) or not isinstance(value,int) or not minimum<=value<=999:raise ValueError('Enter the season and episode numbers.')
                    params[name]=value
            # V1 documents a bounded result count, not page navigation.
            if message.get('page',1)!=1:raise ValueError('Refine your SubDL search for more results.')
            data=await self.request(client,params);found=[]
            feature=(data.get('results') or [{}])[0];title=limited(feature.get('name') or query)
            for item in (data.get('subtitles') or [])[:60]:
                if len(found)>=100:break
                files=item.get('unpack_files') or []
                if files:
                    aliases={'FRENCH':'FR','ENGLISH':'EN','SPANISH':'ES','GERMAN':'DE','ITALIAN':'IT','PORTUGUESE':'PT','BRAZILIAN_PORTUGUESE':'BR','ARABIC':'AR','JAPANESE':'JA','CHINESE':'ZH','RUSSIAN':'RU','KOREAN':'KO','DUTCH':'NL'}
                    candidates=[f for f in files if str(f.get('format','srt')).lower() in {'srt','vtt'} and aliases.get(str(f.get('language') or params['languages']).upper(),str(f.get('language') or params['languages']).upper())==params['languages']]
                    if kind=='episode':candidates=[f for f in candidates if str(f.get('season'))==str(season) and str(f.get('episode'))==str(episode)]
                else:
                    if kind=='episode' and item.get('full_season'):continue
                    if kind=='episode' and item.get('episode') is not None and str(item['episode'])!=str(episode):continue
                    candidates=[item]
                for file in candidates[:30]:
                    if len(found)>=100:break
                    try:url=file_url(file.get('url'))
                    except ValueError:continue
                    identifier=hashlib.sha256((url+'|'+language).encode()).hexdigest()[:32]
                    release=limited(file.get('release_name') or item.get('release_name') or file.get('name'))
                    entry={'fileId':identifier,'provider':'subdl','title':title,'release':release,'filename':limited(file.get('name')),'language':language,'fps':limited(item.get('fps'),20),'hearingImpaired':bool(file.get('hi',item.get('hi'))),'downloads':0,'trusted':False,'machineTranslated':False}
                    self.results[identifier]={**entry,'url':url,'season':season,'episode':episode};found.append(entry)
            while len(self.results)>300:self.results.pop(next(iter(self.results)))
            return {'provider':'subdl','results':found,'page':1,'totalPages':1}

    async def download(self,client,file_id):
        async with self.lock:
            if not isinstance(file_id,str) or file_id not in self.results:raise ValueError('Search SubDL again and choose a track from the results.')
            for identifier,item in self.downloads.items():
                if item['fileId']==file_id:return {'id':identifier,'track':item['track']}
            entry=self.results[file_id];url=entry['url']
            try:
                for _ in range(4):
                    async with client.get(url,headers={'User-Agent':f'Airplay Flixer v{VERSION}'},allow_redirects=False,timeout=aiohttp.ClientTimeout(total=20)) as response:
                        if response.status in {301,302,307,308}:url=file_url(urljoin(url,response.headers.get('Location','')));continue
                        if response.status==429:raise ValueError('SubDL download limit reached. Try again later.')
                        if response.status!=200:raise ValueError('SubDL download failed.')
                        content=bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            content.extend(chunk)
                            if len(content)>2_000_000:raise ValueError('SubDL download too large.')
                        break
                else:raise ValueError('Too many SubDL redirects.')
            except (aiohttp.ClientError,TimeoutError):raise ValueError('Cannot download the SubDL track.') from None
            text=subtitle_text(bytes(content),entry['season'],entry['episode'])
            track={'content':text,'label':'SubDL · '+(entry['release'] or entry['title']),'language':entry['language'],'provider':'subdl','fileId':file_id}
            identifier=secrets.token_urlsafe(18);self.downloads[identifier]={'track':track,'fileId':file_id}
            while len(self.downloads)>5:self.downloads.pop(next(iter(self.downloads)))
            return {'id':identifier,'track':track}
