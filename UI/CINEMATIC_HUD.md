# Asymmetric Jarvis HUD

Frontend work is isolated in the cinematic-composition worktree. No shared Python modules, security, audio behavior, or backend interfaces were changed. No commits or pushes.

## Implemented

The reviewed static geometry in hud-static.js is shared by the static study and application renderer. It contains the foreground cutaway, asymmetric reactor aperture, weighted segmented bands, diagnostic housings, tick fans, and orbital satellite. It does not perform requests or animate independently.

cinematic.js preserves the existing JarvisHUD update/enable/menu contract. A single requestAnimationFrame clock counter-rotates three selected mechanical assemblies and the scanning sweep; foreground panels and readout typography remain stationary. Listening and speaking have separate CSS opacity pulses. These are state-driven effects, not audio amplitude measurements. The backend idle_check state maps to speaking. Idle, thinking, executing, listening, speaking, quiet, interrupted, disconnected, and unavailable labels use the existing snapshot. Task progress uses only reported steps.

Offline, disconnected, connecting, quiet, error, interrupted, reduced-motion and hidden-page states pause the motion clock. Exiting restores moved controls. Existing conversation, tasks, devices, system and settings controls are reused without duplicate IDs or handlers. Voice selection remains in the original form. A full-mode chat submission stays in the HUD; its confirmed reply yields to a newer backend conversation snapshot. Failed and uncertain submissions retain the existing draft/retry handling.

## Offline verification

35 focused tests pass:
node --test tests/test_dashboard_frontend.cjs tests/test_dashboard_appearance.cjs tests/test_cinematic_hud.cjs tests/test_hud_static.cjs

Browser checks cover task and settings panels, active voice confirmation with persistence unconfirmed, full-mode chat replies, and resizing. Screenshots are stored in the chat visualization directory.

For offline browser QA, run tests/dashboard_preview.cjs with JARVIS_PREVIEW_PORT=8768. This helper serves synthetic fixtures only; no Jarvis process, integrations, paid services or hardware are launched. Port 8768 defaults to the animated full-mode speaking fixture and marks it OFFLINE PREVIEW / SYNTHETIC DATA / NO LIVE ACTIONS. The original static study remains at /hud-static.html. Production index.html uses the existing backend routes when served by the normal Jarvis dashboard server.

## Remaining verification and backend coordination

The shared live dashboard on port 8765 has not been replaced with this worktree's frontend. Promotion and a live smoke test remain outstanding; offline verification does not establish that real speech, integrations or runtime performance are perfect.

Current snapshots provide voice state but no timestamped microphone/playback amplitude stream. Actual audio reactivity requires a backend-owned read-only envelope stream containing normalized level, source, session ID, playback-relative timestamps and stale/disconnected semantics. Voice persistence also needs a backend-confirmed persisted provider/voice ID; the UI currently reports persistence as unconfirmed. Coordinate either addition with Claude Code; neither contract was changed here.

## Files changed in the animation/integration pass

UI/index.html, UI/jarvis.js, UI/cinematic.js, UI/cinematic.css, UI/hud-static.js, UI/CINEMATIC_HUD.md.
Tests: tests/dashboard_preview.cjs, tests/test_cinematic_hud.cjs, tests/test_dashboard_frontend.cjs, tests/test_hud_static.cjs.

Browser sizing checks: 1440x900, 1280x800, 1024x768, 768x1024, 390x844, 1280x720 and 860x720. No document-level horizontal overflow was observed. Desktop and mobile screenshots were reviewed; micro-instrument typography is intentionally decorative and not a source of runtime data. Status and actionable controls use separate readable text. All seven reliability regression groups still pass. Browser console had no error entries. Full-mode chat, task/settings panels, active voice selection and unavailable-state control disabling were exercised using synthetic fixtures only.

## Repair pass (navigation, replies, default appearance)

Following user review, three concrete defects were fixed, offline-tested, and re-verified in a real browser (desktop 1440x900 and mobile 390x844):

