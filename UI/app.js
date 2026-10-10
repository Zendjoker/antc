const changes = {}; // key -> new value, only fields the user actually edited
const settingsBaseline = {};
let settingsSaving = false, settingsLoad = 0, settingsRevision = 0;
let settingsSubmitted = {};

// Deadlines cover both the request and response body. Abort is never proof that a mutation failed.
async function requestJSON(path, options = {}, timeoutMs = 10000) {
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(path, { ...options, signal: controller.signal });
        let data;
        try { data = await response.json(); }
        catch { throw new Error("Invalid response. The outcome could not be confirmed."); }
        if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Unexpected response. The outcome could not be confirmed.");
        if (!response.ok) {
          const error = new Error(data.error || data.message || "Request failed.");
          error.unknown = response.status >= 500;
          throw error;
        }
        return { data, status: response.status };
      })(),
      new Promise((_, reject) => { timer = setTimeout(() => {
        controller.abort(); reject(new Error("Request timed out. The outcome could not be confirmed."));
      }, timeoutMs); }),
    ]);
  } finally { clearTimeout(timer); }
}

function $(sel, root = document) { return root.querySelector(sel); }
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// (page navigation lives in jarvis.js)

// ---- status ----
const STATUS_ICONS = {
  brain: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M12 5v13"/>',
  ear: '<path d="M6 8.5a6.5 6.5 0 1 1 13 0c0 6-6 6-6 10a3.5 3.5 0 1 1-7 0"/><path d="M15 8.5a2.5 2.5 0 0 0-5 0v1a2 2 0 1 1 0 4"/>',
  volume: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
  wake: '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
  speaker: '<rect x="4" y="2" width="16" height="20" rx="2"/><circle cx="12" cy="14" r="4"/><path d="M12 6h.01"/>',
  shield: '<path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"/><path d="m8 12 3 3 5-6"/>',
  dollar: '<path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  pulse: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
};
function statusIcon(name) {
  const s = el("span", "st-icon");
  s.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${STATUS_ICONS[name] || ""}</svg>`;
  return s;
}
function statusTile(icon, label, value, tone, extra) {
  const tile = el("div", `status-tile${tone ? " " + tone : ""}`), text = el("div", "st-text");
  text.append(el("div", "label", label), el("div", `value${tone ? " " + tone : ""}`, value == null || value === "" ? "Not set" : String(value)));
  if (extra) text.append(extra);
  tile.append(statusIcon(icon), text);
  return tile;
}

async function loadStatus() {
  const res = await fetch("/api/status");
  const s = await res.json();
  const board = $("#status-cards");

  const spentUsd = Object.values(s.spend.usd || {}).reduce((a, b) => a + b, 0);
  const budget = s.daily_budget;
  const overBudget = budget > 0 && spentUsd >= budget;

  // Only facts reported by /api/status: nothing here is inferred or simulated.
  const issues = [];
  if (overBudget) issues.push(`Today's spend ($${spentUsd.toFixed(2)}) has reached the $${budget.toFixed(2)} daily limit.`);
  if (s.speaker_verify && !s.voiceprint_enrolled) issues.push("Voice verification is on, but your voice isn't enrolled yet.");
  if (!s.mic_device) issues.push("No microphone is configured.");

  const summary = el("section", `status-summary ${issues.length ? "attention" : "ok"}`);
  const sumText = el("div", "ss-text");
  sumText.append(el("h2", null, issues.length ? `${issues.length} ${issues.length === 1 ? "thing needs" : "things need"} your attention` : "Everything is set up"),
                 el("p", null, issues.length ? "Based on the current configuration." : "No configuration problems reported."));
  summary.append(statusIcon(issues.length ? "alert" : "check"), sumText);
  if (issues.length) { const ul = el("ul", "ss-issues"); issues.forEach(t => ul.append(el("li", null, t))); summary.append(ul); }

  const group = (title, tiles, cls) => {
    const g = el("section", `status-group${cls ? " " + cls : ""}`), grid = el("div", "status-tiles");
    tiles.forEach(t => grid.append(t));
    g.append(el("h3", null, title), grid);
    return g;
  };
  const meter = el("div", `st-meter${overBudget ? " over" : ""}`), fill = el("i");
  fill.style.width = budget > 0 ? `${Math.min(100, (spentUsd / budget) * 100)}%` : "0%";
  meter.append(fill);
  const groups = el("div", "status-groups");
  groups.append(
    group("Intelligence & voice", [
      statusTile("brain", "Brain", s.llm_default === "openai" ? s.openai_model : s.model),
      statusTile("ear", "Speech-to-text", `${s.stt_provider}${s.stt_provider === "whisper" ? " · " + s.whisper_model : ""}`),
      statusTile("volume", "Text-to-speech", s.tts_provider),
      statusTile("wake", "Wake word", String(s.wake_word || "").replace(/_/g, " ")),
    ]),
    group("Devices & privacy", [
      statusTile("mic", "Microphone", s.mic_device),
      statusTile("speaker", "Speaker", s.speaker_device),
      statusTile("shield", "Voice verification", s.speaker_verify ? (s.voiceprint_enrolled ? "On, enrolled" : "On, not enrolled") : "Off",
                 s.speaker_verify && !s.voiceprint_enrolled ? "warn" : ""),
    ]),
    group("Usage today", [
      statusTile("dollar", "Spend", budget > 0 ? `$${spentUsd.toFixed(2)} of $${budget.toFixed(2)}` : `$${spentUsd.toFixed(2)} (no limit)`,
                 overBudget ? "warn" : "", budget > 0 ? meter : null),
      statusTile("pulse", "API calls", s.spend.calls ?? 0),
    ], "usage"),
  );
  board.replaceChildren(summary, groups);
}

