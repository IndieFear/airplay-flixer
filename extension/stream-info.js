// HLS declarations describe variants; absence of a declaration is not proof of silence.
function attributes(line) {
  return Object.fromEntries([...line.slice(line.indexOf(':') + 1).matchAll(/([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))/g)].map(m => [m[1], m[2] ?? m[3]]));
}
function resolve(value, base) { try { const url = new URL(value, base); return /^https?:$/.test(url.protocol) ? url.href : null; } catch { return null; } }
export function parseManifest(body, url) {
  if (!body.trimStart().startsWith('#EXTM3U')) return null;
  const lines = body.split(/\r?\n/).map(l => l.trim());
  const renditions = lines.filter(l => l.startsWith('#EXT-X-MEDIA:')).map(attributes);
  const children = [];
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].startsWith('#EXT-X-STREAM-INF:')) continue;
    const a = attributes(lines[i]);
    const next = lines.slice(i + 1).find(l => l && !l.startsWith('#'));
    const childUrl = next && resolve(next, url);
    if (!childUrl) continue;
    const codecs = (a.CODECS || '').split(',').filter(Boolean);
    const audioCodec = codecs.some(c => /^(mp4a|ac-3|ec-3|opus|flac|alac)/i.test(c));
    const audioGroup = renditions.filter(r => r.TYPE === 'AUDIO' && r['GROUP-ID'] === a.AUDIO);
    const externalAudio = audioGroup.length > 0 && audioGroup.every(r => r.URI);
    const height = Number(a.RESOLUTION?.split('x')[1]) || 0;
    children.push({url: childUrl, role: height || codecs.some(c => /^(avc|hvc|hev|av01|vp0)/i.test(c)) ? 'video' : audioCodec && !a.AUDIO ? 'audio' : 'variant', height, codecs, bitrate: Number(a['AVERAGE-BANDWIDTH'] || a.BANDWIDTH) || 0, fps: Number(a['FRAME-RATE']) || 0, audio: externalAudio ? false : audioCodec ? true : codecs.length && !a.AUDIO ? false : null, externalAudio, languages: audioGroup.map(r => r.LANGUAGE || r.NAME).filter(Boolean)});
  }
  for (const r of renditions) if (r.URI) {
    const childUrl = resolve(r.URI, url);
    if (childUrl) children.push({url: childUrl, role: r.TYPE === 'AUDIO' ? 'audio' : r.TYPE === 'SUBTITLES' ? 'subtitle' : 'rendition', languages: [r.LANGUAGE || r.NAME].filter(Boolean)});
  }
  const variants = children.filter(c => c.role === 'video' || c.role === 'variant');
  return {info: variants.length ? {role: 'master', height: Math.max(...variants.map(c => c.height)), audio: variants.some(c => c.audio === true || c.externalAudio) ? true : variants.every(c => c.audio === false) ? false : null, languages: [...new Set(variants.flatMap(c => c.languages))], codecs: [...new Set(variants.flatMap(c => c.codecs))], qualities: [...new Set(variants.map(c => c.height).filter(Boolean))].sort((a,b) => a-b)} : {role: 'playlist', audio: null}, children};
}
export function describeStream(candidate, playerHeight) {
  const i = candidate.info || {};
  const quality = i.height ? `${i.height >= 2160 ? '4K · ' : ''}${i.height}p` : '';
  const observed = !quality && playerHeight > 0 ? `${playerHeight}p in browser` : '';
  const sound = i.role === 'audio' ? 'audio only' : i.audio === true ? 'video + audio' : i.audio === false ? 'video only' : '';
  const kind = candidate.kind.toUpperCase();
  const format = i.role === 'master' ? 'Auto' : candidate.kind === 'hls' ? 'HLS stream' : kind;
  const label = [format, i.role === 'master' && quality ? `up to ${quality}` : quality || observed, sound].filter(Boolean).join(' · ');
  const explanation = i.role === 'master' ? 'Adaptive stream. The receiver can switch quality during playback.'
    : i.externalAudio ? 'Separate audio. Choose the master stream for video and audio.'
    : i.role === 'audio' ? 'Audio track only.'
    : i.role === 'video' || i.role === 'variant' ? 'Fixed quality variant.' : '';
  const details = [
    explanation,
    observed ? 'Resolution measured in the browser. The receiver may play a different quality.' : '',
    !quality && !observed && i.role !== 'audio' ? 'Resolution is not declared or could not be read.' : '',
    !sound ? 'Audio is not declared or could not be verified.' : '',
    i.qualities?.length ? `Qualities: ${i.qualities.map(h => `${h}p`).join(', ')}.` : '',
    i.bitrate ? `${(i.bitrate / 1e6).toFixed(1)} Mb/s` : '',
    i.fps ? `${i.fps} fps` : '',
    i.languages?.length ? `Audio: ${[...new Set(i.languages)].join(', ')}` : '',
    i.codecs?.length ? `Codecs: ${i.codecs.join(', ')}` : '',
  ].filter(Boolean).join(' ');
  return {label, details};
}
export function metadataRank(info = {}) {
  return info.role === 'audio' ? -200 : info.role === 'master' ? (info.audio === false ? 15 : 100) : info.audio === false ? -80 : info.audio === true ? 25 : 0;
}

export async function fetchManifest(url, headers = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);
  let reader;
  try {
    // Cookies are managed by the browser; never copy another origin’s credentials.
    const authorization = Object.entries(headers).find(([k]) => k.toLowerCase() === 'authorization');
    const response = await fetch(url, {credentials: 'include', signal: controller.signal, headers: authorization ? {Authorization: authorization[1]} : {}, redirect: 'error'});
    if (!response.ok || !response.body) return null;
    reader = response.body.getReader();
    const decoder = new TextDecoder(); let body = '', size = 0;
    while (true) {
      const {value, done} = await reader.read(); if (done) break;
      size += value.byteLength; if (size > 512000) return null;
      body += decoder.decode(value, {stream: true});
    }
    return parseManifest(body + decoder.decode(), url);
  } catch { return null; }
  finally { clearTimeout(timer); if (reader) await reader.cancel().catch(() => {}); }
}

export function probedSourceLabel(source, {video, audio, variant}) {
  const names = {h264:"H.264", hevc:"H.265", av1:"AV1", aac:"AAC", ac3:"AC-3", eac3:"E-AC-3", opus:"Opus"};
  const codec = name => names[name] || name?.toUpperCase();
  const adaptive = variant || source.info?.role === "master";
  const height = adaptive ? source.info?.height || video?.height : video?.height;
  const sound = [...new Set(audio.map(track => [
    codec(track.codec_name), track.channels ? `${track.channels} ch` : ""
  ].filter(Boolean).join(" ")).filter(Boolean))];
  return [
    adaptive ? "Auto" : source.kind.toUpperCase(),
    height ? `${adaptive ? "up to " : ""}${height}p` : "",
    codec(video?.codec_name),
    source.info?.externalAudio ? "separate audio" : sound.join(", ") || (video ? "audio not detected" : ""),
    !video && sound.length ? "audio only" : "",
  ].filter(Boolean).join(" · ");
}
