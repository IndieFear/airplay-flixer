import {formatTime, progress} from "./player.js";
import {groupSubtitles} from "./subtitles.js";
import {downloadable} from "./detection.js";
const $ = id => document.getElementById(id);
let osBusy = false, osQueryDirty = false, osSearchParams = null, osPage = 1, osChosen = null;
let downloadBusy = false, downloadError = "";
let tabId, page, state, busy = false, refreshing = false, previewing = false, configured = false, sourceChanged = false, deviceDirty = false, subtitleGroups = [], forgetId = null, syncSession = null, syncDirty = false;
function send(message) {
  return chrome.runtime.sendMessage(message).then(response => {
    if (!response?.ok) throw new Error(response?.error || "L’extension n’a pas répondu.");
    return response.result;
  });
}
function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
function availability() {
  const active = !!state?.casting, duration = state?.duration || 0;
  $("cast").disabled = busy || !state || !state.selected || !page?.sources.length;
  $("save-device").hidden = !deviceDirty && !!state?.selected;
  $("save-device").disabled = busy || !state || !$("device").value || $("device").value === state?.selected;
  $("timeline").disabled = busy || !active || duration <= 0 || state.seekable === false;
  for (const id of ["toggle", "stop", "backward", "forward"]) $(id).disabled = busy || !active;
  $("backward").disabled ||= duration <= 0;
  $("forward").disabled ||= duration <= 0 || state?.seekable === false;
  $("backward").disabled ||= state?.seekable === false;
  $("toggle").disabled ||= state?.pauseable === false && !state?.paused;
  $("source").disabled = busy || !page?.sources.length;
  $("subtitle").disabled = busy || state?.selectedProtocol === "dlna";
  $("subtitle-language").disabled = busy || state?.selectedProtocol === "dlna";
  $("pairing-card").hidden = state?.selectedProtocol === "dlna" || deviceDirty || !state?.selected;
  $("pair").hidden = state?.selectedProtocol === "dlna";
  $("pair").disabled = busy || !state?.selected || deviceDirty;
  $("forget").disabled = busy || !state?.selected || deviceDirty;
  $("confirm-forget").disabled = busy || forgetId !== state?.selected;
  $("cancel-forget").disabled = busy;
  if (state?.selectedProtocol === "dlna") $("pair-form").hidden = true;
  renderSync();
  renderOpenSubtitles();
  renderSubtitleSummary();
  renderDownload();
  $("subtitle-hint").textContent = state?.selectedProtocol === "dlna" ? "DLNA : utilise les pistes intégrées depuis le lecteur de la TV. Les pistes externes ne sont pas transmises." : subtitleHint();
}
function renderDownload() {
  const job = state?.videoDownload;
  const running = job?.state === "running";
  const source = page?.sources.find(item => item.url === $("source").value);
  $("download-video").hidden = !source && !running;
  $("download-video").disabled = downloadBusy || running || !job || !downloadable(source);
  $("download-cancel").hidden = !running;
  $("download-cancel").disabled = downloadBusy;
  const mb = bytes => `${(bytes / 1e6).toLocaleString("fr-FR", {maximumFractionDigits:1})} Mo`;
  let message = downloadError;
  if (!message && running) message = `${job.title} · ${mb(job.bytes)}${job.total ? ` / ${mb(job.total)}` : ""}`;
  if (!message && job?.state === "done") message = [`Dans Téléchargements · ${job.filename}`, job.subtitleFilename, job.subtitleWarning].filter(Boolean).join(" · ");
  if (!message && job?.state === "error") message = job.error;
  if (!message && job?.state === "cancelled") message = "Téléchargement annulé.";
  if (!message && source && !job) message = state ? "Mets à jour le compagnon pour télécharger." : "Ouvre le compagnon sur ton Mac pour télécharger.";
  if (!message && source && !downloadable(source)) message = "Choisis un flux vidéo HLS, MP4, MKV ou WebM.";
  if (!message && source && page.subtitles.length && $("subtitle").value !== "off") message = state?.videoDownloadSubtitles ? "La piste choisie dans Sous-titres est enregistrée à côté de la vidéo." : "Mets à jour le compagnon pour enregistrer la piste choisie.";
  $("download-status").textContent = message;
  $("download-status").hidden = !message;
}