// ---- settings ----
function markDirty(input, key, value) {
  settingsRevision++;
  if (value === settingsBaseline[key] && (!settingsSaving || !(key in settingsSubmitted))) delete changes[key];
  else changes[key] = value;
  input.classList.toggle("dirty", key in changes);
  updateSaveBar();
}

function buildField(f) {
  const row = el("div", "field");
  row.dataset.search = [f.key, f.comment].filter(Boolean).join(" ").toLowerCase().replace(/_/g, " ");
  const info = el("div");
  const names = { MIC_DEVICE: "Microphone", SPEAKER_DEVICE: "Speaker", LLM_PROVIDER: "AI provider", LLM_DEFAULT: "Default AI", LLM_SMART: "Advanced AI", STT_PROVIDER: "Speech recognition", TTS_PROVIDER: "Voice provider", DAILY_BUDGET_USD: "Daily spending limit ($)", ANTHROPIC_API_KEY: "Anthropic API key", OPENAI_API_KEY: "OpenAI API key", ELEVENLABS_API_KEY: "ElevenLabs API key", DEEPGRAM_API_KEY: "Deepgram API key", ELEVENLABS_VOICE_ID: "ElevenLabs voice", PIPER_VOICE: "Local voice", HA_URL: "Home Assistant address", HA_TOKEN: "Home Assistant access token", SPEAKER_VERIFY: "Recognize your voice", BARGE_IN: "Allow interruptions", WEATHER_LOCATION: "Weather location", WAKE_WORD: "Wake word", WAKE_THRESHOLD: "Wake word sensitivity (threshold)" };
  const label = el("label", "key", names[f.key] || f.key.toLowerCase().replace(/_/g, " ").replace(/^./, c => c.toUpperCase()));
  label.htmlFor = `setting-${f.key}`; label.title = f.key; info.append(label);
  if (f.comment) info.append(el("div", "comment", f.comment));
  row.appendChild(info);

  const control = el("div", "control");

  if (f.secret) {
    const wrap = el("div", "secret-row");
    const display = el("input");
    display.type = "text";
    display.value = f.value;
    display.readOnly = true;
    display.placeholder = f.has_value ? "" : "(not set)";
    const btn = el("button", "ghost", "Change");
    btn.type = "button";
    btn.addEventListener("click", () => {
      display.readOnly = false;
      display.type = "password";
      display.value = "";
      display.placeholder = "enter a new value";
      display.focus();
      btn.remove();
      display.addEventListener("input", () => markDirty(display, f.key, display.value));
    });
    wrap.append(display, btn);
    control.appendChild(wrap);
  } else if (f.type === "bool") {
    const label = el("label", "switch");
    const input = el("input");
    input.type = "checkbox";
    input.checked = f.value === "1";
    input.addEventListener("change", () => markDirty(input, f.key, input.checked ? "1" : "0"));
    label.append(input, el("span", "slider"));
    control.appendChild(label);
  } else if (f.type === "select") {
    const select = el("select");
    for (const opt of f.options) {
      const value = typeof opt === "string" ? opt : opt.value;
      const label = typeof opt === "string" ? opt : opt.label;
      const o = el("option", null, label);
      o.value = value;
      if (value === f.value) o.selected = true;
      select.appendChild(o);
    }
    if (!f.options.some(o => (typeof o === "string" ? o : o.value) === f.value) && f.value) {
      const o = el("option", null, `${f.value} (custom)`);
      o.value = f.value;
      o.selected = true;
      select.prepend(o);
    }
    select.addEventListener("change", () => markDirty(select, f.key, select.value));
    control.appendChild(select);
  } else {
    const input = el("input");
    input.type = f.type === "number" ? "number" : "text";
    if (f.type === "number") input.step = "any";
    input.value = f.value;
    input.addEventListener("input", () => markDirty(input, f.key, input.value));
    control.appendChild(input);
  }

  row.appendChild(control);
  const field = control.querySelector("input, select");
  if (field) field.id = `setting-${f.key}`;
  return row;
}

