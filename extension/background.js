import {mediaKind, rank, labelFor, inspectPage, downloadable} from "./detection.js";

import {fetchManifest, describeStream, metadataRank} from "./stream-info.js";

import {fetchArtwork} from "./artwork.js";

import {subtitleSet} from "./subtitles.js";

import {hasCastableVideo, iconPaths} from "./action-state.js";

const HOST = "com.airplay_flixer.companion";
const tabs = new Map();
const ready = chrome.storage.session.get(null).then(saved => {
  for (const [key, value] of Object.entries(saved)) if (key.startsWith("tab-") && Date.now() - value.updated < 6 * 3600 * 1000 && !tabs.has(Number(key.slice(4)))) {
    const id = Number(key.slice(4)); tabs.set(id, value); updateAction(id);
  }
});
const pending = new Set();
const actionStates = new Map();
function updateAction(tabId, detected) {
  const state = tabs.get(tabId);
  const available = detected ?? !!(state?.domVideo || hasCastableVideo(state?.candidates));
  if (actionStates.get(tabId) === available) return;
  actionStates.set(tabId, available);
  Promise.all([
    chrome.action.setIcon({tabId, path: iconPaths(available)}),
    chrome.action.setTitle({tabId, title: available ? "Flux vidéo détecté · Ouvrir pour caster" : "Airplay Flixer · Aucun flux vidéo détecté"})
  ]).catch(() => {}); // A tab can close between detection and the toolbar update.
}
function stateFor(tabId) {
  if (!tabs.has(tabId)) tabs.set(tabId, {candidates: [], headers: {}, updated: Date.now()});
  return tabs.get(tabId);
}
function persist(tabId) {
  if (pending.has(tabId)) return;
  pending.add(tabId);
  setTimeout(() => {
    pending.delete(tabId);
    const state = tabs.get(tabId);
    if (state) chrome.storage.session.set({[`tab-${tabId}`]: state});
  }, 200);
}
function observe(details, mime = "") {
  if (details.tabId < 0) return;
  const kind = mediaKind(details.url, mime);
  if (!kind) return;
  const state = stateFor(details.tabId);
  const existing = state.candidates.find(item => item.url === details.url);
  if (existing) { existing.kind = kind; existing.seen = Date.now(); existing.requestId = details.requestId; }
  else state.candidates.push({url: details.url, kind, seen: Date.now(), frameId: details.frameId, requestId: details.requestId});
  state.candidates = state.candidates.slice(-100);
  state.updated = Date.now();
  persist(details.tabId);
  updateAction(details.tabId);
}
chrome.webRequest.onBeforeRequest.addListener(details => {
  if (details.tabId >= 0 && details.type === "main_frame") {
    tabs.set(details.tabId, {candidates: [], headers: {}, updated: Date.now()});
    updateAction(details.tabId, false);
    chrome.action.setBadgeText({tabId: details.tabId, text: ""});
    chrome.storage.session.remove(`tab-${details.tabId}`);
  }
  observe(details);
}, {urls: ["http://*/*", "https://*/*"]});
function forgetFailedRequest(details) {
  const state = tabs.get(details.tabId);
  if (!state) return;
  const current = state.candidates.find(c => c.url === details.url);
  if (current && current.requestId !== details.requestId) return;
  state.candidates = state.candidates.filter(c => c.url !== details.url);
  if (state.domSources?.includes(details.url)) {
    state.domSources = state.domSources.filter(url => url !== details.url);
    state.domVideo = state.domSources.length > 0;
  }
  persist(details.tabId); updateAction(details.tabId);
}
chrome.webRequest.onHeadersReceived.addListener(details => {
  if (details.statusCode >= 400) { forgetFailedRequest(details); return; }
  observe(details, details.responseHeaders?.find(h => h.name.toLowerCase() === "content-type")?.value || "");
}, {urls: ["http://*/*", "https://*/*"]}, ["responseHeaders"]);
chrome.webRequest.onErrorOccurred.addListener(forgetFailedRequest, {urls: ["http://*/*", "https://*/*"]});
chrome.webRequest.onSendHeaders.addListener(details => {
  if (details.tabId < 0 || !["media", "xmlhttprequest", "other"].includes(details.type)) return;
  const state = stateFor(details.tabId);
  const origin = new URL(details.url).origin;
  const headers = {};
  for (const header of details.requestHeaders || []) if (["referer", "origin", "user-agent", "cookie", "authorization"].includes(header.name.toLowerCase()) && header.value?.length < 16384) headers[header.name] = header.value;
  // Credentials stay in session memory and are forwarded only to relevant media origins.
  if (Object.keys(headers).length) {
    state.headers[origin] = headers;
    const origins = Object.keys(state.headers);
    if (origins.length > 40) delete state.headers[origins[0]];
    if (state.candidates.length) persist(details.tabId);
  }
}, {urls: ["http://*/*", "https://*/*"]}, ["requestHeaders", "extraHeaders"]);
chrome.tabs.onRemoved.addListener(id => { tabs.delete(id); actionStates.delete(id); chrome.storage.session.remove(`tab-${id}`); });

