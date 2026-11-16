const aliases = {
  fra:'fr',fre:'fr',french:'fr','français':'fr',francais:'fr',eng:'en',english:'en',anglais:'en',
  spa:'es',spanish:'es',espagnol:'es',deu:'de',ger:'de',german:'de',allemand:'de',ita:'it',italian:'it',italien:'it',
  por:'pt',portuguese:'pt',portugais:'pt',pob:'pt',nld:'nl',dut:'nl',dutch:'nl',rus:'ru',russian:'ru',
  ara:'ar',arabic:'ar',jpn:'ja',japanese:'ja',kor:'ko',korean:'ko',zho:'zh',chi:'zh',chinese:'zh',
  dan:'da',danish:'da',swe:'sv',swedish:'sv',nor:'no',norwegian:'no',fin:'fi',finnish:'fi',
  pol:'pl',polish:'pl',ron:'ro',rum:'ro',romanian:'ro',tur:'tr',turkish:'tr',ces:'cs',cze:'cs',czech:'cs',
  hun:'hu',hungarian:'hu',ell:'el',gre:'el',greek:'el',heb:'he',hebrew:'he',ind:'id',indonesian:'id',
  vie:'vi',vietnamese:'vi',tha:'th',thai:'th',ukr:'uk',ukrainian:'uk',bul:'bg',bulgarian:'bg',
  hrv:'hr',croatian:'hr',srp:'sr',serbian:'sr',slk:'sk',slo:'sk',slovak:'sk',slv:'sl',slovenian:'sl',
  est:'et',estonian:'et',lav:'lv',latvian:'lv',lit:'lt',lithuanian:'lt',isl:'is',ice:'is',icelandic:'is',
  fas:'fa',per:'fa',persian:'fa',ben:'bn',bengali:'bn',msa:'ms',may:'ms',malay:'ms',sqi:'sq',alb:'sq',albanian:'sq',mya:'my',bur:'my',burmese:'my'
};
const names = new Intl.DisplayNames(['fr'], {type:'language'});
const known = new Set(('fr en es de it pt nl ru ar ja ko zh da sv no fi pl ro tr cs hu el he id vi th uk bg hr sr sk sl et lv lt is fa bn ms sq my mn ka hy az eu ca gl af sw ta te hi ur ne si km lo').split(' '));
export function subtitleLanguage(track) {
  const declared = String(track.language || '').toLowerCase().replaceAll('_','-').split('-')[0];
  if (known.has(declared)) return declared;
  if (aliases[declared]) return aliases[declared];
  const label = String(track.label || '').toLowerCase();
  // Whole tokens only: never infer "en" from a word or a movie title.
  const tokens = label.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  for (const token of tokens) if (aliases[token]) return aliases[token];
  const suffix = label.match(/(?:^|[._ -])([a-z]{2})(?=[._ -](?:srt|vtt|sdh|hi|[0-9]{8,})|$)/g) || [];
  for (const part of suffix) { const code = part.replace(/^[._ -]/, ''); if (known.has(code)) return code; }
  return 'und';
}
function trackLabel(track, n) {
  const full = String(track.label || 'Sous-titres');
  const features = [...new Set(full.match(/WEB[- .]?DL|WEBRip|BluRay|TELESYNC|CAMRip|SDH|\bHI\b|x26[45]|H[ .]?26[45]|HDR10|\b4K\b|1080p|720p|2160p/gi) || [])];
  const short = full.length <= 52 ? full : features.length ? features.slice(0,4).join(' · ') : full.slice(0,49)+'…';
  return `Piste ${n + 1} · ${short}${track.selected ? ' · active' : ''}`;
}
export function groupSubtitles(tracks) {
  const groups = new Map(), urls = new Set();
  tracks.forEach((track,index) => {
    if (track.url && urls.has(track.url)) return;
    if (track.url) urls.add(track.url);
    const key = subtitleLanguage(track);
    if (!groups.has(key)) groups.set(key, {key, label:key === 'und' ? 'Langue non précisée' : names.of(key), tracks:[]});
    const group = groups.get(key);
    group.tracks.push({index, label:trackLabel(track,group.tracks.length), fullLabel:track.label || 'Sous-titres', selected:!!track.selected});
  });
  const priority = key => key === 'fr' ? 0 : key === 'en' ? 1 : key === 'und' ? 3 : 2;
  return [...groups.values()].sort((a,b) => priority(a.key)-priority(b.key) || a.label.localeCompare(b.label,'fr'));
}

// Keep every rendition of the chosen language, with one initial selection.
export function subtitleSet(tracks, selection = 'auto') {
  if (selection === 'off') return {primary:{off:true}, tracks:[]};
  const chosen = typeof selection === 'number' ? tracks[selection] : tracks.find(t => t.selected) || tracks.find(t => subtitleLanguage(t) === 'fr');
  if (typeof selection === 'number' && !chosen) throw new Error('Cette piste n’est plus disponible. Actualise la liste des sous-titres.');
  if (!chosen) return {primary:null, tracks:[]};
  const group = groupSubtitles(tracks).find(g => g.key === subtitleLanguage(chosen));
  const initial = group.tracks.find(t => tracks[t.index] === chosen || chosen.url && tracks[t.index].url === chosen.url) || group.tracks[0];
  const renditions = group.tracks.map(t => ({...tracks[t.index], label:t.label.replace(/ · active$/, ''), language:group.key, default:t.index === initial.index}));
  return {primary:renditions.find(t => t.default), tracks:renditions};
}