async function loadSettings() {
  const generation = ++settingsLoad, revision = settingsRevision;
  const { data: { sections } } = await requestJSON("/api/env");
  if (generation !== settingsLoad || revision !== settingsRevision || settingsSaving || Object.keys(changes).length) return;
  if (!Array.isArray(sections)) throw new Error("Settings could not be loaded.");
  const root = $("#settings-sections");
  root.innerHTML = "";
  sections.forEach((section, i) => {
    const wrap = el("div", i === 0 || root.dataset.open === section.title ? "section" : "section collapsed");
    const title = el("button", "section-title");
    title.type = "button";
    title.setAttribute("aria-expanded", String(!wrap.classList.contains("collapsed")));
    title.setAttribute("aria-controls", `settings-section-${i}`);
    title.append(el("span", null, section.title), el("span", "chevron", "▾"));
    title.addEventListener("click", () => { wrap.classList.toggle("collapsed"); title.setAttribute("aria-expanded", String(!wrap.classList.contains("collapsed"))); if (!wrap.classList.contains("collapsed")) root.dataset.open = section.title; });
    const body = el("div", "section-body");
    body.id = `settings-section-${i}`;
    for (const f of section.fields) { settingsBaseline[f.key] = f.value; body.appendChild(buildField(f)); }
    wrap.append(title, body);
    root.appendChild(wrap);
  });
  filterSettings();
}

function filterSettings() {
  const query = $("#settings-search").value.trim().toLowerCase().replace(/_/g, " ");
  let matches = 0;
  document.querySelectorAll("#settings-sections .section").forEach(section => {
    let count = 0;
    section.querySelectorAll(".field").forEach(field => {
      const match = !query || `${field.dataset.search} ${field.querySelector(".key").textContent.toLowerCase()} ${section.querySelector(".section-title").textContent.toLowerCase()}`.includes(query);
      field.hidden = !match; if (match) count++;
    });
    section.hidden = !count;
    if (query && count) {
      if (!section.dataset.beforeSearch) section.dataset.beforeSearch = section.classList.contains("collapsed") ? "collapsed" : "open";
      section.classList.remove("collapsed");
    } else if (!query && section.dataset.beforeSearch) {
      section.classList.toggle("collapsed", section.dataset.beforeSearch === "collapsed"); delete section.dataset.beforeSearch;
    }
    section.querySelector(".section-title").setAttribute("aria-expanded", String(!section.classList.contains("collapsed")));
    matches += count;
  });
  $("#settings-no-results").classList.toggle("hidden", matches > 0 || !query);
}
$("#settings-search").addEventListener("input", filterSettings);

function updateSaveBar() {
  const n = Object.keys(changes).length;
  $("#save-hint").textContent = settingsSaving ? "Saving… New edits will stay unsaved." : n ? `${n} unsaved change${n > 1 ? "s" : ""}` : "No unsaved changes";
  $("#save-btn").disabled = settingsSaving || n === 0;
  $("#save-btn").textContent = settingsSaving ? "Saving…" : "Save changes";
}

function showBanner(msg, isError) {
  const b = $("#banner");
  b.textContent = msg;
  b.className = `banner${isError ? " error" : ""}`;
}

