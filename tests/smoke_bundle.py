"""Exercise the actual frozen engine and Chrome host without pairing a TV."""
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import urllib.request
import wave

root = Path(__file__).resolve().parents[1]
engine = root / 'dist/Airplay Flixer.app/Contents/Resources/engine/FlixerEngine'
extension_id = (root / 'extension/extension-id.txt').read_text().strip()
checked = subprocess.run([str(engine), '--check-pairing'], capture_output=True, timeout=15, check=True)
assert b'SRP pairing compatibility: OK' in checked.stdout
print(checked.stdout.decode().strip())
checked_auto = subprocess.run([str(engine),'--check-autosync'],capture_output=True,timeout=30,check=True)
assert b'Auto-sync FFT and voice detector: OK' in checked_auto.stdout
print(checked_auto.stdout.decode().strip())
checked_power = subprocess.run([str(engine),'--check-power'],capture_output=True,timeout=15,check=True)
assert b'macOS idle sleep protection: OK' in checked_power.stdout
print(checked_power.stdout.decode().strip())
with tempfile.TemporaryDirectory(prefix='flixer-smoke-') as directory:
    folder = Path(directory)
    environment = {**os.environ, 'AIRPLAY_FLIXER_HOME': directory}
    (folder / 'installation.json').write_text(json.dumps({'extension_origin': f'chrome-extension://{extension_id}/'}))
    audio = folder / 'silent.wav'
    with wave.open(str(audio),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        output.writeframes(b'\0'*(16000*2*90))
    job = folder / 'autosync.json'
    job.write_text(json.dumps({'audio':str(audio),'intervals':[(i,i+2) for i in range(0,90,5)],'start':0}))
    worker = subprocess.run([str(engine),'--autosync-worker',str(job)],capture_output=True,env=environment,timeout=30,check=True)
    assert json.loads(worker.stdout)['state'] == 'uncertain', 'Frozen auto-sync worker failed'
    print('Frozen ffsubsync/NumPy/WebRTC worker: OK')
    process = subprocess.Popen([str(engine), '--serve'], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if (folder / 'runtime.json').exists():
                break
            if process.poll() is not None:
                raise RuntimeError(process.communicate()[1].decode())
            time.sleep(0.1)
        runtime = json.loads((folder / 'runtime.json').read_text())
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f'http://127.0.0.1:{runtime["port"]}/rpc', data=b'{"action":"status"}', headers={'Authorization': 'Bearer ' + runtime['token'], 'Content-Type': 'application/json'})
        with opener.open(request, timeout=10) as response:
            result = json.load(response)
        assert result['ok'] and result['result']['version'] == '0.10.1'
        assert result['result']['videoDownload'] == {'state':'idle'}
        assert result['result']['subdl'] == {'configured':False}
        assert result['result']['opensubtitles'] == {'configured':False,'connected':False,'username':''}
        missing_key = urllib.request.Request(f'http://127.0.0.1:{runtime["port"]}/rpc', data=json.dumps({'action':'os_search','query':'test','language':'fr','type':'movie'}).encode(), headers={'Authorization':'Bearer '+runtime['token'],'Content-Type':'application/json'})
        with opener.open(missing_key,timeout=10) as response:
            failed=json.load(response)
        assert not failed['ok'] and 'clé API' in failed['error']
        with opener.open(request,timeout=10) as response:
            assert json.load(response)['result']['error'] is None
        print('OpenSubtitles missing-key flow and playback error isolation: OK')
        subdl_request = urllib.request.Request(f'http://127.0.0.1:{runtime["port"]}/rpc', data=json.dumps({'action':'sd_search','query':'test','language':'fr','type':'movie'}).encode(), headers={'Authorization':'Bearer '+runtime['token'],'Content-Type':'application/json'})
        with opener.open(subdl_request,timeout=10) as response:
            failed=json.load(response)
        assert not failed['ok'] and 'clé API SubDL' in failed['error']
        with opener.open(request,timeout=10) as response:
            assert json.load(response)['result']['error'] is None
        print('SubDL missing-key flow and playback error isolation: OK')
        payload = json.dumps({'action': 'status'}).encode()
        wire = struct.pack('<I', len(payload)) + payload
        host = subprocess.run([str(engine), f'chrome-extension://{extension_id}/'], input=wire, capture_output=True, env=environment, timeout=15, check=True)
        length = struct.unpack('<I', host.stdout[:4])[0]
        response = json.loads(host.stdout[4:4+length])
        assert response['ok'] and response['result']['message'] == 'Prêt à caster'
        assert len(host.stdout) == length + 4, 'Diagnostics polluted Native Messaging stdout'
        denied = subprocess.run([str(engine), 'chrome-extension://not-authorized/'], input=wire, capture_output=True, env=environment, timeout=15, check=True)
        assert not json.loads(denied.stdout[4:])['ok']
        print('Frozen engine: OK · loopback RPC: OK · Chrome Native Messaging: OK · origin restriction: OK')
    finally:
        process.terminate()
        process.communicate(timeout=10)
    assert not (folder / 'runtime.json').exists(), 'Runtime token was not cleaned up'
    print('Graceful shutdown and token cleanup: OK')
