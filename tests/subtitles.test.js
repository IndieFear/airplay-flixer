import test from 'node:test';
import assert from 'node:assert/strict';
import {subtitleLanguage,groupSubtitles} from '../extension/subtitles.js';

test('subtitle languages use declared metadata, ISO aliases and recognizable labels',()=>{
  for (const [track, expected] of [
    [{language:'fr-CA',label:'English'},'fr'],
    [{language:'fre'},'fr'],
    [{language:'und',label:'French (Spider)'},'fr'],
    [{language:'und',label:'Spider-Man.1080p.WEBRip.x264.fr-1791483012.srt'},'fr'],
    [{language:'und',label:'English (SDH)'},'en'],
    [{language:'und',label:'Portuguese (14097929)'},'pt'],
    [{language:'und',label:'Spider.Man.2160p.iT.WEB-DL.H265.MP4-BTM.srt'},'und'],
    [{language:'und',label:'Movie.No.Way.Home.1080p.srt'},'und'],
    [{language:'zh-Hant'},'zh'],
    [{language:'und',label:'subtitle-1791483012.srt'},'und']
  ]) assert.equal(subtitleLanguage(track),expected,JSON.stringify(track));
});
test('groups prioritize French and English, keep original cast indexes and omit duplicate URLs',()=>{
  const tracks=[
    {url:'a',language:'en',label:'English'},
    {url:'b',language:'fr',label:'Français',selected:true},
    {url:'c',language:'und',label:'Unknown'},
    {url:'d',language:'es',label:'Spanish'},
    {url:'e',language:'fr',label:'Movie.1080p.WEBRip.x264.fr-1791483012.srt'},
    {url:'b',language:'fr',label:'Duplicate'}
  ];
  const groups=groupSubtitles(tracks);
  assert.deepEqual(groups.map(g=>g.key),['fr','en','es','und']);
  assert.deepEqual(groups[0].tracks.map(t=>t.index),[1,4]);
  assert.equal(groups[0].tracks[0].selected,true);
  assert.match(groups[0].tracks[0].label,/active/);
  assert.equal(groups[0].tracks[1].fullLabel,tracks[4].label);
});

test('casts every subtitle of the chosen language with exactly one initial track',async()=>{
  const {subtitleSet}=await import('../extension/subtitles.js');
  const tracks=[{url:'https://a/fr1.srt',label:'French (A)',language:'fr'},{url:'https://b/en.srt',label:'English',language:'en'},{url:'https://c/fr2.srt',label:'French (B)',language:'fr'}];
  const result=subtitleSet(tracks,2);
  assert.deepEqual(result.tracks.map(t=>t.url),[tracks[0].url,tracks[2].url]);
  assert.equal(result.primary.url,tracks[2].url);
  assert.equal(result.tracks.filter(t=>t.default).length,1);
  assert.equal(subtitleSet(tracks,'auto').tracks.length,2);
  assert.deepEqual(subtitleSet(tracks,'off'),{primary:{off:true},tracks:[]});
  assert.throws(()=>subtitleSet(tracks,99));
});
