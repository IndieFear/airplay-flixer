import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {hasCastableVideo,iconPaths} from '../extension/action-state.js';
test('toolbar detects video rather than DASH, subtitles or audio renditions',()=>{
  assert.equal(hasCastableVideo([{kind:'hls',url:'https://cdn.example/master.m3u8'}]),true);
  for (const candidate of [
    {kind:'dash',url:'https://cdn.example/movie.mpd'},
    {kind:'subtitle',url:'https://cdn.example/fr.vtt'},
    {kind:'hls',url:'https://cdn.example/audio/playlist.m3u8'},
    {kind:'hls',url:'https://cdn.example/playlist.m3u8',info:{role:'audio'}},
  ]) assert.equal(hasCastableVideo([candidate]),false);
});
test('manifest and dynamic toolbar assets exist as actual PNGs at each declared size',()=>{
  const manifest=JSON.parse(readFileSync(new URL('../extension/manifest.json',import.meta.url)));
  for (const paths of [manifest.icons,manifest.action.default_icon,iconPaths(true),iconPaths(false)]) {
    for (const [size,path] of Object.entries(paths)) {
      const png=readFileSync(new URL('../extension/'+path,import.meta.url));
      assert.deepEqual([...png.subarray(0,8)],[137,80,78,71,13,10,26,10]);
      assert.equal(png.readUInt32BE(16),Number(size));
      assert.equal(png.readUInt32BE(20),Number(size));
    }
  }
});