1. **Sidebar hidden on Home in HUD appearance.** `presence.css` hid `.sidebar` on the Home route whenever `data-appearance="hud"`, trapping users on Home with no way to reach Memory/Activity/Missions/Status/Connections/Settings except the (itself unreachable at the time) hamburger button. Removed the Home-specific hide rules; the Full-Jarvis-mode sidebar hide rule (`html.jarvis-full .sidebar`) is untouched, since that overlay is explicit and has its own exit control.
2. **Chat replies vanishing.** `jarvis.js`'s chat feed rendered only from `live.conversation` (backend-polled). A confirmed reply with no superseding poll yet (always true against the offline fixture, and possible as a live-backend race) disappeared. Added a client-side fallback render branch in `renderFeed()` using the existing `latestCommandReply`/new `lastSentText` state so the just-completed exchange stays visible until real conversation data supersedes it. No backend contract changed.
3. **Default appearance reverted to Classic (light).** The Normal HUD defaulted to the dark `hud` appearance, which read as a generic neon/cyberpunk skin rather than a professional product surface, and its own navbar treatment (uppercase mono labels, glow, grid background) was the chief visual complaint. `appearance.js`'s default (and its invalid-value/cross-window fallback) now start from `classic` — the existing light, Notion/Linear-style theme in `style.css` (white backgrounds, restrained neutral palette, clear active-nav state) — while `JARVIS HUD` remains available as an optional dark appearance from Settings, independent of the separate cinematic "Full Jarvis" overlay. `tests/test_dashboard_appearance.cjs`'s default-appearance assertions were updated to match the new default; no other test encoded the old default.
4. **`tests/dashboard_preview.cjs` forced-redirect removed.** The fixture server previously 302-redirected `/` into `?view=jarvis&state=speaking` whenever run on port 8768, i.e. every ordinary visit landed in Full Jarvis. This only ever existed in the test fixture (confirmed `UI/server.py` has no such redirect); removed so the preview's Home route always loads the Normal HUD, matching production behavior. Full Jarvis is reached only via the explicit in-page control or an explicit `?view=jarvis` deep link.

All 35 existing offline tests continue to pass after each change (`node --test tests/test_dashboard_frontend.cjs tests/test_dashboard_appearance.cjs tests/test_cinematic_hud.cjs tests/test_hud_static.cjs`).

## Zend brand pass and live promotion

- `UI/brand.css` (loaded after `workspace.css`) applies the Zend palette (`--zend-*` tokens) and logo (`UI/brand/zend-logo.png`, `zend-favicon.png`, cropped from the supplied transparent PNG) to the light Classic appearance: white sidebar with a gradient active-page indicator, white assistant console with a gradient top edge, gradient orb and an explicit "Enter full Jarvis" pill. Offline status is shown in neutral text, never in brand colors.
- Full Jarvis previously centered its reactor only under the dark appearance; from Classic it was squeezed into a 190px grid column. `brand.css` now gives `html.jarvis-full` its own layout and dark tokens, independent of the appearance setting.
- The appearance preference moved to the `jarvis.appearance.v2` storage key: the old script saved its `hud` default automatically on every first visit, so values under the old key were not real user choices.
- Port 8765 is the live dashboard served by `UI/server.py` from the main checkout, not this worktree. The frontend files listed above (never `server.py`) were copied into the main checkout after a backup in the Copilot session folder; Flask serves them with `no-cache`, so no restart is needed.

36 offline tests pass in both the worktree and the main checkout.

## ZEND redesign pass

- Visible product name changed from Jarvis to ZEND (UI text only). Code identifiers (`JarvisHUD`, `#jarvis-*`, the `X-Jarvis` request header) are unchanged, and "Say “Hey Jarvis”" stays because the configured wake word is still `hey_jarvis`; renaming the spoken wake word requires a trained wake-word model, which does not exist yet.
- `brand.css` is now the full light design system: floating sidebar with a solid blue active item, sticky top bar, one 1200px content column, four equal snapshot tiles and four equal action tiles, equal-height workspace rows, and restyled Status, Activity (timeline rail), Missions, Settings and Connections. No yellow (warnings use rose), no gradient text.
- The core is now a glass sphere of water: two rotating wave layers whose speed and level follow the real state (`data-state` on the orb, `data-phase` on `.hero-art`); ripples only while listening or speaking; gray and still when offline. No audio amplitude is implied.
- Status renders a health summary from `/api/status` facts only (over the daily limit, voice verification on without enrollment, no microphone configured), then grouped setup tiles and a spend meter.
- The overview workspace is open by default; the duplicate Controls/Conversation/Settings bar is hidden outside Full ZEND (the sidebar reaches every page).

## Worktree Git status

The status below includes inherited uncommitted frontend work, which was preserved:
 M UI/app.js
 M UI/index.html
 M UI/jarvis.js
?? UI/CINEMATIC_HUD.md
?? UI/appearance.js
?? UI/cinematic.css
?? UI/cinematic.js
?? UI/hud-static.css
?? UI/hud-static.html
?? UI/hud-static.js
?? UI/hud.css
?? UI/presence.css
?? UI/presence.js
?? UI/workspace.css
?? tests/dashboard_preview.cjs
?? tests/hud_static_preview.cjs
?? tests/test_cinematic_hud.cjs
?? tests/test_dashboard_appearance.cjs
?? tests/test_dashboard_frontend.cjs
?? tests/test_hud_static.cjs