function native(message) {
  return new Promise((resolve, reject) => chrome.runtime.sendNativeMessage(HOST, message, result => {
    if (chrome.runtime.lastError) return reject(new Error("Compagnon indisponible. Ouvre Airplay Flixer sur le Mac, puis réessaie."));
    if (!result?.ok) return reject(new Error(result?.error || "Le compagnon n’a pas répondu."));
    resolve(result.result);
  }));
}

const analyses = new WeakMap();
async function enrich(candidates, state) {
  const hls = candidates.filter(c => c.kind === "hls").sort((a,b) => rank(b)-rank(a)).slice(0, 12);
  // Four concurrent bounded reads; cache belongs to this tab and its credentials.
  const results = new Map();
  let index = 0;
  await Promise.all(Array.from({length: Math.min(4, hls.length)}, async () => {
    while (index < hls.length) {
      const c = hls[index++];
      const key = c.url;
      state.manifests ||= {};
      for (const old of Object.keys(state.manifests)) if (!hls.some(item => item.url === old)) delete state.manifests[old];
      let cached = state.manifests[key];
      if (!cached || Date.now() - cached.time > (cached.value ? 300000 : 15000)) {
        let jobs = analyses.get(state);
        if (!jobs) { jobs = new Map(); analyses.set(state, jobs); }
        if (!jobs.has(key)) jobs.set(key, fetchManifest(key, state.headers[new URL(key).origin]).finally(() => jobs.delete(key)));
        cached = {time: Date.now(), value: await jobs.get(key)};
        state.manifests[key] = cached;
      }
      if (cached.value) results.set(key, cached.value);
    }
  }));
  for (const c of candidates) if (results.has(c.url)) c.info = results.get(c.url).info;
  // The parent declaration is authoritative for separate audio and variant quality.
  for (const [parent, result] of results) for (const child of result.children) {
    const c = candidates.find(c => c.url === child.url);
    if (c && c.info?.role !== "master") c.info = {...child, parent};
  }
}

