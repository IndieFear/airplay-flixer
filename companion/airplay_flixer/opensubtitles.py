"""OpenSubtitles REST client. Credentials remain in the Mac's private data folder."""
import asyncio
import json
import re
import secrets
from urllib.parse import urlencode, urlsplit

import aiohttp

from . import VERSION
from .config import read_json, write_json

API_HOSTS = {'api.opensubtitles.com', 'vip-api.opensubtitles.com'}
LANGUAGES = {'fr','en','es','de','it','pt','pt-br','nl','ar','ja','ko','zh','ru'}


def limited(value, size=200):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '')).strip()[:size]


def download_url(value):
    p = urlsplit(value if isinstance(value, str) else '')
    host = p.hostname or ''
    if p.scheme != 'https' or p.username or p.password or p.port not in (None,443) or not any(host == d or host.endswith('.'+d) for d in ('opensubtitles.com','opensubtitles.org')):
        raise ValueError('OpenSubtitles a renvoyé une adresse de téléchargement non autorisée.')
    return value


class OpenSubtitles:
    def __init__(self, folder):
        self.path = folder / 'opensubtitles.json'
        self.config = read_json(self.path)
        self.results = {}
        self.downloads = {}
        self.lock = asyncio.Lock()

    def status(self):
        return {'configured':bool(self.config.get('key')), 'connected':bool(self.config.get('token')), 'username':self.config.get('username','')}

    async def request(self, client, method, endpoint, *, params=None, body=None, config=None):
        config = self.config if config is None else config
        if not config.get('key'):
            raise ValueError('Ajoute ta clé API OpenSubtitles dans Paramètres.')
        host = config.get('host','api.opensubtitles.com')
        if host not in API_HOSTS:
            host = 'api.opensubtitles.com'
        url = f'https://{host}/api/v1/{endpoint}'
        if params:
            url += '?' + urlencode(sorted(params.items()))
        headers = {'Api-Key':config['key'], 'User-Agent':f'Airplay Flixer v{VERSION}', 'Accept':'application/json'}
        if config.get('token'):
            headers['Authorization'] = 'Bearer '+config['token']
        try:
            # Only same-origin canonical redirects can retain API credentials.
            for _ in range(4):
                async with client.request(method,url,headers=headers,json=body,allow_redirects=False,timeout=aiohttp.ClientTimeout(total=20)) as response:
                    if response.status in {301,302,307,308}:
                        from urllib.parse import urljoin
                        destination = urljoin(url,response.headers.get('Location',''))
                        if urlsplit(destination).scheme != 'https' or urlsplit(destination).netloc != urlsplit(url).netloc:
                            raise ValueError('Redirection OpenSubtitles refusée.')
                        url = destination
                        continue
                    if response.status >= 400:
                        messages = {401:'Connexion OpenSubtitles expirée ou identifiants refusés. Reconnecte ton compte dans Paramètres.',403:'OpenSubtitles a refusé la demande. Vérifie ta clé API, ton compte et ton quota.',406:'Quota de téléchargements OpenSubtitles atteint.',429:'OpenSubtitles reçoit trop de demandes. Réessaie dans quelques instants.'}
                        raise ValueError(messages.get(response.status, f'OpenSubtitles est indisponible (HTTP {response.status}).'))
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.extend(chunk)
                        if len(chunks)>2_000_000:
                            raise ValueError('Réponse OpenSubtitles trop volumineuse.')
                    data = json.loads(chunks)
                    if not isinstance(data,dict):
                        raise ValueError('Réponse OpenSubtitles invalide.')
                    return data
            raise ValueError('Trop de redirections OpenSubtitles.')
        except (aiohttp.ClientError,TimeoutError, json.JSONDecodeError):
            raise ValueError('Impossible de joindre OpenSubtitles. Réessaie dans un instant.') from None

    async def configure(self, client, message):
        async with self.lock:
            if message.get('clear'):
                self.path.unlink(missing_ok=True)
                self.config = {};self.results.clear();self.downloads.clear()
                return self.status()
            key = message.get('key') or self.config.get('key','')
            if not isinstance(key,str) or not 1<=len(key)<=512 or any(ord(c)<33 or ord(c)>126 for c in key):
                raise ValueError('Entre une clé API OpenSubtitles valide.')
            updated = dict(self.config) if key == self.config.get('key') else {}
            updated['key'] = key
            username,password = message.get('username'),message.get('password')
            if username or password:
                if not isinstance(username,str) or not isinstance(password,str) or not username or not password or len(username)>200 or len(password)>1024:
                    raise ValueError('Renseigne le nom de compte et le mot de passe pour te connecter.')
                login = await self.request(client,'POST','login',body={'username':username,'password':password},config={'key':key})
                token = login.get('token')
                if not isinstance(token,str) or not token or len(token)>8192 or any(ord(c)<33 or ord(c)>126 for c in token):
                    raise ValueError('OpenSubtitles n’a pas renvoyé de connexion valide.')
                host = login.get('base_url','api.opensubtitles.com').removeprefix('https://').rstrip('/')
                if host not in API_HOSTS:
                    raise ValueError('Serveur de connexion OpenSubtitles non reconnu.')
                updated.update(token=token,username=limited(username),host=host)
            write_json(self.path,updated)
            self.config=updated;self.results.clear();self.downloads.clear()
            return self.status()

    async def search(self, client, message):
        async with self.lock:
            query=limited(message.get('query')).lower()
            language=message.get('language','fr')
            kind=message.get('type','movie')
            if len(query)<2 or language not in LANGUAGES or kind not in {'movie','episode'}:
                raise ValueError('Renseigne le titre ou l’identifiant IMDb et la langue recherchée.')
            params={'languages':language,'type':kind,'order_by':'download_count','order_direction':'desc'}
            imdb=re.fullmatch(r'tt(\d+)',query)
            params['imdb_id' if imdb else 'query']=str(int(imdb[1])) if imdb else query
            if kind=='episode':
                for key in ('season_number','episode_number'):
                    value=message.get(key)
                    if isinstance(value,bool) or not isinstance(value,int) or not (0 if key=='season_number' else 1)<=value<=999:
                        raise ValueError('Précise la saison et l’épisode recherchés.')
                    params[key]=value
            page=message.get('page',1)
            if isinstance(page,bool) or not isinstance(page,int) or not 1<=page<=100:
                raise ValueError('Page de résultats invalide.')
            if page>1:params['page']=page
            data=await self.request(client,'GET','subtitles',params=params)
            found=[]
            for item in data.get('data',[])[:60]:
                a=item.get('attributes',{});feature=a.get('feature_details') or {}
                for file in (a.get('files') or [])[:5]:
                    file_id=file.get('file_id')
                    if isinstance(file_id,bool) or not isinstance(file_id,int) or file_id<=0:continue
                    entry={'fileId':file_id,'title':limited(feature.get('title') or feature.get('movie_name') or query), 'release':limited(a.get('release') or file.get('file_name')), 'filename':limited(file.get('file_name')), 'language':language,'fps':limited(a.get('fps'),20), 'downloads':a.get('download_count',0),'hearingImpaired':bool(a.get('hearing_impaired')), 'machineTranslated':bool(a.get('machine_translated') or a.get('ai_translated')),'trusted':bool(a.get('from_trusted'))}
                    self.results[file_id]=entry;found.append(entry)
            # Retain a small set for explicit selection across result pages.
            while len(self.results)>300:self.results.pop(next(iter(self.results)))
            return {'results':found,'page':page,'totalPages':min(100,int(data.get('total_pages') or 1))}

    async def download(self, client, file_id):
        async with self.lock:
            if isinstance(file_id,bool) or not isinstance(file_id,int) or file_id not in self.results:
                raise ValueError('Relance la recherche puis sélectionne une piste de ses résultats.')
            for identifier,track in self.downloads.items():
                if track['fileId']==file_id:return {'id':identifier,'track':track['track'],'remaining':track.get('remaining')}
            entry=self.results[file_id]
            data=await self.request(client,'POST','download',body={'file_id':file_id,'sub_format':'srt'})
            url=download_url(data.get('link'))
            try:
                for _ in range(4):
                    async with client.get(url,headers={'User-Agent':f'Airplay Flixer v{VERSION}'},allow_redirects=False,timeout=aiohttp.ClientTimeout(total=20)) as response:
                        if response.status in {301,302,307,308}:
                            from urllib.parse import urljoin
                            url=download_url(urljoin(url,response.headers.get('Location','')));continue
                        if response.status != 200:raise ValueError('Le téléchargement OpenSubtitles a échoué. Réessaie la recherche.')
                        content=bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            content.extend(chunk)
                            if len(content)>500000:raise ValueError('Cette piste dépasse la taille autorisée de 500 Ko.')
                        break
                else:raise ValueError('Trop de redirections pour le téléchargement.')
            except (aiohttp.ClientError,TimeoutError):
                raise ValueError('Le téléchargement de la piste a expiré.') from None
            try:text=content.decode('utf-16' if content.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
            except UnicodeDecodeError:text=content.decode('cp1252',errors='replace')
            if len(text.encode())>500000 or not re.search(r'\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->',text):
                raise ValueError('Le fichier téléchargé ne contient pas de sous-titres SRT/WebVTT valides.')
            label='OpenSubtitles · '+(entry['release'] or entry['title'])
            track={'content':text,'label':label,'language':entry['language'],'provider':'opensubtitles','fileId':file_id}
            identifier=secrets.token_urlsafe(18)
            self.downloads[identifier]={'track':track,'fileId':file_id,'remaining':data.get('remaining')}
            while len(self.downloads)>5:self.downloads.pop(next(iter(self.downloads)))
            return {'id':identifier,'track':track,'remaining':data.get('remaining')}
