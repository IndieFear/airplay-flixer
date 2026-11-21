(() => {
  const host = document.createElement("div");
  host.className = "flixer-surface";
  chrome.storage.local.get("theme").then(({theme}) => { host.dataset.theme = theme || "system"; }).catch(() => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.theme) host.dataset.theme = changes.theme.newValue || "system";
  });
  host.style.cssText = "position:fixed;margin:0;padding:0;border:0;display:none;pointer-events:none;z-index:2147483647";
  const root = host.attachShadow({mode: "closed"});
  root.innerHTML = `
    <style>
      button {
        display: grid;
        place-items: center;
        width: 38px;
        height: 38px;
        padding: 0;
        border: 1px solid var(--line);
        border-radius: var(--radius-control);
        background: var(--surface);
        box-shadow: var(--shadow);
        color: var(--accent);
        cursor: pointer;
        opacity: 0;
        pointer-events: none;
        transition: opacity .15s;
      }
      :host([data-visible]) button, button:focus-visible { opacity: 1; pointer-events: auto; }
      button:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
      svg { width: 21px; height: 21px; }
      @media (prefers-reduced-motion: reduce) { button { transition: none; } }
    </style>
    <button type="button" aria-label="Open Airplay Flixer" title="Open Airplay Flixer">
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M5 17H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-1"/>
        <path d="m12 13 6 8H6z" fill="currentColor"/>
      </svg>
    </button>`;
  const button = root.querySelector("button");
  let video, pointer, pending = false;
  let videos = [];
  let hideTimer;

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(update);
  }

  const observer = new ResizeObserver(schedule);
  function update() {
    pending = false;
    const fullscreen = document.fullscreenElement;
    const hovered = pointer && videos.find(candidate => {
      if (!candidate.isConnected || (fullscreen && !fullscreen.contains(candidate))) return false;
      const bounds = candidate.getBoundingClientRect();
      return bounds.width >= 180 && bounds.height >= 100 &&
        pointer.x >= bounds.left && pointer.x <= bounds.right &&
        pointer.y >= bounds.top && pointer.y <= bounds.bottom;
    });
    const candidate = hovered || (video?.isConnected ? video : null) ||
      videos.find(item => item.isConnected && !item.paused);
    const bounds = candidate?.getBoundingClientRect();
    const visible = bounds && bounds.width >= 180 && bounds.height >= 100 &&
      bounds.left < innerWidth - 50 && bounds.right > 50 &&
      bounds.top < innerHeight - 50 && bounds.bottom > Math.max(50, bounds.top + 50) &&
      (!fullscreen || fullscreen !== candidate && fullscreen.contains(candidate));
    if (!visible) {
      host.style.display = "none";
      return;
    }
    if (video !== candidate) {
      observer.disconnect();
      observer.observe(candidate);
      video = candidate;
    }
    const parent = fullscreen || document.documentElement;
    if (host.parentNode !== parent) parent.append(host);
    host.style.display = "block";
    host.style.left = Math.min(innerWidth, bounds.right) - 48 + "px";
    host.style.top = Math.max(0, bounds.top) + 10 + "px";
    host.toggleAttribute("data-visible", !!hovered);
  }

  // The player can be replaced when a playlist advances or a site navigates in place.
  function findVideos() {
    videos = Array.from(document.querySelectorAll("video"));
    schedule();
  }
  function trackPointer(event) {
    if (!videos.length) return;
    pointer = {x: event.clientX, y: event.clientY};
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hidePointer, document.fullscreenElement ? 1200 : 2500);
    schedule();
  }
  document.addEventListener("pointerover", findVideos, true);
  document.addEventListener("pointermove", trackPointer, {passive: true, capture: true});
  function hidePointer() {
    clearTimeout(hideTimer);
    pointer = null;
    schedule();
  }
  document.addEventListener("pointerleave", hidePointer);
  document.addEventListener("play", findVideos, true);
  document.addEventListener("fullscreenchange", () => {
    hidePointer();
    findVideos();
  });
  window.addEventListener("resize", schedule);
  window.addEventListener("scroll", schedule, {passive: true, capture: true});
  button.addEventListener("click", async event => {
    if (!event.isTrusted) return;
    event.stopPropagation();
    try {
      const result = await chrome.runtime.sendMessage({action: "open-player-popup"});
      if (result?.ok) return;
    } catch { /* The tab may need to be refreshed after an extension update. */ }
    button.title = "Open Airplay Flixer from the Chrome toolbar";
    button.setAttribute("aria-label", button.title);
  });
  findVideos();
})();