async function downloadAction(message) {
  if (downloadBusy) return;
  downloadBusy = true;
  downloadError = "";
  renderDownload();
  try {
    const result = await send(message);
    if (state) state.videoDownload = result;
  } catch (err) {
    downloadError = err.message;
  } finally {
    downloadBusy = false;
    renderDownload();
  }
}
function renderSync() {
  const active = !!state?.casting, tracks = state?.subtitleTracks || [];
  const available = state?.selectedProtocol !== "dlna" && (active ? tracks.length > 0 : !!page?.subtitles.length && $("subtitle-language").value !== "off");
  const options = $("panel-subtitles").querySelector(".subtitle-options");
  if (active && options.previousElementSibling !== $("subtitle-sync")) options.before($("subtitle-sync"));
  if (!active && options.nextElementSibling !== $("subtitle-sync")) options.after($("subtitle-sync"));
  $("subtitle-sync").hidden = !available;
  $("sync-unavailable").hidden = available;
  if (!available) return;
  $("sync-context").textContent = active ? "Pendant la lecture" : "Au prochain lancement";
  const session = active ? state.subtitleSession : "next";
  const fresh = session !== syncSession;
  if (fresh) { syncSession = session; syncDirty = false; $("sync-offset").value = 0; }
  $("sync-track-field").hidden = !active;
  if (active) {
    const previous = $("sync-track").value;
    const fingerprint = JSON.stringify(tracks.map(t => [t.index,t.label]));
    if ($("sync-track").dataset.choices !== fingerprint) {
      $("sync-track").replaceChildren(...tracks.map(t => new Option(t.label,String(t.index))));
      $("sync-track").dataset.choices = fingerprint;
      if (tracks.some(t => String(t.index) === previous)) $("sync-track").value = previous;
    }
    if (fresh) $("sync-track").value = String((tracks.find(t => t.default) || tracks[0]).index);
    if (!syncDirty) $("sync-offset").value = tracks.find(t => String(t.index) === $("sync-track").value)?.offset || 0;
  }
  const offset = Number($("sync-offset").value);
  const valid = $("sync-offset").value !== "" && Number.isFinite(offset) && Math.abs(offset) <= 600;
  const selected = tracks.find(t => String(t.index) === $("sync-track").value);
  const auto = state?.autoSync;
  const analysing = auto?.state === "running";
  const sameTarget = auto?.session === state?.subtitleSession && auto?.index === Number($("sync-track").value);
  $("sync-auto").hidden = false;
  $("sync-auto").disabled = busy || analysing || !active;
  $("sync-auto").textContent = analysing ? "Analyse en cours…" : "Synchroniser automatiquement";
  $("auto-progress").dataset.state = auto?.state || "idle";
  $("auto-progress").setAttribute("aria-busy", String(analysing));
  $("auto-progress").hidden = !active || !(sameTarget || analysing) || auto?.state === "idle";
  const analysed = auto?.analysedSeconds ? ` Analyse : ${auto.analysedSeconds >= 60 ? Math.round(auto.analysedSeconds / 60) + " min" : Math.round(auto.analysedSeconds) + " s"} d’audio.` : "";
  $("auto-message").textContent = sameTarget && auto?.state === "proposed" ? `Décalage proposé : ${auto.offset > 0 ? "+" : ""}${auto.offset.toLocaleString("fr-FR")} s. ${auto.message}${analysed}` : analysing ? `Analyse de ${tracks.find(t => t.index === auto.index)?.label || "la piste"} : ${auto.message}` : sameTarget ? (auto?.message || "") + analysed : "";
  $("auto-cancel").hidden = !analysing;
  $("auto-use").hidden = !(sameTarget && auto?.state === "proposed");
  const appliedAuto = !!selected?.default && selected.offset === auto?.offset;
  const stagedAuto = syncDirty && offset === auto?.offset;
  $("auto-use").disabled = busy || stagedAuto || appliedAuto;
  $("auto-use").textContent = appliedAuto ? "Décalage déjà appliqué" : stagedAuto ? "Décalage prêt à appliquer" : "Utiliser ce décalage";
  $("auto-cancel").disabled = busy;
  $("sync-apply").hidden = !active || !syncDirty && !!selected?.default;
  $("sync-apply").disabled = busy || !valid || selected?.default && selected.offset === offset;
  for (const id of ["sync-track","sync-offset","sync-reset","sync-earlier","sync-later"]) $(id).disabled = busy;
  $("offset-summary").textContent = valid ? `${offset > 0 ? "+" : ""}${offset.toLocaleString("fr-FR")} s` : "À corriger";
  $("sync-meaning").textContent = !valid ? "Entre un décalage entre −600 et +600 secondes." : offset === 0 ? "Aucun décalage." : `Les sous-titres apparaîtront ${Math.abs(offset).toLocaleString("fr-FR")} s plus ${offset < 0 ? "tôt" : "tard"}.`;
  $("sync-hint").textContent = active ? syncDirty || selected && !selected.default ? "Appliquer recharge la vidéo à la position actuelle, avec une courte coupure." : "L’analyse propose un décalage. Tu choisis ensuite de l’appliquer." : "Ce décalage sera appliqué à la piste choisie au démarrage. La synchronisation automatique est disponible pendant la diffusion.";
}
$("sync-auto").addEventListener("click", () => action(async () => {
  renderState(await send({action:"subtitle_auto_start", session:state.subtitleSession,index:Number($("sync-track").value)}));
}));
$("auto-cancel").addEventListener("click", () => action(async () => renderState(await send({action:"subtitle_auto_cancel"}))));
$("auto-use").addEventListener("click", () => {
  const auto = state?.autoSync;
  if (auto?.state !== "proposed" || auto.session !== state.subtitleSession || auto.index !== Number($("sync-track").value)) return;
  $("sync-offset").value = auto.offset;
  syncDirty = true; renderSync();
});
$("sync-track").addEventListener("change", () => { syncDirty = false; renderSync(); });
$("sync-offset").addEventListener("input", () => { syncDirty = true; renderSync(); });
function changeOffset(delta, reset=false) {
  const value = reset ? 0 : (Number($("sync-offset").value) || 0) + delta;
  $("sync-offset").value = Math.max(-600, Math.min(600, Math.round(value*1000)/1000));
  syncDirty = true; renderSync();
}
$("sync-earlier").addEventListener("click", () => changeOffset(-.5));
$("sync-later").addEventListener("click", () => changeOffset(.5));
$("sync-reset").addEventListener("click", () => changeOffset(0,true));
$("sync-apply").addEventListener("click", () => action(async () => {
  const result = await send({action:"subtitle_sync", session:state.subtitleSession, index:Number($("sync-track").value), offset:Number($("sync-offset").value)});
  syncDirty = false; renderState(result);
}));
function paintTimeline(position, duration) {
  $("timeline").style.setProperty("--progress", `${progress(position, duration)}%`);
  $("position").textContent = formatTime(position);
  $("duration").textContent = formatTime(duration);
  $("timeline").setAttribute("aria-valuetext", `${formatTime(position)} sur ${formatTime(duration)}`);
}
function renderPlayer() {
  const active = !!state?.casting;
  document.body.dataset.casting = String(active);
  $("controls").hidden = !active;
  $("stop").hidden = !active;
  const stream = state?.stream;
  const rate = value => `${(value / 1000000).toFixed(2)} Mb/s`;
  const resolution = stream?.height ? `${stream.width} × ${stream.height}` : "Résolution indisponible";
  const stats = [resolution];
  if (stream?.segmentBitrate != null) stats.push(`Vidéo ≈ ${rate(stream.segmentBitrate)}`);
  else if (stream?.declaredBitrate) stats.push(`Annoncé : ${rate(stream.declaredBitrate)}`);
  if (stream?.transferBitrate != null) stats.push(`Transfert : ${rate(stream.transferBitrate)}`);
  if (stream?.sampleAge > 15) stats.push("Dernier envoi");
  $("stream-stats").textContent = stream ? stats.join(" · ") : "Mesure du flux en attente…";
  $("stream-stats").title = "Résolution de la dernière variante vidéo envoyée. Débit vidéo estimé sur les segments complets (audio inclus si multiplexé). Transfert mesuré sur 10 s : varie avec la mise en mémoire tampon, et peut tomber à zéro. La variante affichée à l’écran peut différer pendant le buffering.";
  $("title").textContent = (active ? state.title : page?.sources.length ? page.title : null) || "Une vidéo, un grand écran.";
  $("player-label").textContent = active ? (state.paused ? "EN PAUSE" : "EN LECTURE") : page?.sources.length ? "VIDÉO DÉTECTÉE" : "EN ATTENTE D’UNE VIDÉO";
  $("source-caption").hidden = active;
  $("source-caption").textContent = active ? `Sur ${state.selectedName || "l’Apple TV"}` : "Depuis l’onglet ouvert";
  $("empty").hidden = active || !!page?.sources.length;
  $("cast").hidden = active && (!page?.sources.length || page.title === state.title && !sourceChanged);
  $("cast").querySelector("span").textContent = busy ? "Un instant…" : active ? "Diffuser cette source" : `Diffuser sur ${state?.selectedName || "l’appareil"}`;
  $("cast-hint").textContent = !state ? "Ouvre le compagnon sur ton Mac pour diffuser." : !state.selected ? "Choisis d’abord ta destination dans Paramètres." : !page?.sources.length ? "Lance la vidéo sur la page, puis actualise ici." : active && sourceChanged ? "La nouvelle source remplacera la diffusion en cours." : "";
  $("player-label").dataset.active = String(active);
  $("pause-glyph").toggleAttribute('hidden', !!state?.paused);
  $("play-glyph").toggleAttribute('hidden', !state?.paused);
  $("toggle").setAttribute("aria-label", state?.paused ? "Reprendre la lecture" : "Mettre en pause");
  $("toggle").title = state?.paused ? "Reprendre la lecture" : "Mettre en pause";
  $("playing-status").parentElement.hidden = !busy && !["seeking", "loading"].includes(state?.playbackState);
  $("playing-status").textContent = busy ? "Un instant…" : state?.playbackState === "seeking" || state?.playbackState === "loading" ? "Chargement…" : state?.paused ? "En pause" : `Lecture sur ${state?.selectedName || "l’appareil"}`;
  if (!previewing) {
    $("timeline").max = Math.max(state?.duration || 0, 1);
    $("timeline").value = state?.position || 0;
    paintTimeline(state?.position || 0, state?.duration || 0);
  }
  availability();
}
async function action(task) {
  if (busy) return;
  busy = true; error(); renderPlayer();
  for (const id of ["scan", "pair", "device", "save-device"]) $(id).disabled = true;
  try { await task(); } catch (err) { error(err.message); }
  finally {
    busy = false;
    for (const id of ["scan", "pair", "device", "save-device"]) $(id).disabled = false;
    renderPlayer();
  }
}
function renderState(value) {
  state = value;
  $("connection-label").textContent = value.casting ? "Connecté à l’appareil" : "Compagnon prêt";
  $("connection-label").title = value.message || "";
  $("connection").className = value.casting ? "active" : "ready";
  $("room").textContent = value.selectedName || "Choisir un appareil";
  $("destination").setAttribute("aria-label", `Destination : ${value.selectedName || "aucune"}. Ouvrir les paramètres`);
  const devices = value.devices || [];
  const previousDevice = $("device").value;
  const choices = [...devices].sort((a,b) => Number(a.protocol === "dlna") - Number(b.protocol === "dlna")).map(d => ({label: `${d.name} · ${d.protocol === "dlna" ? "DLNA" : "AirPlay"}`, value: d.id, disabled: d.canCast === false}));
  if (value.selected && !devices.some(d => d.id === value.selected)) choices.unshift({label: value.selectedName || "Apple TV favorite", value: value.selected});
  if (!value.selected) choices.unshift({label: "Choisir un appareil", value: ""});
  const fingerprint = JSON.stringify(choices);
  if ($("device").dataset.choices !== fingerprint) {
    $("device").replaceChildren(...choices.map(d => { const option = new Option(d.label, d.value); option.disabled = !!d.disabled; return option; }));
    $("device").dataset.choices = fingerprint;
    if (deviceDirty && choices.some(d => d.value === previousDevice)) $("device").value = previousDevice;
    else deviceDirty = false;
  }
  if (!deviceDirty) $("device").value = value.selected || "";
  $("device-saved").textContent = deviceDirty ? "Non enregistré" : value.selected ? "Enregistré" : "À configurer";
  $("save-device").querySelector("span").textContent = value.casting && deviceDirty ? "Changer de destination" : "Enregistrer par défaut";
  $("device-hint").textContent = value.casting && deviceDirty ? "Changer de destination arrêtera la diffusion en cours." : value.selected ? "Enregistré pour tes prochaines diffusions, y compris le raccourci clavier." : "Choisis un appareil, puis enregistre-le pour tes prochaines diffusions.";
  const dlnaCount = devices.filter(d => d.protocol === "dlna").length;
  $("discovery-hint").textContent = [value.discoveryWarning, dlnaCount ? `${dlnaCount} appareil(s) DLNA disponible(s). Formats et contrôles selon le téléviseur.` : "La recherche inclut AirPlay et les récepteurs DLNA du réseau local."].filter(Boolean).join(" ");
  const selected = devices.find(d => d.id === value.selected);
  const paired = value.selectedPaired ?? selected?.paired;
  $("pair").textContent = paired ? "Appairer à nouveau…" : "Appairer cette Apple TV";
  $("pair-status").textContent = paired ? "Appairée" : value.selected ? "Non appairée" : "À sélectionner";
  $("pair-status").classList.toggle("paired", !!paired);
  $("pair-description").textContent = paired ? `Une association à ${value.selectedName || "cette Apple TV"} est enregistrée sur ce Mac.` : value.selected ? `Aucune association enregistrée sur ce Mac pour ${value.selectedName || "cette Apple TV"}. Lance l’appairage avec le code affiché sur la TV.` : "Enregistre une Apple TV par défaut pour consulter son appairage.";
  $("forget").hidden = !paired || value.selectedProtocol === "dlna";
  if (forgetId && (forgetId !== value.selected || deviceDirty || !paired)) { forgetId = null; $("forget-confirm").hidden = true; }
  $("pair").disabled = busy || !value.selected || deviceDirty;
  if (!configured) { if (!value.selected) showTab("settings"); configured = true; }
  $("playback-warning").textContent = value.playbackWarning || "";
  $("playback-warning").hidden = !value.playbackWarning;
  if (value.error) error(value.error);
  renderPlayer();
}
function renderPage(value) {
  const previousSource = $("source").value, previousSubtitle = $("subtitle").value;
  const previousTrack = /^\d+$/.test(previousSubtitle) ? page?.subtitles[Number(previousSubtitle)] : null;
  const previousLanguage = $("subtitle-language").value;
  page = value;
  $("source").replaceChildren(...(value.sources.length ? value.sources.map(s => new Option(s.label, s.url)) : [new Option("Aucun flux détecté", "")]));
  $("source").disabled = !value.sources.length;
  $("source-count").textContent = `${value.sources.length} ${value.sources.length > 1 ? "sources" : "source"}`;
  if (value.sources.some(s => s.url === previousSource)) $("source").value = previousSource;
  subtitleGroups = groupSubtitles(value.subtitles);
  $("subtitle-language").replaceChildren(new Option("Automatique · français ou piste active", "auto"), new Option("Désactivés", "off"), ...subtitleGroups.map(g => new Option(`${g.label.charAt(0).toUpperCase() + g.label.slice(1)} · ${g.tracks.length}`,g.key)));
  if ([...$("subtitle-language").options].some(o => o.value === previousLanguage)) $("subtitle-language").value = previousLanguage;
  const sameTrack = previousTrack ? value.subtitles.findIndex(t => previousTrack.url ? t.url === previousTrack.url : t.content === previousTrack.content && t.label === previousTrack.label) : -1;
  if (sameTrack < 0 && previousLanguage === "auto" && value.recommendedSubtitle != null) {
    const recommended = subtitleGroups.find(g => g.tracks.some(t => t.index === value.recommendedSubtitle));
    if (recommended) $("subtitle-language").value = recommended.key;
  }
  renderSubtitleTracks(sameTrack >= 0 ? String(sameTrack) : value.recommendedSubtitle != null ? String(value.recommendedSubtitle) : null);
  renderSourceHint();
  seedOpenSubtitles(value.title);
  renderPlayer();
}
function subtitleHint() {
  if ($("subtitle-language").value === "off") return "Les sous-titres seront désactivés au prochain lancement.";
  if (!subtitleGroups.length) return "Aucune piste externe détectée. Les pistes intégrées restent disponibles sur l’Apple TV.";
  const group = subtitleGroups.find(g => g.key === $("subtitle-language").value);
  return group ? `Les ${group.tracks.length} pistes seront disponibles avec la télécommande. La piste choisie s’activera au prochain lancement.` : "La langue suit la piste active de la page, sinon le français. Toutes ses pistes seront disponibles sur l’Apple TV.";
}
function renderSubtitleTracks(previous = null) {
  const language = $("subtitle-language").value;
  const group = subtitleGroups.find(g => g.key === language);
  $("subtitle-track-field").hidden = !group;
  if (group) {
    $("subtitle").replaceChildren(...group.tracks.map(t => { const o = new Option(t.label, String(t.index)); o.title = t.fullLabel; return o; }));
    const selected = group.tracks.find(t => String(t.index) === previous) || group.tracks.find(t => t.selected) || group.tracks[0];
    $("subtitle").value = String(selected.index);
  } else $("subtitle").replaceChildren(new Option(language === "off" ? "Désactivés" : "Automatique", language === "off" ? "off" : "auto"));
}
$("subtitle-language").addEventListener("change", () => { renderSubtitleTracks(); if (!state?.casting) syncSession = null; availability(); });
function showTab(name) {
  document.scrollingElement.scrollTop = 0;
  for (const tab of ["player", "subtitles", "settings"]) {
    const selected = tab === name;
    $("tab-" + tab).setAttribute("aria-selected", String(selected));
    $("tab-" + tab).tabIndex = selected ? 0 : -1;
    $("panel-" + tab).hidden = !selected;
  }
}
for (const name of ["player", "subtitles", "settings"]) {
  $("tab-" + name).addEventListener("click", () => showTab(name));
  $("tab-" + name).addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = ["player", "subtitles", "settings"];
    const next = event.key === "Home" ? "player" : event.key === "End" ? "settings" : tabs[(tabs.indexOf(name) + (event.key === "ArrowRight" ? 1 : 2)) % tabs.length];
    showTab(next); $("tab-" + next).focus();
  });
}
$("destination").addEventListener("click", () => { showTab("settings"); $("device").focus(); });
$("subtitle-shortcut").addEventListener("click", () => showTab("subtitles"));
function renderSubtitleSummary() {
  const language = $("subtitle-language").value;
  const group = subtitleGroups.find(g => g.key === language);
  const dlna = state?.selectedProtocol === "dlna";
  $("subtitle-summary").textContent = dlna ? "Depuis la TV" : language === "off" ? "Désactivés" : group ? `${group.label.charAt(0).toUpperCase() + group.label.slice(1)} · ${group.tracks.length} piste${group.tracks.length > 1 ? "s" : ""}` : "Automatique";
  const count = state?.casting ? state.subtitleTracks?.length || 0 : page?.subtitles.length || 0;
  $("subtitle-count").textContent = count;
  $("subtitle-count").hidden = count === 0;
}
function renderSourceHint() {
  const details = page?.sources.find(s => s.url === $("source").value)?.details || "";
  $("source-hint").textContent = details;
  $("source-hint").closest("details").hidden = !details;
}
$("source").addEventListener("change", () => { sourceChanged = true; renderSourceHint(); renderPlayer(); });
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const results = await Promise.allSettled([send({action: "inspect", tabId}), send({action: "status"})]);
    if (results[0].status === "fulfilled") renderPage(results[0].value);
    if (results[1].status === "fulfilled") renderState(results[1].value);
    else { state = null; $("connection-label").textContent = "Compagnon déconnecté"; $("connection").className = ""; error(results[1].reason.message); renderPlayer(); }
  } finally { refreshing = false; }
}
async function seek(position) {
  if (!state?.casting || !Number.isFinite(position) || !(state.duration > 0)) return;
  const target = Math.max(0, Math.min(position, state.duration - 0.5));
  const previous = state;
  state = {...state, position: target, playbackState: 'seeking'};
  previewing = false;
  $("seek-preview").textContent = "";
  await action(async () => {
    try { renderState(await send({action: "seek", position: target})); }
    catch (err) { state = previous; throw err; }
  });
}
$("timeline").addEventListener("input", () => {
  previewing = true;
  const value = Number($("timeline").value);
  paintTimeline(value, state?.duration || 0);
  $("seek-preview").textContent = `Aller à ${formatTime(value)}`;
});
$("timeline").addEventListener("change", () => seek(Number($("timeline").value)));
$("timeline").addEventListener("pointercancel", () => { previewing = false; $("seek-preview").textContent = ""; renderPlayer(); });
$("backward").addEventListener("click", () => seek((state?.position || 0) - 10));
$("forward").addEventListener("click", () => seek((state?.position || 0) + 10));
$("refresh").addEventListener("click", () => action(refresh));
$("scan").addEventListener("click", () => action(async () => {
  $("scan").querySelector("span").textContent = "Recherche sur le réseau…";
  try { renderState(await send({action: "scan"})); }
  finally { $("scan").querySelector("span").textContent = "Rechercher des appareils"; }
}));
$("device").addEventListener("change", () => { deviceDirty = $("device").value !== state?.selected; if (state) renderState(state); else availability(); });
$("save-device").addEventListener("click", () => action(async () => {
  const value = await send({action: "select", id: $("device").value});
  deviceDirty = false; renderState(value);
}));
$("forget").addEventListener("click", () => {
  forgetId = state.selected;
  $("forget-description").textContent = `Effacer l’association à ${state.selectedName || "cette Apple TV"} sur ce Mac ?${state.casting ? " La diffusion en cours sera arrêtée." : ""} Tu pourras ensuite refaire l’appairage.`;
  $("forget-confirm").hidden = false;
  availability();
});
$("cancel-forget").addEventListener("click", () => { forgetId = null; $("forget-confirm").hidden = true; });
$("confirm-forget").addEventListener("click", () => action(async () => {
  const result = await send({action: "pair_forget", id: forgetId});
  forgetId = null; $("forget-confirm").hidden = true; $("pair-form").hidden = true; $("pin").value = "";
  renderState(result);
}));
$("pair").addEventListener("click", () => action(async () => {
  const result = await send({action: "pair_begin"});
  $("pair-form").hidden = !result.pinRequired;
  if (result.pinRequired) $("pin").focus(); else await refresh();
}));
$("pair-form").addEventListener("submit", event => {
  event.preventDefault();
  action(async () => { await send({action: "pair_finish", pin: $("pin").value}); $("pin").value = ""; $("pair-form").hidden = true; await refresh(); });
});
$("cancel-pair").addEventListener("click", () => action(async () => { await send({action: "pair_cancel"}); $("pair-form").hidden = true; $("pin").value = ""; }));
$("cast").addEventListener("click", () => action(async () => {
  $("cast").querySelector("span").textContent = "Préparation…";
  const selection = $("subtitle").value;
  const result = await send({action: "cast", tabId, url: $("source").value, subtitle: /^\d+$/.test(selection) ? Number(selection) : selection, subtitleOffset: state?.casting || state?.selectedProtocol === "dlna" ? 0 : Number($("sync-offset").value) || 0});
  sourceChanged = false;
  renderState(result);
}));
$("download-video").addEventListener("click", () => {
  const selection = $("subtitle").value;
  if (page?.subtitles.length && selection !== "off" && !state?.videoDownloadSubtitles) {
    downloadError = "Mets à jour le compagnon pour enregistrer les sous-titres.";
    renderDownload();
    return;
  }
  downloadAction({action:"video_download_start", tabId, url:$("source").value, subtitle:/^\d+$/.test(selection) ? Number(selection) : selection});
});
$("download-cancel").addEventListener("click", () => downloadAction({action:"video_download_cancel"}));
$("toggle").addEventListener("click", () => action(async () => renderState(await send({action: "toggle", tabId}))));
$("stop").addEventListener("click", () => action(async () => {
  renderState(await send({action: "stop", tabId}));
  previewing = false;
  $("seek-preview").textContent = "";
  sourceChanged = false;
  showTab("player");
  await refresh();
}));
const [active] = await chrome.tabs.query({active: true, currentWindow: true});
tabId = active?.id;
await refresh();
setInterval(() => { if (!busy) send({action: "status"}).then(value => { if (!busy) renderState(value); }).catch(() => {}); }, 1000);