async function inspect(tabId) {
  await ready;
  let frames = [];
  try { frames = await chrome.scripting.executeScript({target: {tabId, allFrames: true}, func: inspectPage}); } catch { /* Chrome internal pages cannot be inspected. */ }
  const videos = frames.flatMap(frame => (frame.result?.videos || []).map(video => ({...video, frameId: frame.frameId, title: frame.result.title, mediaTitle: frame.result.mediaTitle})));
  videos.sort((a, b) => Number(b.playing) - Number(a.playing) || b.area - a.area);
  const video = videos[0];
  const candidates = [...stateFor(tabId).candidates];
  for (const item of videos) for (const url of [item.src, ...item.sources]) {
    if (!url?.startsWith("http")) continue;
    const existing = candidates.find(c => c.url === url);
    if (existing) existing.fromVideo = true;
    else candidates.push({url, kind: mediaKind(url) || "mp4", fromVideo: true, frameId: item.frameId});
  }
  await enrich(candidates, stateFor(tabId));
  const score = candidate => rank(candidate) + metadataRank(candidate.info) + (video && candidate.frameId === video.frameId ? 30 : 0);
  const sources = candidates.filter(c => !c.kind.startsWith("subtitle") && c.info?.role !== "subtitle").sort((a, b) => score(b) - score(a) || b.seen - a.seen).map((c, i) => ({...c, ...describeStream(c)}));
  const state = stateFor(tabId);
  state.domSources = videos.flatMap(v => [v.src, ...v.sources]).filter(url => /^https?:/.test(url || "") && ['hls','mp4','mkv','webm'].includes(mediaKind(url) || "mp4"));
  state.domVideo = state.domSources.length > 0;
  state.updated = Date.now();
  updateAction(tabId, state.domVideo || hasCastableVideo(sources));
  persist(tabId);
  const importTitle = frames.find(f => f.frameId === 0)?.result?.title || video?.title || "";
  if (state.importTitle && state.importTitle !== importTitle) state.imported = [];
  state.importTitle = importTitle;
  const subtitles = [...(state.imported || []), ...(video?.tracks || [])];
  for (const item of candidates.filter(c => c.kind === "subtitle")) if (!subtitles.some(t => t.url === item.url)) subtitles.push({url: item.url, label: labelFor(item.url, subtitles.length), language: /(?:^|[\/_.-])(fr|fra|fre)(?:[\/_.-]|$)/i.test(item.url) ? "fr" : "und"});
  const artwork = [...new Set([...(frames.find(f => f.frameId === 0)?.result?.artwork || []), ...(frames.find(f => f.frameId === video?.frameId)?.result?.artwork || [])])].slice(0,3);
  return {video, videos, sources, subtitles, artwork, recommendedSubtitle: state.imported?.length ? 0 : null, title: video?.mediaTitle || frames.find(f => f.frameId === 0)?.result?.mediaTitle || frames.find(f => f.frameId === 0)?.result?.title || video?.title || frames[0]?.result?.title || "Vidéo web"};
}

async function cast(tabId, url, subtitle, subtitleOffset = 0) {
  if (typeof subtitleOffset !== "number" || !Number.isFinite(subtitleOffset) || Math.abs(subtitleOffset) > 600) throw new Error("Décalage des sous-titres invalide.");
  const page = await inspect(tabId);
  const source = page.sources.find(item => item.url === url) || (!url ? page.sources[0] : null);
  if (!source) throw new Error("Aucun flux détecté. Recharge la page puis lance la vidéo avant de caster.");
  const video = page.videos.find(v => v.src === source.url || v.sources.includes(source.url)) || page.videos.find(v => v.frameId === source.frameId) || page.video;
  const selected = subtitleSet(page.subtitles, subtitle === "auto" && page.recommendedSubtitle != null ? page.recommendedSubtitle : subtitle);
  const track = selected.primary;
  if (track && !track.off) track.offset = subtitleOffset;
  const relevantOrigins = new Set([source.url, ...selected.tracks.map(t => t.url)].filter(Boolean).map(value => new URL(value).origin));
  const headersByOrigin = Object.fromEntries(Object.entries(stateFor(tabId).headers).filter(([origin]) => relevantOrigins.has(origin)));
  const media = {url:source.url, kind:source.kind, title:page.title, position:video?.position || 0, duration:video?.duration || 0, subtitle:track, subtitles:selected.tracks, headersByOrigin};
  if ((await native({action:"status"})).selectedProtocol !== "dlna") media.artwork = await fetchArtwork(page.artwork);
  if (new TextEncoder().encode(JSON.stringify(media)).length > 850000) delete media.artwork;
  if (new TextEncoder().encode(JSON.stringify(media)).length > 850000) throw new Error('Ces pistes de sous-titres sont trop volumineuses pour être transmises ensemble. Choisis une autre langue ou désactive les sous-titres externes.');
  const result = await native({action:"cast", media});
  chrome.action.setBadgeText({tabId, text: "TV"});
  chrome.action.setBadgeBackgroundColor({tabId, color: "#1673ef"});
  confirmAndPause(tabId, video).catch(() => {});
  return result;
}

async function confirmAndPause(tabId, video) {
  if (!video) return;
  for (let attempt = 0; attempt < 15; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    const state = await native({action: "status"});
    if (!state.casting) return;
    if (!state.confirmed) continue;
    await chrome.scripting.executeScript({target: {tabId, frameIds: [video.frameId]}, func: (index, originalSource) => {
      const current = document.querySelectorAll("video")[index];
      if (current && (current.currentSrc || current.src) === originalSource) current.pause();
    }, args: [video.index, video.src]});
    return;
  }
}