$("#save-btn").addEventListener("click", async () => {
  if (settingsSaving || !Object.keys(changes).length) return;
  const submitted = { ...changes };
  settingsSubmitted = submitted;
  settingsSaving = true; settingsLoad++; updateSaveBar();
  try {
    const { data } = await requestJSON("/api/env", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jarvis": "1" },
      body: JSON.stringify(submitted),
    });
    if (data.ok !== true || !Array.isArray(data.changed) || !Object.keys(submitted).every(k => data.changed.includes(k))) throw new Error("Save could not be confirmed. Your edits are retained.");
    for (const [key, value] of Object.entries(submitted)) {
      settingsBaseline[key] = String(value).replace(/[\r\n]/g, " ").trim();
      if (changes[key] === value || changes[key] === settingsBaseline[key]) {
        delete changes[key];
        const field = $("#setting-" + key);
        if (field) { field.classList.remove("dirty"); if (field.type !== "checkbox") field.value = settingsBaseline[key]; }
      }
    }
    const applied = Array.isArray(data.applied) ? data.applied : [];
    const switched = applied.length ? "Audio devices switched now. " : "";
    const liveError = data.live_error ? `Couldn't switch audio devices now (${data.live_error}); they'll be used after a restart. ` : "";
    const restart = data.restart_required === false ? "" : "Restart ZEND for the other changes to take effect.";
    showBanner(Object.keys(changes).length ? `Submitted settings saved. ${switched}Newer edits remain unsaved. Restart ZEND after saving all changes.`
                                           : `Settings saved. ${switched}${liveError}${applied.length && restart ? restart : restart.replace(" the other", " these")}`.trim(), !!data.live_error);
    if (window.toast) toast(applied.length ? "Audio devices switched" : "Settings saved");
  } catch (e) {
    showBanner(`${e.message} Your edits are retained. Check settings before retrying if the outcome is unknown.`, true);
  } finally { settingsSaving = false; settingsSubmitted = {}; updateSaveBar(); }
});

// ---- Settings -> Connections ----
const STATUS_TEXT = { connected: "Connected", expired: "Needs reconnecting", disconnected: "Not connected",
                      not_configured: "Not set up" };

