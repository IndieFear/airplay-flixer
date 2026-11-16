import test from 'node:test';
import assert from 'node:assert/strict';
import {parseManifest, describeStream, metadataRank, fetchManifest} from '../extension/stream-info.js';
const url = 'https://cdn.example/path/master.m3u8?secret=hidden';
test('master identifies qualities, quoted codecs and separate audio without claiming child has audio', () => {
  const parsed = parseManifest(`#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="French",LANGUAGE="fr",URI="sound.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="a",FRAME-RATE=25
high.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2",AUDIO="a"
low.m3u8`, url);
  assert.equal(parsed.info.role, 'master');
  assert.equal(parsed.info.audio, true);
  assert.deepEqual(parsed.info.qualities, [720,1080]);
  const child = parsed.children[0];
  assert.equal(child.url, 'https://cdn.example/path/high.m3u8');
  assert.equal(child.audio, false);
  assert.equal(child.externalAudio, true);
  assert.equal(child.codecs.length, 2);
  assert.match(describeStream({kind:'hls',info:child}).details, /Separate audio/);
  assert.ok(metadataRank(parsed.info) > metadataRank(child));
  assert.match(describeStream({kind:'hls',info:parsed.info}).label, /Auto.*1080p.*video \+ audio/);
  assert.equal(parsed.children.at(-1).role, 'audio');
});
test('muxed audio and URI-less audio rendition stay playable', () => {
  for (const group of ['', '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Original"\n']) {
    const result = parseManifest(`#EXTM3U\n${group}#EXT-X-STREAM-INF:RESOLUTION=1280x720,CODECS="avc1.4d001f,mp4a.40.2"${group ? ',AUDIO="a"' : ''}\nindex.m3u8`,url);
    assert.equal(result.children[0].audio,true);
  }
});
test('media playlists and missing declarations remain unknown; codec-declared video alone is explicit', () => {
  const media = parseManifest('#EXTM3U\n#EXTINF:6,\nsegment.ts',url);
  assert.equal(media.info.audio,null);
  assert.match(describeStream({kind:'hls',info:media.info}).label,/audio unconfirmed/);
  assert.equal(parseManifest('not a playlist',url),null);
  const video = parseManifest('#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080,CODECS="avc1.640028"\nv.m3u8',url);
  assert.equal(video.children[0].audio,false);
  assert.equal(video.info.audio,false);
});
test('fetch is bounded and failed access does not masquerade as missing audio',async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response('x'.repeat(512001));
    assert.equal(await fetchManifest(url),null);
    globalThis.fetch = async () => new Response('Forbidden',{status:403});
    assert.equal(await fetchManifest(url),null);
    globalThis.fetch = async (_url,options) => {
      assert.deepEqual(options.headers,{Authorization:'Bearer token'});
      assert.equal(options.redirect,'error');
      return new Response('#EXTM3U\n#EXTINF:6,\nsegment.ts');
    };
    assert.equal((await fetchManifest(url,{Authorization:'Bearer token',Cookie:'private'})).info.audio,null);
  } finally {globalThis.fetch=original;}
});
