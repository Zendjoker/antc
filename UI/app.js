const changes = {}; // key -> new value, only fields the user actually edited

function $(sel, root = document) { return root.querySelector(sel); }
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// (page navigation lives in jarvis.js)

// ---- status ----
async function loadStatus() {
  const res = await fetch("/api/status");
  const s = await res.json();
  const cards = $("#status-cards");
  cards.innerHTML = "";

  const spentUsd = Object.values(s.spend.usd || {}).reduce((a, b) => a + b, 0);
  const budget = s.daily_budget;
  const overBudget = budget > 0 && spentUsd >= budget;

  const rows = [
    ["Brain", `${s.llm_default === "openai" ? s.openai_model : s.model}`],
    ["Speech-to-text", `${s.stt_provider}${s.stt_provider === "whisper" ? " · " + s.whisper_model : ""}`],
    ["Text-to-speech", s.tts_provider],
    ["Wake word", s.wake_word],
    ["Mic", s.mic_device],
    ["Speaker", s.speaker_device],
    ["Voice verification", s.speaker_verify ? (s.voiceprint_enrolled ? "on, enrolled" : "on, not enrolled yet") : "off"],
    ["Spend today", budget > 0 ? `$${spentUsd.toFixed(3)} / $${budget.toFixed(2)}` : `$${spentUsd.toFixed(3)} (no limit)`, overBudget ? "warn" : "good"],
    ["API calls today", s.spend.calls ?? 0],
  ];
  for (const [label, value, tone] of rows) {
    const c = el("div", "card");
    c.append(el("div", "label", label), el("div", `value${tone ? " " + tone : ""}`, String(value)));
    cards.appendChild(c);
  }
}

// ---- settings ----
function markDirty(input, key, value) {
  changes[key] = value;
  input.classList.add("dirty");
  updateSaveBar();
}

function buildField(f) {
  const row = el("div", "field");
  const info = el("div");
  info.append(el("div", "key", f.key));
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
  return row;
}

async function loadSettings() {
  const res = await fetch("/api/env");
  const { sections } = await res.json();
  const root = $("#settings-sections");
  root.innerHTML = "";
  sections.forEach((section, i) => {
    const wrap = el("div", i === 0 || root.dataset.open === section.title ? "section" : "section collapsed");
    const title = el("div", "section-title");
    title.append(el("span", null, section.title), el("span", "chevron", "▾"));
    title.addEventListener("click", () => { wrap.classList.toggle("collapsed"); if (!wrap.classList.contains("collapsed")) root.dataset.open = section.title; });
    const body = el("div", "section-body");
    for (const f of section.fields) body.appendChild(buildField(f));
    wrap.append(title, body);
    root.appendChild(wrap);
  });
}

function updateSaveBar() {
  const n = Object.keys(changes).length;
  $("#save-hint").textContent = n ? `${n} change${n > 1 ? "s" : ""} pending` : "No changes";
  $("#save-btn").disabled = n === 0;
}

function showBanner(msg, isError) {
  const b = $("#banner");
  b.textContent = msg;
  b.className = `banner${isError ? " error" : ""}`;
}

$("#save-btn").addEventListener("click", async () => {
  const btn = $("#save-btn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/env", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jarvis": "1" },
      body: JSON.stringify(changes),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "save failed");
    for (const key of Object.keys(changes)) delete changes[key];
    updateSaveBar();
    showBanner("Saved. Restart Jarvis (python main.py) for these changes to take effect.");
    if (window.toast) toast("Settings saved");
    await loadSettings();
  } catch (e) {
    showBanner(e.message, true);
    btn.disabled = false;
  }
});

// ---- Settings -> Connections ----
const STATUS_TEXT = { connected: "Connected", expired: "Needs reconnecting", disconnected: "Not connected",
                      not_configured: "Not set up" };

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis": "1" },
                                  body: JSON.stringify(body || {}) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "request failed");
  return data;
}

function connBanner(msg, isError) {
  const b = $("#conn-banner");
  b.textContent = msg;
  b.className = `banner${isError ? " error" : ""}`;
}

async function follow(flowId) {
  connBanner("Finish signing in in the browser window that just opened...");
  for (;;) {
    await new Promise(r => setTimeout(r, 1500));
    const f = await (await fetch(`/api/connections/flows/${flowId}`)).json();
    if (f.state === "pending") continue;
    connBanner(f.message, f.state !== "done");
    await loadConnections();
    return;
  }
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
    add.addEventListener("click", async () => {
      try { await follow((await api(`/api/connections/${p.id}/connect`, { levels: { gmail: ["read"], calendar: ["read"] } })).flow); }
      catch (e) { connBanner(e.message, true); }
    });
    head.append(add);
  }
  card.append(head);
  if (!p.configured) card.append(el("div", "setup", p.setup));

  for (const a of p.accounts) {
    const box = el("div", "account");
    const ah = el("div", "account-head");
    ah.append(el("span", "who", a.account + (a.active ? "  (used by Jarvis)" : "")),
              el("span", `pill ${a.status}`, STATUS_TEXT[a.status] || a.status));
    if (!a.active) {
      const use = el("button", "btn ghost", "Use this account");
      use.addEventListener("click", async () => { await api(`/api/connections/${p.id}/active`, { account: a.account }); loadConnections(); });
      ah.append(use);
    }
    if (a.status === "expired") {
      const re = el("button", "btn", "Reconnect");
      re.addEventListener("click", async () => follow((await api(`/api/connections/${p.id}/reconnect`, { account: a.account })).flow));
      ah.append(re);
    }
    const dis = el("button", "btn danger", "Disconnect");
    dis.addEventListener("click", async () => {
      if (!confirm(`Disconnect ${a.account}? Jarvis loses access and its saved key is deleted.`)) return;
      const r = await api(`/api/connections/${p.id}/disconnect`, { account: a.account });
      connBanner(r.revoked ? "Disconnected, and access was revoked at the provider." :
                 "Disconnected here. The provider couldn't be reached to revoke access: remove it in your account's security settings.", !r.revoked);
      loadConnections();
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
          try {
            const r = await api(`/api/connections/${p.id}/access`, { account: a.account, service: svc.id, level: l.id, on: cb.checked });
            if (r.flow) await follow(r.flow); else loadConnections();
          } catch (e) { connBanner(e.message, true); }
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
  const res = await fetch("/api/connections");
  const { providers } = await res.json();
  const root = $("#providers");
  root.innerHTML = "";
  for (const p of providers) root.appendChild(providerCard(p));
}

loadStatus();
loadSettings();
setInterval(loadStatus, 15000);
