// Jarvis app shell: navigation, the live link to the running Jarvis, every page's controls, the command menu and toasts.
// Uses $ and el from app.js (settings, connections and the status cards live there).
(() => {
  // ---------------------------------------------------------------- icons (Lucide-style, 24px grid, 1.75 stroke)
  const P = {
    home: '<path d="m3 9.5 9-7 9 7V20a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    memory: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M12 5v13"/>',
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
    plug: '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    settings: '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    arrowUp: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    timer: '<path d="M10 2h4"/><path d="m12 14 3-3"/><circle cx="12" cy="14" r="8"/>',
    alarm: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M5 3 2 6"/><path d="m22 6-3-3"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    car: '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>',
    pin: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    window: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 9h20"/>',
    mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    calendar: '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    play: '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12-7.5a1 1 0 0 0 0-1.72l-12-7.5A1 1 0 0 0 7 4.5Z"/>',
    pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    next: '<path d="M5 5.5v13a1 1 0 0 0 1.5.86l10-6.5a1 1 0 0 0 0-1.72l-10-6.5A1 1 0 0 0 5 5.5Z"/><path d="M19 5v14" stroke-width="2.4"/>',
    prev: '<path d="M19 5.5v13a1 1 0 0 1-1.5.86l-10-6.5a1 1 0 0 1 0-1.72l10-6.5A1 1 0 0 1 19 5.5Z"/><path d="M5 5v14" stroke-width="2.4"/>',
    volume: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
    mute: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    sparkle: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M20 21a8 8 0 0 0-16 0"/>',
    bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
    mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
    zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    dollar: '<path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    route: '<circle cx="6" cy="19" r="3"/><path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15"/><circle cx="18" cy="5" r="3"/>',
  };
  const FILLED = new Set(["play", "pause", "next", "prev"]);
  const svg = (name, size = 16) =>
    `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="${FILLED.has(name) ? "currentColor" : "none"}" stroke="currentColor" ` +
    `stroke-width="${FILLED.has(name) ? 0 : 1.75}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ""}</svg>`;
  const ico = name => { const s = el("span"); s.dataset.icon = name; s.innerHTML = svg(name); return s; };
  const paintIcons = (root = document) => root.querySelectorAll("[data-icon]:empty").forEach(n => { n.innerHTML = svg(n.dataset.icon); });
  paintIcons();
  const setIcon = (node, name) => { node.dataset.icon = name; node.innerHTML = svg(name); };

  // ---------------------------------------------------------------- small helpers
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cap = s => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const clock = s => s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
    : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  function ago(ts) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  }
  const hhmm = t => { const m = /(\d{2}):(\d{2})/.exec(String(t).slice(11)); if (!m) return ""; const h = +m[1];
    return `${h % 12 || 12}:${m[2]} ${h < 12 ? "AM" : "PM"}`; };
  function row(k, v, icon, tone) {
    const r = el("div", "row"), kk = el("span", "k");
    if (icon) kk.append(ico(icon));
    kk.append(document.createTextNode(k));
    const vv = el("span", "v");
    if (v instanceof Node) vv.append(v); else vv.textContent = v == null || v === "" ? "—" : String(v);
    if (tone) vv.style.color = `var(--${tone})`;
    r.append(kk, vv);
    return r;
  }
  function tag(text, tone) { return el("span", `tag ${tone || ""}`, text); }
  function emptyState(icon, title, sub) {
    const e = el("div", "empty"), i = el("div", "e-icon");
    i.append(ico(icon)); e.append(i, el("b", null, title));
    if (sub) e.append(el("span", null, sub));
    return e;
  }

  // ---------------------------------------------------------------- toasts
  function toast(message, kind = "ok") {
    const t = el("div", `toast ${kind}`);
    t.append(ico(kind === "err" ? "alert" : "check"), el("span", null, message));
    $("#toasts").append(t);
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 250); }, kind === "err" ? 5000 : 3000);
  }
  window.toast = toast;

  // ---------------------------------------------------------------- navigation
  const PAGES = ["home", "chat", "memory", "activity", "status", "connections", "settings"];
  function show(page) {
    if (!PAGES.includes(page)) page = "home";
    document.querySelectorAll(".page").forEach(p => p.classList.toggle("active", p.id === `page-${page}`));
    document.querySelectorAll(".sb-nav a").forEach(a => a.classList.toggle("active", a.dataset.page === page));
    $(".main").classList.toggle("chat-mode", page === "chat");
    document.body.classList.remove("nav-open");
    document.title = page === "home" ? "Jarvis" : `${cap(page)} · Jarvis`;
    if (page === "memory") loadKnowledge();
    if (page === "connections") loadConnections();
    if (page === "status") loadStatus();
    if (page === "chat") { scrollFeed(true); setTimeout(() => $("#chat-input").focus(), 50); }
    if (location.hash.slice(1) !== page) history.replaceState(null, "", `#${page}`);
  }
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  $("#menu-btn").addEventListener("click", () => document.body.classList.add("nav-open"));
  $("#scrim").addEventListener("click", () => document.body.classList.remove("nav-open"));

  // ---------------------------------------------------------------- live state
  const STATES = {
    offline: ["Offline", "Not running"],
    starting: ["Starting up", "One moment"],
    wake_word_only: ["Standing by", "Say “Hey Jarvis”"],
    quiet: ["Quiet mode", "Silent until you say “Hey Jarvis”"],
    listening: ["Listening", "Go ahead"],
    processing: ["Thinking", "Working on it"],
    speaking: ["Speaking", "Answering out loud"],
    idle_check: ["Speaking", "Answering out loud"],
    call: ["On a call", "Talking on your phone"],
  };
  let live = { online: false }, polledAt = 0, pending = null, volumeDragging = false;

  async function api(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis": "1" }, body: JSON.stringify(body || {}) });
    let data = {};
    try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok && !data.message) data.message = data.error || "Jarvis couldn't do that.";
    return { ok: r.ok, ...data };
  }
  async function act(body, quiet) {
    const r = await api("/api/action", body);
    if (!quiet || !r.ok) toast(r.message || (r.ok ? "Done" : "That didn't work"), r.ok ? "ok" : "err");
    poll();
    return r;
  }

  async function poll() {
    try { live = await (await fetch("/api/live", { cache: "no-store" })).json(); }
    catch { live = { online: false }; }
    polledAt = Date.now();
    render();
  }

  function render() {
    document.body.classList.toggle("offline", !live.online);
    renderPresence(); renderHome(); renderFeed(); renderActivity(); renderSystem(); renderPhoneProvider();
  }

  function stateKey() {
    if (!live.online) return "offline";
    if (pending) return "processing";
    if (live.in_call) return "call";
    return (live.state && live.state.name) || "wake_word_only";
  }
  function renderPresence() {
    const key = stateKey();
    const [label, sub] = STATES[key] || [cap(key), ""];
    document.querySelectorAll(".orb").forEach(o => { o.dataset.state = key; });
    document.querySelectorAll('[data-bind="state"]').forEach(n => { n.textContent = label; });
    document.querySelectorAll('[data-bind="sub"]').forEach(n => { n.textContent = sub; });
    $("#sb-user").textContent = live.user ? `${cap(live.user)}'s assistant` : "Personal assistant";

    const q = $("#quiet-toggle");
    q.checked = key === "quiet";
    q.disabled = !live.online || !["quiet", "wake_word_only"].includes(key);

    const sp = live.spend || {}, today = sp.today || 0, limit = sp.limit || 0;
    $("#spend-text").textContent = live.online ? (limit > 0 ? `$${today.toFixed(2)} of $${limit.toFixed(2)}` : `$${today.toFixed(2)}`) : "—";
    $("#spend-fill").style.width = limit > 0 ? `${Math.min(100, (today / limit) * 100)}%` : "0%";
    $("#spend-fill").parentElement.classList.toggle("over", limit > 0 && today >= limit * 0.9);

    const ring = live.online && live.ringing;
    $("#ring-banner").classList.toggle("hidden", !ring);
    if (ring) $("#ring-text").textContent = `${cap(ring.label)} ${ring.kind === "alarm" ? "alarm" : "timer"} is ringing`;

    for (const id of ["#undo-btn", "#undo-btn-2"]) {
      const b = $(id);
      b.disabled = !live.online || !live.undo;
      b.title = live.undo ? `Undo: ${live.undo}` : "Nothing to undo";
    }
    $("#chat-send").disabled = !live.online || !!pending;
    document.querySelectorAll("#home-ask .send").forEach(b => { b.disabled = !live.online || !!pending; });
  }

  // ---------------------------------------------------------------- home
  function greeting() {
    const h = new Date().getHours();
    const part = h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    return live.user ? `${part}, ${cap(live.user)}` : part;
  }
  function renderHome() {
    $("#greeting").textContent = greeting();
    const d = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    $("#home-sub").textContent = [d, live.online && live.location].filter(Boolean).join("  ·  ");
    renderMedia(); renderTimers(); renderPhone(); renderEnv();
  }

  function renderMedia() {
    const box = $("#media"), np = live.online && live.now_playing;
    const sig = JSON.stringify([live.online, np]);
    if (box.dataset.sig !== sig) {
      box.dataset.sig = sig;
      box.innerHTML = "";
      if (!np || !np.title) {
        box.append(emptyState("music", "Nothing playing", live.online ? "Play something in Spotify or any media app" : "Waiting for Jarvis"));
      } else {
        const t = el("div", "track"), cover = el("div", "cover"), txt = el("div", "track-text");
        cover.append(ico("music"));
        const sub = el("div", "track-sub");
        if (np.playing) { const b = el("span", "bars"); b.append(el("i"), el("i"), el("i")); sub.append(b); }
        sub.append(document.createTextNode([np.artist, np.app].filter(Boolean).join(" · ")));
        txt.append(el("div", "track-title", np.title), sub);
        t.append(cover, txt);
        const tr = el("div", "transport");
        const mk = (icon, label, what, cls) => {
          const b = el("button", `icon-btn ${cls || ""}`); b.type = "button"; b.setAttribute("aria-label", label); b.title = label;
          b.append(ico(icon)); b.addEventListener("click", () => act({ do: what }, true)); return b;
        };
        tr.append(mk("prev", "Previous", "previous"), mk(np.playing ? "pause" : "play", np.playing ? "Pause" : "Play", "play_pause", "main-btn"),
                  mk("next", "Next", "next"));
        box.append(t, tr);
      }
    }
    const v = live.online && live.volume, range = $("#volume-range");
    const has = v && v.level != null;
    $("#volume").classList.toggle("hidden", !has);
    if (has && !volumeDragging) {
      range.value = v.level;
      range.style.setProperty("--v", `${v.muted ? 0 : v.level}%`);
      $("#volume-num").textContent = v.muted ? "Muted" : `${v.level}%`;
      setIcon($("#mute-btn [data-icon]"), v.muted ? "mute" : "volume");
      $("#mute-btn").title = v.muted ? "Unmute" : "Mute";
    }
  }
  const range = $("#volume-range");
  range.addEventListener("input", () => { volumeDragging = true; range.style.setProperty("--v", `${range.value}%`); $("#volume-num").textContent = `${range.value}%`; });
  range.addEventListener("change", async () => { await act({ do: "volume", percent: +range.value }, true); volumeDragging = false; });
  $("#mute-btn").addEventListener("click", () => act({ do: live.volume && live.volume.muted ? "unmute" : "mute" }, true));

  function renderTimers() {
    const box = $("#timers");
    const items = (live.online && live.timers) || [];
    const gone = Math.floor((Date.now() - polledAt) / 1000);
    box.innerHTML = "";
    if (!items.length) { box.append(emptyState("timer", "No timers", live.online ? "Start one here, or just ask Jarvis" : "Waiting for Jarvis")); return; }
    for (const t of items) {
      const left = Math.max(0, t.in_s - gone);
      const it = el("div", "titem"), r = el("div", "titem-row"), txt = el("div", "titem-text");
      txt.append(el("div", "titem-name", cap(t.label)),
                 el("div", "titem-kind", t.kind === "timer" ? `Rings at ${t.at}` : `Alarm${t.daily ? " · every day" : ""}`));
      const x = el("button", "icon-btn"); x.type = "button"; x.title = "Cancel"; x.setAttribute("aria-label", `Cancel ${t.label}`);
      x.append(ico("x"));
      x.addEventListener("click", () => act({ do: "cancel_timer", id: t.id }));
      r.append(ico(t.kind === "timer" ? "timer" : "alarm"), txt, el("span", "titem-left", t.kind === "timer" ? clock(left) : t.at), x);
      it.append(r);
      if (t.kind === "timer" && t.total_s) {
        const p = el("div", "progress"), f = el("i");
        f.style.width = `${Math.min(100, (1 - left / t.total_s) * 100)}%`;
        p.append(f); it.append(p);
      }
      box.append(it);
    }
  }
  setInterval(() => { if (live.online && (live.timers || []).length) renderTimers(); }, 1000);

  const PRESETS = [[1, "1 min"], [5, "5 min"], [10, "10 min"], [25, "25 min"], [60, "1 hour"]];
  for (const [m, label] of PRESETS) {
    const c = el("button", "chip", label); c.type = "button";
    c.addEventListener("click", () => startTimer(m * 60, $("#nt-label").value));
    $("#timer-presets").append(c);
  }
  $("#new-timer-btn").addEventListener("click", () => {
    const f = $("#new-timer"); f.classList.toggle("hidden");
    if (!f.classList.contains("hidden")) $("#nt-min").focus();
  });
  $("#new-timer").addEventListener("submit", e => {
    e.preventDefault();
    const m = parseFloat($("#nt-min").value);
    if (!(m > 0)) { $("#nt-min").focus(); return; }
    startTimer(Math.round(m * 60), $("#nt-label").value);
  });
  async function startTimer(seconds, label) {
    const r = await act({ do: "timer", seconds, label: (label || "").trim() }, true);
    if (r.ok) {
      toast(`Timer started${label ? `: ${label}` : ""}`);
      $("#new-timer").classList.add("hidden"); $("#nt-min").value = ""; $("#nt-label").value = "";
    }
  }

  function renderPhone() {
    const p = (live.online && live.phone) || {};
    const t = $("#phone-tag");
    t.className = `tag push ${p.in_call ? "green" : p.ready ? "green" : p.mode ? "amber" : ""}`;
    t.textContent = !live.online ? "Offline" : p.in_call ? "On a call" : p.ready ? "Ready" : p.mode ? "Needs setup" : "Off";
    const kv = $("#phone-kv"); kv.innerHTML = "";
    kv.append(row("Your number", p.number || "Not set", "phone"),
              row("Driving", p.driving ? `Yes${p.driving_source ? ` (${p.driving_source})` : ""}` : "No", "car", p.driving ? "amber" : null),
              row("Calls you about", "Urgent things only", "bell"));
    const b = $("#call-btn");
    b.disabled = !p.ready || p.in_call;
    b.title = p.ready ? "Jarvis calls your phone now" : "Set up the phone line first (see phone.md)";
  }
  $("#call-btn").addEventListener("click", () => {
    if (confirm(`Jarvis will call your phone (${(live.phone || {}).number || "your number"}) now. Twilio charges a few cents per call. Continue?`))
      act({ do: "call_me" });
  });

  function renderEnv() {
    const kv = $("#env-kv"); kv.innerHTML = "";
    const c = (live.online && live.connections) || {};
    const g = c.google || "—", gOk = String(g).startsWith("connected");
    kv.append(row("Location", live.online ? live.location || "Unknown" : "—", "pin"),
              row("App in focus", live.online ? live.app || "—" : "—", "window"),
              row("Google", gOk ? "Connected" : cap(g), "mail", gOk ? "green" : null),
              row("Brain", live.online ? live.brain : "—", "sparkle"));
  }

  // suggestions
  const SUGGEST = [["sun", "Brief me"], ["calendar", "What's on my calendar today?"], ["mail", "Any important emails?"],
                   ["sun", "What's the weather?"], ["bulb", "What have you learned about me?"]];
  for (const [icon, text] of SUGGEST) {
    const c = el("button", "chip needs-live"); c.type = "button"; c.append(ico(icon), document.createTextNode(text));
    c.addEventListener("click", () => send(text, true));
    $("#home-suggest").append(c);
  }
  $("#home-ask").addEventListener("submit", e => { e.preventDefault(); const v = $("#home-input").value; $("#home-input").value = ""; send(v, true); });
  for (const id of ["#undo-btn", "#undo-btn-2"]) $(id).addEventListener("click", () => act({ do: "undo" }));
  $("#ring-stop").addEventListener("click", () => act({ do: "stop_ringing" }, true));
  $("#quiet-toggle").addEventListener("change", e => act({ do: "quiet", on: e.target.checked }));
  $("#copy-start").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText("python main.py"); toast("Copied: python main.py"); } catch { toast("Couldn't copy", "err"); }
  });

  // ---------------------------------------------------------------- chat
  const STARTERS = [["sun", "Brief me", "Weather, calendar and anything important"], ["calendar", "What's on my calendar today?", "Your meetings and events"],
                    ["timer", "Set a timer for 10 minutes", "Timers and alarms"], ["window", "Open Spotify on my left monitor", "Apps and windows"]];
  let feedSig = "";
  function renderFeed() {
    const conv = live.online ? live.conversation || [] : [];
    const sig = JSON.stringify(conv) + (pending ? `|${pending}` : "") + live.online;
    if (sig === feedSig) return;
    const atBottom = isAtBottom();
    feedSig = sig;
    const feed = $("#feed");
    feed.innerHTML = "";
    if (!conv.length && !pending) {
      const w = el("div", "welcome"), orb = el("div", "orb lg"); orb.dataset.state = stateKey(); orb.append(el("span"));
      w.append(orb, el("h2", null, live.online ? "How can I help?" : "Jarvis is offline"),
               el("p", null, live.online ? "Ask anything, or try one of these." : "Start it with python main.py, then come back here."));
      const s = el("div", "starters");
      for (const [icon, title, sub] of STARTERS) {
        const b = el("button", "starter needs-live"); b.type = "button";
        const t = el("span"); t.append(el("b", null, title), el("span", "s", sub));
        b.append(ico(icon), t); b.addEventListener("click", () => send(title));
        s.append(b);
      }
      w.append(s); feed.append(w);
      return;
    }
    let lastDay = "";
    for (const m of conv) {
      const day = String(m.time || "").slice(0, 10);
      if (day && day !== lastDay) {
        lastDay = day;
        const dt = new Date(`${day}T12:00:00`), today = new Date().toDateString();
        feed.append(el("div", "day", dt.toDateString() === today ? "Today" : dt.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })));
      }
      feed.append(message(m.role, m.text, hhmm(m.time)));
    }
    if (pending) {
      if (!conv.length || conv[conv.length - 1].text !== pending) feed.append(message("user", pending));
      const t = message("assistant", ""), dots = el("span", "dots"); dots.append(el("i"), el("i"), el("i"));
      t.querySelector(".body").replaceWith(dots); feed.append(t);
    }
    if (atBottom) scrollFeed();
  }
  function message(role, text, time, error) {
    if (role === "user") { const m = el("div", "msg user", text); if (time) m.title = time; return m; }
    const m = el("div", `msg assistant${error ? " error" : ""}`), mark = el("div", "mark"), col = el("div"), meta = el("div", "meta");
    mark.append(el("i"), el("i"), el("i"));
    meta.append(el("b", null, "Jarvis"));
    if (time) meta.append(el("span", null, time));
    col.append(meta, el("div", "body", text));
    m.append(mark, col);
    return m;
  }
  const isAtBottom = () => { const s = $("#chat-scroll"); return s.scrollHeight - s.scrollTop - s.clientHeight < 80; };
  function scrollFeed(instant) { const s = $("#chat-scroll"); s.scrollTo({ top: s.scrollHeight, behavior: instant ? "auto" : "smooth" }); }

  async function send(text, goToChat) {
    text = (text || "").trim();
    if (!text || pending) return;
    if (!live.online) { toast("Jarvis isn't running. Start it with: python main.py", "err"); return; }
    if (goToChat) show("chat");
    pending = text;
    render(); scrollFeed();
    const r = await api("/api/command", { text });
    pending = null; feedSig = "";
    await poll();
    if (!r.ok) { $("#feed").append(message("assistant", r.message || "Something went wrong.", "", true)); scrollFeed(); }
  }
  const input = $("#chat-input");
  const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(180, input.scrollHeight)}px`; };
  input.addEventListener("input", grow);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#chat-form").requestSubmit(); }
  });
  $("#chat-form").addEventListener("submit", e => { e.preventDefault(); const v = input.value; input.value = ""; grow(); send(v); });

  // ---------------------------------------------------------------- activity
  const KIND_ICON = { app: "window", window: "window", volume: "volume", media: "music", timer: "timer", mail: "mail",
                      calendar: "calendar", phone: "car", undo: "undo" };
  function renderActivity() {
    const items = (live.online && live.activity) || [];
    const sig = JSON.stringify(items.map(a => [a.at, a.text])) + Math.floor(Date.now() / 30000);
    const targets = [["#activity-home", 6], ["#activity-full", 50]];
    for (const [id, n] of targets) {
      const box = $(id);
      if (box.dataset.sig === sig) continue;
      box.dataset.sig = sig;
      box.innerHTML = "";
      box.classList.toggle("cols", id === "#activity-home" && items.length > 3);
      if (!items.length) {
        box.classList.remove("cols");
        box.append(emptyState("activity", "Nothing yet", "Things Jarvis does for you show up here"));
        continue;
      }
      for (const a of items.slice(0, n)) {
        const ev = el("div", "ev"), i = el("div", "ev-icon");
        i.append(ico(KIND_ICON[a.kind] || "zap"));
        ev.append(i, el("span", "ev-text", a.text), el("span", "ev-time", ago(a.at)));
        box.append(ev);
      }
    }
  }

  // ---------------------------------------------------------------- status
  function renderSystem() {
    const kv = $("#sys-kv"); kv.innerHTML = "";
    const [label] = STATES[stateKey()] || [stateKey()];
    kv.append(row("State", label, "activity"), row("Wake word", live.online ? (live.wake_word || "").replace(/_/g, " ") : "—", "mic"),
              row("Brain", live.online ? live.brain : "—", "sparkle"), row("Voice", live.online ? live.voice : "—", "volume"),
              row("Hearing", live.online ? live.hearing : "—", "mic"),
              row("Phone line", live.online ? (live.phone && live.phone.ready ? "Ready" : "Off") : "—", "phone"));
    const t = (live.online && live.timing) || {}, box = $("#timing");
    const sig = JSON.stringify(t);
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig; box.innerHTML = "";
    const parts = [["hearing", "Understanding you"], ["first_words", "Thinking"], ["tools", "Taking action"], ["first_sound", "Until it spoke"]]
      .filter(([k]) => t[k] != null);
    if (!parts.length) { box.append(emptyState("clock", "No timings yet", "Talk to Jarvis and they'll show up here")); return; }
    for (const [k, label2] of parts) {
      const bar = el("div", "bar"), lbl = el("div", "lbl"), track = el("div", "bar-track"), fill = el("div", `fill${t[k] > 2.5 ? " slow" : ""}`);
      lbl.append(el("span", null, label2), el("span", null, `${t[k].toFixed(2)}s`));
      fill.style.width = `${Math.min(100, (t[k] / 4) * 100)}%`;
      track.append(fill); bar.append(lbl, track); box.append(bar);
    }
  }
  function renderPhoneProvider() {
    const p = (live.online && live.phone) || {};
    const pill = $("#phone-pill");
    pill.className = `pill ${p.ready ? "connected" : p.mode ? "expired" : ""}`;
    pill.textContent = !live.online ? "Unknown" : p.ready ? "Ready" : p.mode ? "Needs setup" : "Off";
    $("#phone-detail").textContent = !live.online ? "Start Jarvis to see the phone line." : p.ready
      ? `Calls go to ${p.number}. ${p.driving ? "You're driving right now." : ""}`
      : "Turn on PHONE_MODE in Settings and follow phone.md to set it up.";
  }

  // ---------------------------------------------------------------- memory
  async function loadKnowledge() {
    let k;
    try {
      const r = await fetch("/api/knowledge", { cache: "no-store" });
      k = await r.json();
      if (!r.ok) throw new Error(k.error);
    } catch {
      for (const id of ["#mem-profile", "#mem-prefs", "#mem-facts", "#mem-summaries"]) {
        $(id).innerHTML = ""; $(id).append(emptyState("power", "Jarvis is offline", "Start it to see what it remembers"));
      }
      return;
    }
    const prof = $("#mem-profile"); prof.innerHTML = "";
    if (!k.profile.length) prof.append(emptyState("user", "Nothing yet", "Tell Jarvis your name, where you live, what you do"));
    for (const p of k.profile) prof.append(row(p.label, p.value));

    const prefs = $("#mem-prefs"); prefs.innerHTML = "";
    $("#pref-count").textContent = k.preferences.length || "";
    if (!k.preferences.length) prefs.append(emptyState("bulb", "No preferences yet", "Say “always…”, or correct Jarvis and it learns"));
    for (const p of k.preferences) {
      const it = el("div", "item"), main = el("div", "item-main"), meta = el("div", "item-meta");
      main.append(el("div", "item-text", cap(p.text)));
      if (p.source === "explicit") meta.append(tag("You told Jarvis", "blue"));
      else {
        const c = el("span", "conf"), m = el("span", "meter"), f = el("i");
        f.style.width = `${Math.round(p.confidence * 100)}%`; m.append(f);
        c.append(m, document.createTextNode(`${Math.round(p.confidence * 100)}% sure · seen ${p.evidence}×`));
        meta.append(p.applied ? tag("Learned", "green") : tag("Still learning", "amber"), c);
      }
      main.append(meta);
      const actions = el("div", "item-actions");
      if (p.applied) {
        const auto = el("label", "auto"), sw = el("span", "switch"), cb = el("input");
        cb.type = "checkbox"; cb.checked = p.auto; sw.append(cb, el("span", "slider"));
        auto.title = "Apply this automatically"; auto.append(document.createTextNode("Auto"), sw);
        cb.addEventListener("change", () => act({ do: "auto_preference", key: p.key, on: cb.checked }, true));
        actions.append(auto);
      }
      if (p.kind === "routine") {
        const run = el("button", "btn sm", "Run"); run.type = "button";
        run.addEventListener("click", () => act({ do: "routine", trigger: p.key.split(":").slice(1).join(":") }));
        actions.append(run);
      }
      const del = el("button", "icon-btn"); del.type = "button"; del.title = "Forget"; del.setAttribute("aria-label", "Forget"); del.append(ico("trash"));
      del.addEventListener("click", async () => {
        if (!confirm(`Forget this preference?\n\n${cap(p.text)}`)) return;
        await act({ do: "forget_preference", key: p.key }); loadKnowledge();
      });
      actions.append(del);
      it.append(main, actions); prefs.append(it);
    }

    const facts = $("#mem-facts");
    $("#fact-count").textContent = k.facts.length || "";
    const drawFacts = () => {
      const q = $("#fact-filter").value.trim().toLowerCase();
      facts.innerHTML = "";
      const list = k.facts.filter(f => !q || f.text.toLowerCase().includes(q));
      if (!list.length) facts.append(emptyState("memory", q ? "No matches" : "Nothing remembered yet", q ? "" : "Jarvis remembers useful things you mention"));
      for (const f of list.slice(0, 200)) {
        const it = el("div", "item"), main = el("div", "item-main"), meta = el("div", "item-meta");
        main.append(el("div", "item-text", f.text));
        meta.append(el("span", null, `Saved ${f.saved}`));
        if (f.category && f.category !== "fact") meta.append(tag(cap(f.category), "plain"));
        main.append(meta);
        const actions = el("div", "item-actions"), del = el("button", "icon-btn");
        del.type = "button"; del.title = "Forget"; del.setAttribute("aria-label", "Forget"); del.append(ico("trash"));
        del.addEventListener("click", async () => {
          if (!confirm(`Forget this?\n\n${f.text}`)) return;
          const r = await act({ do: "forget_fact", id: f.id });
          if (r.ok) { k.facts = k.facts.filter(x => x.id !== f.id); $("#fact-count").textContent = k.facts.length || ""; drawFacts(); }
        });
        actions.append(del); it.append(main, actions); facts.append(it);
      }
    };
    $("#fact-filter").oninput = drawFacts;
    drawFacts();

    const sums = $("#mem-summaries"); sums.innerHTML = "";
    if (!k.summaries.length) sums.append(emptyState("chat", "No past conversations", "Short notes about your talks show up here"));
    for (const s of k.summaries) {
      const d = el("div", "summary");
      d.append(el("div", "when", String(s.date || "").slice(0, 16).replace("T", " ")), el("div", null, s.summary));
      sums.append(d);
    }
  }
  $("#mem-refresh").addEventListener("click", () => { loadKnowledge(); toast("Memory refreshed"); });

  // ---------------------------------------------------------------- command menu
  const pal = $("#palette"), pin = $("#palette-input"), plist = $("#palette-list");
  let pitems = [], psel = 0;
  const NAV = [["home", "Home", "home"], ["chat", "Chat", "chat"], ["memory", "Memory", "memory"], ["activity", "Activity", "activity"],
               ["status", "Status", "cpu"], ["connections", "Connections", "plug"], ["settings", "Settings", "settings"]];
  function commands() {
    const muted = live.volume && live.volume.muted, playing = live.now_playing && live.now_playing.playing;
    const quiet = stateKey() === "quiet";
    return [
      { group: "Actions", icon: playing ? "pause" : "play", label: playing ? "Pause music" : "Play music", run: () => act({ do: "play_pause" }, true), live: 1, keys: "music song media resume stop" },
      { group: "Actions", icon: "next", label: "Next track", run: () => act({ do: "next" }, true), live: 1, keys: "skip song music" },
      { group: "Actions", icon: "prev", label: "Previous track", run: () => act({ do: "previous" }, true), live: 1, keys: "back song music" },
      { group: "Actions", icon: muted ? "volume" : "mute", label: muted ? "Unmute" : "Mute", run: () => act({ do: muted ? "unmute" : "mute" }, true), live: 1, keys: "volume sound audio silence" },
      { group: "Actions", icon: "timer", label: "Start a 5 minute timer", run: () => startTimer(300, ""), live: 1, keys: "alarm countdown" },
      { group: "Actions", icon: "timer", label: "Start a 25 minute focus timer", run: () => startTimer(1500, "focus"), live: 1, keys: "pomodoro work alarm" },
      { group: "Actions", icon: "undo", label: live.undo ? `Undo: ${live.undo}` : "Undo last change", run: () => act({ do: "undo" }), live: 1, off: !live.undo, keys: "revert back" },
      { group: "Actions", icon: "moon", label: quiet ? "Turn off quiet mode" : "Turn on quiet mode", run: () => act({ do: "quiet", on: !quiet }), live: 1, keys: "silent sleep do not disturb dnd" },
      { group: "Actions", icon: "phone", label: "Call my phone", run: () => $("#call-btn").click(), live: 1, off: !(live.phone && live.phone.ready), keys: "ring twilio" },
      { group: "Ask Jarvis", icon: "sun", label: "Brief me", run: () => send("Brief me", true), live: 1 },
      { group: "Ask Jarvis", icon: "calendar", label: "What's on my calendar today?", run: () => send("What's on my calendar today?", true), live: 1 },
      { group: "Ask Jarvis", icon: "mail", label: "Any important emails?", run: () => send("Any important emails?", true), live: 1 },
      { group: "Ask Jarvis", icon: "pin", label: "Where am I?", run: () => send("Where am I?", true), live: 1 },
      ...NAV.map(([page, label, icon]) => ({ group: "Go to", icon, label, run: () => show(page), hint: "Page" })),
    ].filter(c => !c.off && (!c.live || live.online));
  }
  function drawPalette() {
    const q = pin.value.trim().toLowerCase();
    pitems = commands().filter(c => !q || `${c.label} ${c.keys || ""} ${c.group}`.toLowerCase().includes(q));
    if (q && live.online) pitems.unshift({ group: "Ask Jarvis", icon: "sparkle", label: `Ask Jarvis: “${pin.value.trim()}”`, run: () => send(pin.value.trim(), true), hint: "Enter" });
    psel = Math.min(psel, Math.max(0, pitems.length - 1));
    plist.innerHTML = "";
    if (!pitems.length) { plist.append(el("div", "p-none", live.online ? "No matches" : "Jarvis is offline. Start it with python main.py")); return; }
    let group = "";
    pitems.forEach((c, i) => {
      if (c.group !== group) { group = c.group; plist.append(el("div", "p-group", group)); }
      const it = el("div", `p-item${i === psel ? " sel" : ""}`);
      it.append(ico(c.icon), el("span", null, c.label));
      if (c.hint) it.append(el("span", "hint-r", c.hint));
      it.addEventListener("mousemove", () => { if (psel !== i) { psel = i; mark(); } });
      it.addEventListener("click", () => runItem(i));
      plist.append(it);
    });
  }
  function mark() { plist.querySelectorAll(".p-item").forEach((n, i) => n.classList.toggle("sel", i === psel)); const s = plist.querySelector(".sel"); if (s) s.scrollIntoView({ block: "nearest" }); }
  function runItem(i) { const c = pitems[i]; if (!c) return; closePalette(); c.run(); }
  function openPalette() { pal.classList.remove("hidden"); pin.value = ""; psel = 0; drawPalette(); setTimeout(() => pin.focus(), 10); }
  function closePalette() { pal.classList.add("hidden"); }
  pin.addEventListener("input", () => { psel = 0; drawPalette(); });
  pin.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); psel = Math.min(pitems.length - 1, psel + 1); mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); psel = Math.max(0, psel - 1); mark(); }
    else if (e.key === "Enter") { e.preventDefault(); runItem(psel); }
    else if (e.key === "Escape") closePalette();
  });
  pal.addEventListener("mousedown", e => { if (e.target === pal) closePalette(); });
  $("#open-palette").addEventListener("click", openPalette);
  document.querySelectorAll("[data-palette]").forEach(b => b.addEventListener("click", openPalette));
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); pal.classList.contains("hidden") ? openPalette() : closePalette(); }
    else if (e.key === "Escape" && !pal.classList.contains("hidden")) closePalette();
    else if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); openPalette(); }
  });

  // ---------------------------------------------------------------- start
  show(location.hash.slice(1) || "home");
  poll();
  setInterval(poll, 1000);
  setInterval(() => { if ($("#page-memory").classList.contains("active") && live.online && document.visibilityState === "visible") loadKnowledge(); }, 30000);
})();
