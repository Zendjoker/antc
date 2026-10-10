// Browser-only preference: both appearances share exactly the same controls and APIs.
(() => {
  // v2 key: the previous version saved its "hud" default automatically on first visit, so a stored value under the
  // old key does not reflect a real user choice and must not override the new light default.
  const key = "jarvis.appearance.v2", root = document.documentElement;
  const valid = value => value === "classic" || value === "hud";
  // Classic (light, professional) is the default Normal HUD look. ZEND HUD remains an optional dark appearance,
  // separate from the cinematic "Full Jarvis" mode (presence.js), which stays available via explicit entry.
  let mode = "classic", saved = true;
  try { const value = localStorage.getItem(key); if (valid(value)) mode = value; }
  catch { saved = false; }

  function sync() {
    const select = document.querySelector("#appearance-mode");
    if (select) select.value = mode;
    const shortcut = document.querySelector("#appearance-shortcut");
    if (shortcut) {
      shortcut.textContent = mode === "hud" ? "HUD" : "Classic";
      shortcut.setAttribute("aria-label", `Appearance: ${mode === "hud" ? "ZEND HUD" : "Classic"}. Switch to ${mode === "hud" ? "Classic" : "ZEND HUD"}`);
    }
    const status = document.querySelector("#appearance-status");
    if (status) status.textContent = saved ? "Saved in this browser. Changes apply immediately." : "Applied in this window. Browser storage is unavailable; this preference could not be saved.";
  }
  function apply(value, persist = true) {
    if (!valid(value)) return false;
    mode = value; root.dataset.appearance = mode;
    if (persist) {
      try { localStorage.setItem(key, mode); saved = true; }
      catch { saved = false; }
    }
    sync(); return saved;
  }
  function motionVisibility() {
    root.dataset.motion = document.visibilityState === "hidden" ? "paused" : "active";
  }
  apply(mode); motionVisibility();
  document.addEventListener("visibilitychange", motionVisibility);
  document.addEventListener("DOMContentLoaded", () => {
    sync();
    document.querySelector("#appearance-mode").addEventListener("change", e => apply(e.target.value));
    document.querySelector("#appearance-shortcut").addEventListener("click", () => apply(mode === "hud" ? "classic" : "hud"));
  });
  window.addEventListener("storage", e => {
    if (e.key === key) { saved = true; apply(valid(e.newValue) ? e.newValue : "classic", false); }
  });
})();
