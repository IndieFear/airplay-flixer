"""Browser QA for the actual optional artwork converter, using a local PNG."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
root = Path(__file__).resolve().parents[1]
worker = '''import {fetchArtwork} from '/artwork.js';
onmessage=async()=>{const data=await fetchArtwork([location.origin+'/image.png']);postMessage(data);};'''
html = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Test affiche</title><button id="test">Tester la conversion JPEG</button><output id="result">En attente</output><script type="module">
import {fetchArtwork} from '/artwork.js';
document.querySelector('#test').onclick=async()=>{
 const worker=new Worker('/worker.js',{type:'module'});
 const data=await new Promise(resolve=>{worker.onmessage=e=>resolve(e.data);worker.postMessage('test');});worker.terminate();
 if(!data){document.querySelector('#result').textContent='Échec';return;}
 const bytes=Uint8Array.from(atob(data),c=>c.charCodeAt(0));
 const bitmap=await createImageBitmap(new Blob([bytes],{type:'image/jpeg'}));
 document.querySelector('#result').textContent=`JPEG valide : ${bitmap.width} × ${bitmap.height}, ${bytes.length} octets ; limite respectée : ${bytes.length<=180000}`;
 bitmap.close();
};</script></html>'''
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path=='/':data,mime=html.encode(),'text/html'
  elif self.path=='/worker.js':data,mime=worker.encode(),'text/javascript'
  elif self.path=='/artwork.js':data,mime=(root/'extension/artwork.js').read_bytes(),'text/javascript'
  elif self.path=='/image.png':data,mime=(root/'extension/icons/brand-128.png').read_bytes(),'image/png'
  else:self.send_error(404);return
  self.send_response(200);self.send_header('Content-Type',mime);self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',0),Handler)
print(f'http://127.0.0.1:{server.server_port}/',flush=True)
server.serve_forever()