async function api(path, body) {
  if (pendingFlow && /\/(connect|reconnect|access)$/.test(path) && body?.on !== false) throw new Error("A previous sign-in is unconfirmed. Check that sign-in before starting another.");
  const { data } = await requestJSON(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis": "1" }, body: JSON.stringify(body || {}) });
  if (data.ok === false || data.error) throw new Error(data.error || data.message || "Request rejected.");
  if (data.ok !== true && typeof data.flow !== "string") throw new Error("Request outcome could not be confirmed.");
  return data;
}

function connBanner(msg, isError) {
  const b = $("#conn-banner");
  b.textContent = msg;
  b.className = `banner${isError ? " error" : ""}`;
}

let connectionBusy = false, connectionGeneration = 0, pendingFlow = "";
async function connectionOperation(operation, rollback) {
  if (connectionBusy) { if (rollback) rollback(); return; }
  connectionBusy = true; connectionGeneration++;
  const controls = [...$("#providers").querySelectorAll("button, input")].map(node => [node, node.disabled]);
  controls.forEach(([node]) => { node.disabled = true; });
  $("#providers").setAttribute("aria-busy", "true");
  connBanner("Working…");
  try { await operation(); }
  catch (e) {
    if (rollback) rollback();
    connBanner(`${e.message} Refresh connection status before retrying an operation.`, true);
    const retry = el("button", "btn sm", pendingFlow ? "Check sign-in again" : "Refresh status");
    retry.addEventListener("click", () => connectionOperation(() => pendingFlow ? follow(pendingFlow) : loadConnections()));
    $("#conn-banner").append(retry);
  } finally {
    connectionBusy = false; $("#providers").setAttribute("aria-busy", "false");
    controls.forEach(([node, disabled]) => { node.disabled = disabled; });
    $("#providers").querySelectorAll("button, input").forEach(node => { if (!controls.some(([old]) => old === node)) node.disabled = false; });
  }
}

async function follow(flowId, timeoutMs = 120000) {
  if (typeof flowId !== "string" || !flowId) throw new Error("Sign-in was not confirmed.");
  pendingFlow = flowId;
  connBanner("Finish signing in in the browser window that just opened...");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1500));
    const { data: f } = await requestJSON(`/api/connections/flows/${encodeURIComponent(flowId)}`, {}, Math.max(1, Math.min(10000, deadline - Date.now())));
    if (f.state === "pending") continue;
    if (!["done", "error", "canceled"].includes(f.state)) throw new Error("Unexpected sign-in status.");
    pendingFlow = "";
    if (f.state !== "done") throw new Error(f.message || "Sign-in did not complete.");
    connBanner(f.message || "Sign-in completed.");
    await loadConnections();
    return;
  }
  throw new Error("Sign-in is still unconfirmed. You can check the same sign-in again without opening another flow.");
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function providerCard(p) {
  const card = el("div", "provider");
  const head = el("div", "provider-head");
  const overall = p.configured ? (p.accounts.length ? (p.accounts.some(a => a.status === "connected") ? "connected" : "expired")
                                                     : "disconnected") : "not_configured";
  head.append(el("div", "logo", p.name[0]), el("div", "name", p.name), el("span", `pill ${overall}`, STATUS_TEXT[overall]));
  if (p.configured) {
    const add = el("button", "btn", p.accounts.length ? "Add another account" : `Connect ${p.name}`);
    add.addEventListener("click", () => connectionOperation(async () => { await follow((await api(`/api/connections/${p.id}/connect`, { levels: { gmail: ["read"], calendar: ["read"] } })).flow); }));
    head.append(add);
  }
  card.append(head);
  if (!p.configured) card.append(el("div", "setup", p.setup));

  for (const a of p.accounts) {
    const box = el("div", "account");
    const ah = el("div", "account-head");
    ah.append(el("span", "who", a.account + (a.active ? "  (used by ZEND)" : "")),
              el("span", `pill ${a.status}`, STATUS_TEXT[a.status] || a.status));
    if (!a.active) {
      const use = el("button", "btn ghost", "Use this account");
      use.addEventListener("click", () => connectionOperation(async () => { await api(`/api/connections/${p.id}/active`, { account: a.account }); connBanner("Account selection confirmed."); await loadConnections(); }));
      ah.append(use);
    }
    if (a.status === "expired") {
      const re = el("button", "btn", "Reconnect");
      re.addEventListener("click", () => connectionOperation(async () => follow((await api(`/api/connections/${p.id}/reconnect`, { account: a.account })).flow)));
      ah.append(re);
    }
    const dis = el("button", "btn danger", "Disconnect");
    dis.addEventListener("click", async () => {
      if (connectionBusy) return;
      if (!confirm(`Disconnect ${a.account}? ZEND loses access and its saved key is deleted.`)) return;
      await connectionOperation(async () => {
      const r = await api(`/api/connections/${p.id}/disconnect`, { account: a.account });
      if (typeof r.revoked !== "boolean") throw new Error("Disconnect outcome was not confirmed.");
      connBanner(r.revoked ? "Disconnected, and access was revoked at the provider." :
                 "Disconnected here. The provider couldn't be reached to revoke access: remove it in your account's security settings.", !r.revoked);
      await loadConnections();
      });
    });
    ah.append(dis);
    box.append(ah);
    box.append(el("div", "meta", `Last used: ${ago(a.last_success)}${a.last_error ? " · last problem: " + a.last_error : ""}`));
    for (const svc of a.services) {
      const s = el("div", "service");
      s.append(el("div", "svc-name", svc.name));
      for (const l of svc.levels) {
        const row = el("label", "level");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = l.enabled;
        cb.addEventListener("change", async () => {
          await connectionOperation(async () => {
            const r = await api(`/api/connections/${p.id}/access`, { account: a.account, service: svc.id, level: l.id, on: cb.checked });
            if (r.flow) await follow(r.flow); else { connBanner("Access setting confirmed."); await loadConnections(); }
          }, () => { cb.checked = l.enabled; });
        });
        row.append(cb, el("span", null, l.label));
        if (!l.granted) row.append(el("span", "note", "(asks Google for this permission)"));
        s.append(row);
      }
      box.append(s);
    }
    if (a.permissions.length) box.append(el("div", "meta", "Google granted: " + a.permissions.join(", ")));
    card.append(box);
  }
  return card;
}

async function loadConnections() {
  const generation = ++connectionGeneration;
  try {
  const { data: { providers } } = await requestJSON("/api/connections");
  if (generation !== connectionGeneration) return;
  if (!Array.isArray(providers)) throw new Error("Invalid connection status.");
  const root = $("#providers");
  root.innerHTML = "";
  for (const p of providers) root.appendChild(providerCard(p));
  if (connectionBusy) root.querySelectorAll("button, input").forEach(node => { node.disabled = true; });
  } catch (e) {
    if (generation !== connectionGeneration) return;
    if (connectionBusy) throw e;
    connBanner(`${e.message} Connection status is unavailable.`, true);
    const retry = el("button", "btn sm", "Refresh status"); retry.addEventListener("click", loadConnections); $("#conn-banner").append(retry);
  }
}

loadStatus().catch(() => { $("#status-cards").textContent = "Configuration unavailable. Reopen Status to retry."; });
loadSettings().catch(e => showBanner(e.message + " Reopen Settings to retry.", true));
setInterval(() => loadStatus().catch(() => {}), 15000);