function seedOpenSubtitles(title) {
  if (osQueryDirty || !title) return;
  const episode = title.match(/\bS(\d{1,3})[ ._-]*E(\d{1,3})\b/i) || title.match(/\b(\d{1,3})x(\d{1,3})\b/i);
  $("os-query").value = episode ? title.slice(0,episode.index).trim() || title : title;
  if (episode) { $("os-type").value="episode"; $("os-season").value=Number(episode[1]); $("os-episode").value=Number(episode[2]); }
  $("os-episode-fields").hidden = $("os-type").value !== "episode";
}
function renderOpenSubtitles() {
  const provider = state?.opensubtitles;
  const useSubdl = $("subtitle-provider").value === "subdl";
  const configured = !!(useSubdl ? state?.subdl : provider)?.configured;
  const supported = useSubdl ? !!state?.subdl : !!provider;
  const name = useSubdl ? "SubDL" : "OpenSubtitles";
  $("provider-name").textContent = name;
  $("os-results").setAttribute("aria-label", `Résultats ${name}`);
  $("subtitle-provider").disabled = osBusy || busy;
  $("os-setup").textContent = supported ? `Configurer ${name}` : `Mettre à jour le compagnon pour ${name}`;
  $("os-setup").disabled = !supported;
  $("sd-status").textContent = state?.subdl?.configured ? "Clé enregistrée" : state && !state.subdl ? "Mise à jour nécessaire" : "Non configuré";
  $("sd-key").placeholder = state?.subdl?.configured ? "Clé enregistrée · laisse vide pour la conserver" : "Colle ta clé API SubDL";
  $("sd-clear").hidden = !state?.subdl?.configured;
  $("sd-save").disabled = osBusy || busy || !state?.subdl;
  $("sd-clear").disabled = osBusy || busy || !state?.subdl;
  $("os-status").textContent = provider?.connected ? `Connecté · ${provider.username}` : provider?.configured ? "Clé enregistrée" : "Non configuré";
  $("os-key").placeholder = provider?.configured ? "Clé enregistrée · laisse vide pour la conserver" : "Colle ta clé API";
  $("os-setup").hidden = configured;
  $("os-search-form").hidden = !configured;
  $("os-clear").hidden = !provider?.configured;
  for (const id of ["os-search","os-more","os-save","os-clear"]) $(id).disabled = osBusy || busy || !state;
  for (const button of $("os-results").querySelectorAll("button")) button.disabled = osBusy || busy;
  $("os-search").textContent = osBusy ? "Un instant…" : `Rechercher sur ${name}`;
  $("os-chosen").hidden = !osChosen;
  $("os-apply").hidden = !osChosen || !state?.casting || !state?.subtitleImportable;
  $("os-apply").disabled = osBusy || busy || osChosen?.session !== state?.subtitleSession;
  if (osChosen) $("os-chosen-hint").textContent = state?.casting ? state.subtitleImportable ? "Ajouter reprend la vidéo à sa position actuelle. Tu pourras ensuite lancer l’auto-sync." : "Piste sélectionnée pour le prochain lancement. Cette source doit être recastée pour ajouter des sous-titres." : "Piste sélectionnée pour la prochaine diffusion.";
}
async function osAction(task) {
  if (osBusy || busy) return;
  osBusy=true; renderOpenSubtitles();
  try { await task(); }
  catch(err) { $("os-message").textContent=err.message; }
  finally { osBusy=false; renderOpenSubtitles(); }
}
$("os-query").addEventListener("input",()=>{osQueryDirty=true;});
$("os-type").addEventListener("change",()=>{$("os-episode-fields").hidden=$("os-type").value!=="episode";});
$("os-setup").addEventListener("click",()=>{showTab("settings"); ($("subtitle-provider").value === "subdl" ? $("sd-key") : $("os-key")).focus();});
$("os-config-form").addEventListener("submit",event=>{
  event.preventDefault();
  osAction(async()=>{
    $("os-config-message").textContent="Enregistrement…";
    try {
      const result=await send({action:"os_configure",key:$("os-key").value.trim(),username:$("os-username").value.trim(),password:$("os-password").value});
      state.opensubtitles=result;
      $("os-key").value="";$("os-username").value="";
      $("os-config-message").textContent=result.connected ? "Compte connecté. La recherche est disponible dans Sous-titres." : "Clé enregistrée. La recherche est disponible dans Sous-titres.";
    } catch(err) { $("os-config-message").textContent=err.message; }
    finally { $("os-password").value=""; }
  });
});
$("os-clear").addEventListener("click",()=>osAction(async()=>{
  state.opensubtitles=await send({action:"os_configure",clear:true});
  $("os-key").value="";$("os-password").value="";$("os-username").value="";
  $("os-results").replaceChildren();$("os-more").hidden=true;
  $("os-config-message").textContent="Clé et connexion retirées de ce Mac.";
}));
function renderOsResults(result) {
  const provider = result.provider || $("subtitle-provider").value;
  $("os-results").replaceChildren();
  $("os-message").textContent=result.results.length ? `${result.results.length} piste${result.results.length > 1 ? "s" : ""} · page ${result.page}/${result.totalPages}. Vérifie le titre et la version avant de télécharger.` : "Aucune piste trouvée. Essaie le titre original ou l’identifiant IMDb.";
  for (const track of result.results) {
    const card=document.createElement("article");card.className="os-result";
    const title=document.createElement("strong");title.textContent=track.title;
    const release=document.createElement("p");release.textContent=track.release || track.filename || "Version non précisée";
    const info=document.createElement("p");info.className="hint";
    info.textContent=[track.fps ? `${track.fps} images/s` : "",Number(track.downloads)>0 ? `${Number(track.downloads).toLocaleString("fr-FR")} téléchargements` : "",track.hearingImpaired ? "Malentendants" : "",track.machineTranslated ? "Traduction automatique" : "",track.trusted ? "Contributeur vérifié" : ""].filter(Boolean).join(" · ");
    const button=document.createElement("button");button.type="button";button.className="small";button.textContent="Télécharger cette piste";
    button.addEventListener("click",()=>osAction(async()=>{
      $("os-message").textContent="Téléchargement de la piste…";
      const session=state?.subtitleSession;
      const result=await send({action:provider === "subdl" ? "sd_download" : "os_download",tabId,fileId:track.fileId});
      renderPage(result.page);
      $("subtitle-language").value=result.track.language;
      renderSubtitleTracks(String(result.page.subtitles.findIndex(t=>t.provider===provider && t.fileId===track.fileId)));
      osChosen={id:result.id,session,provider};
      $("os-chosen-label").textContent=`Prête : ${result.track.label}`;
      $("os-message").textContent="Piste téléchargée et sélectionnée."+(result.remaining!=null ? ` Quota restant annoncé : ${result.remaining}.` : "");
      availability();
      $("os-chosen").scrollIntoView({block:"nearest"});
    }));
    card.append(title,release,info,button);$("os-results").append(card);
  }
  osPage=result.page;$("os-more").hidden=result.page>=result.totalPages;
}
async function searchOs(nextPage=1) {
  if(nextPage===1)osSearchParams={provider:$("subtitle-provider").value,query:$("os-query").value,type:$("os-type").value,language:$("os-language").value,season_number:Number($("os-season").value),episode_number:Number($("os-episode").value)};
  $("os-message").textContent=`Recherche dans le catalogue ${osSearchParams.provider === "subdl" ? "SubDL" : "OpenSubtitles"}…`;
  renderOsResults(await send({...osSearchParams,action:osSearchParams.provider === "subdl" ? "sd_search" : "os_search",page:nextPage}));
}
$("os-search-form").addEventListener("submit",event=>{event.preventDefault();osAction(()=>searchOs());});
$("os-more").addEventListener("click",()=>osAction(()=>searchOs(osPage+1)));
$("os-apply").addEventListener("click",()=>osAction(async()=>{
  const result=await send({action:osChosen.provider === "subdl" ? "sd_apply" : "os_apply",session:osChosen.session,id:osChosen.id});
  const chosen=result.subtitleTracks?.find(t=>t.default);
  syncDirty=false;renderState(result);
  if(chosen)$("sync-track").value=String(chosen.index);
  osChosen=null;$("os-message").textContent="Piste ajoutée à la lecture. L’auto-sync est disponible au-dessus.";renderSync();
}));

$("subtitle-provider").addEventListener("change",()=>{
  $("os-results").replaceChildren();$("os-message").textContent="";$("os-more").hidden=true;osSearchParams=null;renderOpenSubtitles();
});
$("sd-config-form").addEventListener("submit",event=>{
  event.preventDefault();osAction(async()=>{
    try {
      state.subdl=await send({action:"sd_configure",key:$("sd-key").value.trim()});
      $("sd-key").value="";$("subtitle-provider").value="subdl";
      $("sd-config-message").textContent="Clé SubDL enregistrée. La recherche est disponible dans Sous-titres.";
    } catch(err) {$("sd-config-message").textContent=err.message;}
  });
});
$("sd-clear").addEventListener("click",()=>osAction(async()=>{
  try {
    state.subdl=await send({action:"sd_configure",clear:true});$("sd-key").value="";
    $("sd-config-message").textContent="Clé SubDL retirée de ce Mac.";
    if($("subtitle-provider").value==="subdl"){$("os-results").replaceChildren();$("os-more").hidden=true;}
  } catch(err) {$("sd-config-message").textContent=err.message;}
}));
