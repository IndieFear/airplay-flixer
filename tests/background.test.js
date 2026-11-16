import test from "node:test";
import assert from "node:assert/strict";

const event = () => ({listeners: [], addListener(fn) { this.listeners.push(fn); }});
const nativeCalls = [];
const iconCalls = [], titleCalls = [];
const extensionId = "aoebnkjhmejafecljmmpmofdgpmjgmib";
const requestHeaders = event(), beforeRequest = event(), responseHeaders = event(), onMessage = event(), requestErrors = event();
globalThis.chrome = {
  storage: {session: {get: async () => ({}), set: async () => {}, remove: async () => {}}},
  webRequest: {onBeforeRequest: beforeRequest, onHeadersReceived: responseHeaders, onSendHeaders: requestHeaders, onErrorOccurred: requestErrors},
  tabs: {onRemoved: event(), query: async () => [{id: 12}]},
  scripting: {executeScript: async () => [{frameId: 0, result: {title: "Mon épisode", videos: [{index: 0, src: "blob:https://page.example/id", sources: [], position: 137, duration: 3000, playing: true, area: 1920 * 1080, tracks: [{url: "https://subs.example/fr.srt", label: "French", language: "fr", selected: true}]}]}}]},
  runtime: {id: extensionId, getURL: path => `chrome-extension://${extensionId}/${path}`, onMessage, sendNativeMessage: (_host, message, cb) => {
    nativeCalls.push(message);
    cb({ok: true, result: {casting: message.action === "cast", confirmed: false, devices: []}});
  }},
  commands: {onCommand: event()},
  action: {setIcon: async value => {iconCalls.push(value);}, setTitle: async value => {titleCalls.push(value);}, setBadgeText: () => {}, setBadgeBackgroundColor: () => {}}
};
globalThis.fetch = async () => { throw new Error("Manifest unavailable in this fixture"); };
await import("../extension/background.js");
const sender = {id: extensionId, url: `chrome-extension://${extensionId}/popup.html`};
function message(value) { return new Promise(resolve => onMessage.listeners[0](value, sender, resolve)); }

test("web pages cannot trigger native commands through extension messaging", () => {
  const before = nativeCalls.length;
  for (const hostile of [{id: extensionId, tab: {id: 12}, url: "https://page.example"}, {id: "other", url: sender.url}, {id: extensionId, url: "https://page.example"}]) {
    assert.equal(onMessage.listeners[0]({action: "scan"}, hostile, () => assert.fail("must not reply")), undefined);
  }
  assert.equal(nativeCalls.length, before);
});

test('extension controller forwards a valid seek and rejects invalid positions', async () => {
  const result = await message({action: 'seek', position: 300});
  assert.equal(result.ok, true);
  assert.deepEqual(nativeCalls.at(-1), {action: 'seek', position: 300});
  const count = nativeCalls.length;
  for (const position of [-10, NaN, Infinity, '300', null]) {
    const rejected = await message({action: 'seek', position});
    assert.equal(rejected.ok, false);
  }
  assert.equal(nativeCalls.length, count);
});

test("casts the real HLS behind a blob, retains position and limits credentials to media origins", async () => {
  const source = "https://cdn.example/master.m3u8?signature=private";
  beforeRequest.listeners[0]({tabId: 12, frameId: 0, type: "xmlhttprequest", url: source});
  for (const [url, cookie] of [[source, "media-session"], ["https://subs.example/fr.srt", "subtitle-session"], ["https://analytics.example/ping", "unrelated-secret"]]) {
    requestHeaders.listeners[0]({tabId: 12, type: "xmlhttprequest", url, requestHeaders: [{name: "Cookie", value: cookie}, {name: "Referer", value: "https://page.example/"}]});
  }
  const result = await message({action: "cast", tabId: 12, url: source, subtitle: "auto"});
  assert.equal(result.ok, true);
  const cast = nativeCalls.find(c => c.action === "cast");
  assert.equal(cast.media.url, source);
  assert.equal(cast.media.position, 137);
  assert.equal(cast.media.subtitle.language, "fr");
  assert.equal(cast.media.headersByOrigin["https://cdn.example"].Cookie, "media-session");
  assert.equal(cast.media.headersByOrigin["https://subs.example"].Cookie, "subtitle-session");
  assert.equal(cast.media.headersByOrigin["https://analytics.example"], undefined);
  const download = await message({action: "video_download_start", tabId: 12, url: source});
  assert.equal(download.ok, true);
  assert.deepEqual(nativeCalls.at(-1), {action: "video_download_start", media: {
    url: source, kind: "hls", title: "Mon épisode",
    headersByOrigin: {"https://cdn.example": {Cookie: "media-session", Referer: "https://page.example/"}}
  }});
  await message({action: "video_download_start", tabId: 12, url: source, subtitle: 0});
  const media = nativeCalls.at(-1).media;
  assert.equal(media.subtitle.url, "https://subs.example/fr.srt");
  assert.equal(media.headersByOrigin["https://subs.example"].Cookie, "subtitle-session");
  assert.equal(media.headersByOrigin["https://analytics.example"], undefined);
});

