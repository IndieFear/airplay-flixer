export function hasCastableVideo(candidates = []) {
  return candidates.some(c => {
    if (!['hls','mp4','mkv','webm'].includes(c.kind) || ['audio','subtitle','rendition'].includes(c.info?.role)) return false;
    try {
      const url = new URL(c.url);
      return /^https?:$/.test(url.protocol) && !/(?:^|[\/_.-])(?:audio|subtitle|caption|advert|preview)(?:[\/_.-]|$)/i.test(url.pathname);
    } catch { return false; }
  });
}
export function iconPaths(available) {
  return Object.fromEntries([16,24,32,48,64,128].map(size => [size, `icons/${available ? 'ready' : 'idle'}-${size}.png`]));
}
