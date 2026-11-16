export function mediaKind(url, mime = "") {
  let path;
  try { path = new URL(url).pathname.toLowerCase(); } catch { return null; }
  if (!/^https?:/.test(url)) return null;
  mime = mime.toLowerCase().split(";")[0].trim();
  if (/\.(vtt|srt)$/.test(path) || ["text/vtt", "application/x-subrip"].includes(mime)) return "subtitle";
  if (/\.m3u8$/.test(path) || /mpegurl/.test(mime)) return /(?:subtitle|caption|\/subs[\/_.-])/.test(path) ? "subtitle-hls" : "hls";
  if (/\.mpd$/.test(path) || mime === "application/dash+xml") return "dash";
  if (/\.(mkv)$/.test(path) || mime === "video/x-matroska") return "mkv";
  if (/\.webm$/.test(path) || mime === "video/webm") return "webm";
  if (/(?:^|\/)(?:init|segment|chunk|frag)[-_.\d]/.test(path)) return null;
  if (/\.(mp4|m4v|mov)$/.test(path) || ["video/mp4", "video/quicktime"].includes(mime)) return "mp4";
  return null;
}

export function rank(candidate) {
  let value = {hls: 100, mp4: 80, mkv: 65, webm: 55, dash: 50}[candidate.kind] ?? 0;
  if (/master|playlist|index/i.test(candidate.url)) value += 10;
  if (/audio[-_.\/]|\/audio\/|trailer|preview|advert/i.test(candidate.url)) value -= 70;
  if (candidate.fromVideo) value += 40;
  return value;
}

export function downloadable(candidate) {
  return ["hls", "mp4", "mkv", "webm"].includes(candidate?.kind) && candidate.info?.role !== "audio";
}

export function labelFor(url, index) {
  try {
    const path = decodeURIComponent(new URL(url).pathname);
    return path.split("/").filter(Boolean).at(-1)?.slice(0, 72) || `Source ${index + 1}`;
  } catch { return `Source ${index + 1}`; }
}

// This function is serialized into the selected tab. Keep it self-contained.
export function inspectPage() {
  const time = value => {
    const ms = Math.round(Math.max(0, value) * 1000);
    return `${String(Math.floor(ms / 3600000)).padStart(2, "0")}:${String(Math.floor(ms / 60000) % 60).padStart(2, "0")}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
  };
  const artwork = [
    ...(globalThis.navigator?.mediaSession?.metadata?.artwork || []).map(image => image.src),
    document.querySelector?.('meta[property="og:image"]')?.content,
    document.querySelector?.('meta[name="twitter:image"]')?.content,
    ...[...document.querySelectorAll("video")].map(video => video.poster),
  ].filter(Boolean).map(value => { try { return new URL(value, document.baseURI).href; } catch { return ""; } }).filter(url => /^https?:/.test(url));
  return {
    artwork,
    title: document.title,
    mediaTitle: globalThis.navigator?.mediaSession?.metadata?.title || document.querySelector?.('meta[property="og:title"]')?.content || "",
    videos: [...document.querySelectorAll("video")].map((video, index) => {
      const tracks = [...video.querySelectorAll("track")].filter(t => ["subtitles", "captions"].includes(t.kind)).map(t => ({url: t.src, label: t.label || t.srclang || "Subtitles", language: t.srclang || "und", selected: t.track?.mode === "showing"}));
      for (const track of Array.from(video.textTracks)) {
        if (!["subtitles", "captions"].includes(track.kind)) continue;
        const existing = tracks.find(t => t.label === track.label && t.language === track.language);
        if (existing) { existing.selected = track.mode === "showing"; continue; }
        if (!track.cues?.length) continue;
        const content = "WEBVTT\n\n" + Array.from(track.cues).map(cue => `${time(cue.startTime)} --> ${time(cue.endTime)}\n${cue.text || ""}\n`).join("\n");
        if (content.length < 350000) tracks.push({content, label: track.label || track.language || "Player subtitles", language: track.language || "und", selected: track.mode === "showing"});
      }
      return {index, src: video.currentSrc || video.src, sources: [...video.querySelectorAll("source")].map(s => s.src), position: video.currentTime || 0, duration: Number.isFinite(video.duration) ? video.duration : 0, playing: !video.paused, protected: !!video.mediaKeys, area: video.videoWidth * video.videoHeight || video.clientWidth * video.clientHeight, tracks};
    })
  };
}