test('inspection prioritizes a complete master over newer silent variants and caches analysis', async () => {
  let reads = 0;
  globalThis.fetch = async url => {
    reads++;
    return new Response(url.endsWith('/master.m3u8') ? '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",LANGUAGE="fr",URI="audio.m3u8"\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="a"\nvideo.m3u8' : '#EXTM3U\n#EXTINF:6,\nsegment.ts');
  };
  for (const name of ['master','video','audio']) beforeRequest.listeners[0]({tabId:99,frameId:0,type:'xmlhttprequest',url:`https://media.example/${name}.m3u8`});
  const response = await message({action:'inspect',tabId:99});
  assert.equal(response.ok,true);
  assert.equal(response.result.sources[0].info.role,'master');
  assert.match(response.result.sources.find(s => s.url.endsWith('/video.m3u8')).label,/video only/);
  assert.match(response.result.sources.at(-1).label,/audio only/);
  await message({action:'inspect',tabId:99});
  assert.equal(reads,3);
});

test('toolbar becomes ready per tab, ignores subtitles and resets on navigation or failed media', () => {
  const request = (tabId,url,requestId) => beforeRequest.listeners[0]({tabId,url,requestId,frameId:0,type:'xmlhttprequest'});
  beforeRequest.listeners[0]({tabId:301,url:'https://page.example/a',type:'main_frame'});
  assert.match(iconCalls.at(-1).path[32],/idle-32/);
  request(301,'https://cdn.example/subtitles.vtt','sub');
  assert.match(iconCalls.at(-1).path[32],/idle-32/);
  request(301,'https://cdn.example/audio/playlist.m3u8','audio');
  assert.match(iconCalls.at(-1).path[32],/idle-32/);
  request(301,'https://cdn.example/master.m3u8','video');
  assert.equal(iconCalls.at(-1).tabId,301);
  assert.match(iconCalls.at(-1).path[32],/ready-32/);
  assert.match(titleCalls.at(-1).title,/Video stream found/);
  responseHeaders.listeners[0]({tabId:301,url:'https://cdn.example/master.m3u8',requestId:'video',statusCode:403});
  assert.match(iconCalls.at(-1).path[32],/idle-32/);
  request(301,'https://cdn.example/movie.mp4','mp4');
  assert.match(iconCalls.at(-1).path[32],/ready-32/);
  requestErrors.listeners[0]({tabId:301,url:'https://cdn.example/movie.mp4',requestId:'mp4'});
  assert.match(iconCalls.at(-1).path[32],/idle-32/);
  request(302,'https://cdn.example/movie.mp4','other');
  assert.equal(iconCalls.at(-1).tabId,302);
  beforeRequest.listeners[0]({tabId:302,url:'https://page.example/b',type:'main_frame'});
  assert.equal(iconCalls.at(-1).tabId,302);
  assert.match(iconCalls.at(-1).path[32],/idle-32/);
});

test('subtitle synchronization forwards only validated explicit settings',async()=>{
  const result=await message({action:'subtitle_sync',session:'local-session',index:1,offset:-.5});
  assert.equal(result.ok,true);
  assert.deepEqual(nativeCalls.at(-1),{action:'subtitle_sync',session:'local-session',index:1,offset:-.5});
  const count=nativeCalls.length;
  for(const offset of [NaN,Infinity,'0.5',601])assert.equal((await message({action:'subtitle_sync',session:'local-session',index:1,offset})).ok,false);
  assert.equal(nativeCalls.length,count);
});

test('downloaded OpenSubtitles tracks survive popup inspection and clear on navigation', async () => {
  const previous = chrome.runtime.sendNativeMessage;
  chrome.runtime.sendNativeMessage = (_host, value, cb) => {
    if(value.action==='os_download')cb({ok:true,result:{id:'download-id',remaining:4,track:{provider:'opensubtitles',fileId:12,content:'1\n00:00:01,000 --> 00:00:03,000\nBonjour',label:'OpenSubtitles · WEB-DL',language:'fr'}}});
    else previous(_host,value,cb);
  };
  try {
    const first=await message({action:'os_download',tabId:12,fileId:12});
    assert.equal(first.ok,true);
    assert.equal(first.result.page.recommendedSubtitle,0);
    const inspect=await message({action:'inspect',tabId:12});
    assert.equal(inspect.result.subtitles[0].provider,'opensubtitles');
    assert.match(inspect.result.subtitles[0].content,/Bonjour/);
    beforeRequest.listeners[0]({tabId:12,type:'main_frame',url:'https://page.example/other',frameId:0});
    const next=await message({action:'inspect',tabId:12});
    assert.equal(next.result.subtitles.some(t=>t.provider==='opensubtitles'),false);
  } finally {chrome.runtime.sendNativeMessage=previous;}
});

test('SubDL imports use their own provider identity alongside OpenSubtitles', async () => {
  const previous=chrome.runtime.sendNativeMessage;
  chrome.runtime.sendNativeMessage=(_host,value,cb)=>{
    if(['sd_download','os_download'].includes(value.action))cb({ok:true,result:{id:'download',track:{provider:value.action==='sd_download'?'subdl':'opensubtitles',fileId:12,content:'1\n00:00:01,000 --> 00:00:02,000\nBonjour',label:'WEB-DL',language:'fr'}}});
    else previous(_host,value,cb);
  };
  try {
    await message({action:'os_download',tabId:12,fileId:12});
    const result=await message({action:'sd_download',tabId:12,fileId:12});
    assert.equal(result.ok,true);
    assert.deepEqual(result.result.page.subtitles.filter(t=>t.provider).map(t=>t.provider),['subdl','opensubtitles']);
    assert.equal(result.result.page.recommendedSubtitle,0);
  } finally {chrome.runtime.sendNativeMessage=previous;}
});
