"""Local visual QA of the real popup with a deterministic Chrome API fixture."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[1] / 'extension'
mock = '''
const mode=new URLSearchParams(location.search).get('mode');
const demo={version:'0.7.1',message:'Playing on Séjour',title:'Spider-Man: Brand New Day',selected:'salon',selectedName:'Séjour',devices:[{id:'salon',name:'Séjour',paired:true},{id:'bureau',name:'Bureau',paired:false}],casting:mode!=='idle',confirmed:true,paused:false,position:2338,duration:8700,seekable:true,playbackState:'playing'};
if(mode==='dlna'){demo.selected='dlna:tv';demo.selectedName='Smart TV';demo.selectedProtocol='dlna';demo.devices.push({id:'dlna:tv',name:'Smart TV',protocol:'dlna',canCast:true,pairingRequired:false});demo.stream={width:1920,height:1080,segmentBitrate:4500000,transferBitrate:5200000};}
demo.subtitleSession='preview-session';demo.subtitleTracks=[{index:0,label:'Track 1 · French',language:'fr',offset:0,default:true},{index:1,label:'Track 2 · WEBRip',language:'fr',offset:0,default:false}];
demo.stream={width:1920,height:1080,segmentBitrate:4500000,transferBitrate:5200000};
if(mode==='empty')demo.casting=false;
if(mode==='setup'){demo.casting=false;demo.selected='';demo.selectedName='';}
demo.opensubtitles={configured:mode==='os',connected:false,username:''};demo.subtitleImportable=true;demo.dlnaExternalSubtitles=true;demo.dlnaAutomaticSubtitles=true;demo.subdl={configured:mode==='sd'};
let stamp=Date.now();
window.chrome={storage:{local:{get:async()=>({theme:new URLSearchParams(location.search).get("theme")}),set:async()=>{}}},tabs:{query:async()=>[{id:1}]},runtime:{getManifest:()=>({version:"0.18.0"}),sendMessage:async(m)=>{
if(mode==='offline'&&m.action==='status')return {ok:false,error:'Ouvre Airplay Flixer sur le Mac, puis réessaie.'};
const now=Date.now();if(demo.casting&&!demo.paused)demo.position+=(now-stamp)/1000;stamp=now;
if(mode==='empty'&&m.action==='inspect')return {ok:true,result:{title:'Onglet sans vidéo',sources:[],subtitles:[]}};
if(m.action==='inspect')return {ok:true,result:{title:'Spider-Man: Brand New Day',video:{position:137},sources:[{url:'https://example.test/master.m3u8',label:'Auto · up to 1080p · video + audio',details:'Master stream. Adjusts quality automatically. Qualities 720p, 1080p. Audio : français.'},{url:'https://example.test/1080.m3u8',label:'HLS · 1080p · video + audio',details:'Fixed quality variant. 4.5 Mb/s · H.264 / AAC'}],subtitles:[{url:'https://example.test/fr.vtt',label:'French · version officielle',language:'fr',selected:true},{url:'https://example.test/fr2.vtt',label:'Spider-Man.Brand.New.Day.2026.1080p.WEBRip.x264.AAC5.1.fr-1791483012.srt',language:'und'},{url:'https://example.test/en.vtt',label:'English (SDH)',language:'en'},{url:'https://example.test/es.vtt',label:'Spanish (KyoGo)',language:'und'},{url:'https://example.test/und.vtt',label:'Track sans langue',language:'und'}]}};
if(m.action==='os_configure'){demo.opensubtitles={configured:!m.clear,connected:!!m.username,username:m.username||''};return {ok:true,result:demo.opensubtitles};}
if(m.action==='sd_configure'){demo.subdl={configured:!m.clear};return {ok:true,result:demo.subdl};}
if(m.action==='sd_search')return {ok:true,result:{provider:'subdl',page:1,totalPages:1,results:[{fileId:'demo-subdl',provider:'subdl',title:'Mon épisode · S02E01',release:'Mon.episode.S02E01.1080p.WEB-DL',language:'fr',fps:'23.976',hearingImpaired:true}]}};
if(m.action==='os_search')return {ok:true,result:{page:m.page||1,totalPages:2,results:[{fileId:12,title:'Mon épisode · S02E01',release:'Mon.episode.S02E01.1080p.WEB-DL',language:'fr',fps:'23.976',downloads:1240,hearingImpaired:false,trusted:true},{fileId:13,title:'Mon épisode · S02E01',release:'Mon.episode.S02E01.BluRay',language:'fr',fps:'24',downloads:725,hearingImpaired:true}]}};
if(['os_download','sd_download'].includes(m.action)){const source=m.action==='sd_download'?'subdl':'opensubtitles';const track={provider:source,fileId:m.fileId,content:'1\\n00:00:01,000 --> 00:00:03,000\\nBonjour',label:(source==='subdl'?'SubDL':'OpenSubtitles')+' · Mon.episode.S02E01.WEB-DL',language:'fr'};const page=(await chrome.runtime.sendMessage({action:'inspect'})).result;page.subtitles.unshift(track);return {ok:true,result:{id:'mock-download',track,page,remaining:4}};}
if(['os_apply','sd_apply'].includes(m.action)){demo.subtitleTracks.forEach(t=>t.default=false);demo.subtitleTracks.push({index:demo.subtitleTracks.length,label:(m.action==='sd_apply'?'SubDL':'OpenSubtitles')+' · WEB-DL',language:'fr',offset:0,default:true});}
if(m.action==='select'){demo.selected=m.id;demo.selectedName=demo.devices.find(d=>d.id===m.id)?.name;demo.selectedProtocol=demo.devices.find(d=>d.id===m.id)?.protocol||'airplay';demo.casting=false;}
if(m.action==='subtitle_auto_start'){demo.autoSync={id:'preview-job',session:demo.subtitleSession,index:m.index,state:'running',message:'Extraction de l’audio…'};setTimeout(()=>{demo.autoSync={...demo.autoSync,state:'proposed',offset:-1.8,message:'The same correction was confirmed in both halves of the sample.',analysedSeconds:480};},1500);}
if(m.action==='subtitle_auto_cancel')demo.autoSync={...demo.autoSync,state:'cancelled',message:'Analysis cancelled.'};
if(m.action==='subtitle_sync'){demo.subtitleTracks.forEach(t=>t.default=t.index===m.index);demo.subtitleTracks.find(t=>t.index===m.index).offset=m.offset;}
if(m.action==='seek')demo.position=m.position;
if(m.action==='toggle'){demo.paused=!demo.paused;demo.playbackState=demo.paused?'paused':'playing';}
if(m.action==='stop'){demo.casting=false;demo.position=0;}
if(m.action==='pair_forget'){demo.selectedPaired=false;demo.devices.find(d=>d.id===demo.selected).paired=false;demo.casting=false;}
if(m.action==='pair_begin')return {ok:true,result:{pinRequired:true}};
if(m.action==='pair_finish'){demo.selectedPaired=true;demo.devices.find(d=>d.id===demo.selected).paired=true;}
if(m.action==='cast'){demo.casting=true;demo.position=137;}
return {ok:true,result:structuredClone(demo)};
}}};
'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.partition('?')[0]
        if path == '/mock.js':
            data, mime = mock.encode(), 'text/javascript'
        elif path in ('/', '/popup.html'):
            data = (root / 'popup.html').read_text().replace('<script type="module"', '<script src="mock.js"></script><script type="module"').encode()
            mime = 'text/html'
        elif path in ('/popup.js', '/popup.css', '/design.css', '/player.js', '/subtitles.js', '/detection.js'):
            data = (root / path.lstrip('/')).read_bytes()
            mime = 'text/javascript' if path.endswith('.js') else 'text/css'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


server = HTTPServer(('127.0.0.1', 0), Handler)
print(f'http://127.0.0.1:{server.server_port}/popup.html', flush=True)
server.serve_forever()