async function downloadVideo(tabId, url, subtitle) {
  const page = await inspect(tabId);
  const source = page.sources.find(item => item.url === url);
  if (!downloadable(source)) throw new Error("Choisis une source vidéo HLS, MP4, MKV ou WebM.");
  const video = page.videos.find(v => v.src === url || v.sources.includes(url)) || page.videos.find(v => v.frameId === source.frameId);
  if (video?.protected) throw new Error("Ce lecteur utilise une protection DRM.");
  const selection = subtitle === "auto" && page.recommendedSubtitle != null ? page.recommendedSubtitle : subtitle;
  const track = subtitleSet(page.subtitles, selection).primary;
  const selected = track || (subtitle === "auto" && page.subtitles.length ? subtitleSet(page.subtitles, 0).primary : null);
  const state = stateFor(tabId);
  const children = state.manifests?.[source.url]?.value?.children || [];
  const origins = new Set([source.url, selected?.url, ...children.map(child => child.url)].filter(Boolean).map(value => new URL(value).origin));
  const headersByOrigin = Object.fromEntries(Object.entries(state.headers).filter(([origin]) => origins.has(origin)));
  const media = {url:source.url, kind:source.kind, title:page.title, headersByOrigin, ...(selected && !selected.off ? {subtitle:selected} : {})};
  return native({action:"video_download_start", media});
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.tab || !sender.url?.startsWith(chrome.runtime.getURL(""))) return;
  (async () => {
    if (message.action === "inspect") return inspect(message.tabId);
    if (["os_download","sd_download"].includes(message.action)) {
      const before = stateFor(message.tabId);
      const title = (await inspect(message.tabId)).title;
      const result = await native({action:message.action, fileId:message.fileId});
      const current = stateFor(message.tabId);
      if (current !== before || (await inspect(message.tabId)).title !== title) throw new Error("La page a changé pendant le téléchargement. Relance la recherche.");
      current.imported = [result.track, ...(current.imported || []).filter(t => (t.provider !== result.track.provider || t.fileId !== result.track.fileId))].slice(0,3);
      persist(message.tabId);
      return {...result, page:await inspect(message.tabId)};
    }
    if (message.action === "cast") return cast(message.tabId, message.url, message.subtitle, message.subtitleOffset);
    if (message.action === "video_download_start") return downloadVideo(message.tabId, message.url, message.subtitle ?? "off");
    if (message.action === "video_download_cancel") return native({action:"video_download_cancel"});
    if (["sd_configure", "sd_search", "sd_apply", "os_configure", "os_search", "os_apply", "status", "scan", "select", "pair_begin", "pair_finish", "pair_cancel", "pair_forget", "toggle", "stop", "seek", "subtitle_sync", "subtitle_auto_start", "subtitle_auto_cancel"].includes(message.action)) {
      if (message.action === 'subtitle_auto_start' && (typeof message.session !== 'string' || !Number.isInteger(message.index) || message.index < 0)) throw new Error('Piste de sous-titres invalide.');
      if (message.action === 'subtitle_sync' && (typeof message.offset !== 'number' || !Number.isFinite(message.offset) || Math.abs(message.offset) > 600 || !Number.isInteger(message.index) || typeof message.session !== 'string')) throw new Error('Réglage de sous-titres invalide.');
      if (message.action === 'seek' && (typeof message.position !== 'number' || !Number.isFinite(message.position) || message.position < 0)) throw new Error('Position de lecture invalide.');
      const result = await native(message);
      if (message.action === "stop" && message.tabId) chrome.action.setBadgeText({tabId: message.tabId, text: ""});
      return result;
    }
    throw new Error("Commande inconnue.");
  })().then(result => respond({ok: true, result}), error => respond({ok: false, error: error.message}));
  return true;
});
chrome.commands.onCommand.addListener(async command => {
  if (command !== "cast-current") return;
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id) return;
  try { await cast(tab.id); } catch {
    chrome.action.setBadgeText({tabId: tab.id, text: "!"});
    chrome.action.setBadgeBackgroundColor({tabId: tab.id, color: "#b42318"});
  }
});
