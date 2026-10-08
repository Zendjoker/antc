# Jarvis: browsers, screen and research

**Status:** built and passing automated tests. Only the read-only part has been checked on your real PC (see "Real-world results").

## What was built

| Area | What Jarvis can do |
|---|---|
| Browser awareness | Knows which browsers are installed, which one is in front, which you used last and the system default. Reads the open tab's address, title and tab list. |
| Choosing a browser | Uses the one you name. Otherwise the one in front, then the last used, then the default. "The other browser" works when it's clear. A browser that isn't installed is never swapped for another. |
| Opening | Sites by name ("YouTube", "Gmail") or address. Always a **new tab**, and your tabs stay. "Here" / "in this tab" uses the current tab. |
| Searching | Google, YouTube, Bing, DuckDuckGo, Wikipedia, Amazon, GitHub, Reddit, "this website". Searching the site you're on stays in the same tab. |
| Navigation | Back, forward, refresh, new tab, "switch to my YouTube tab". Never closes tabs. |
| Reading | "Read this page": the actual text of the tab in front, logged-in pages included. That text stays on this PC and goes only to the model answering you. |
| Clicking | "Click the second result", "open the first video", "click Sign in". Elements are found by what they are (accessibility), not by screen position. |
| Typing | Into a field by its label or the search box, checked by reading it back; Enter submits a search. |
| Scrolling | Down, up, top, bottom, checked by the page position. |
| Copying | A link (this page or result N) to the clipboard, checked. |
| Screen | "What am I looking at?", "read this error": one fresh screenshot of the window in front, read by a vision model. |
| Research | "Research / compare / find the docs for…": several searches, reads the pages, compares them and cites `[n]`. A short spoken summary; sources and links on the dashboard. "Open source 2" opens one. |

Every action follows the same rules:

- It goes through the existing action lifecycle (journal, intent checks, claim checks).
- It checks its own effect: the address changed, the field reads back, the tab count grew.
- If nothing visibly changed, it answers **"not confirmed"** and Jarvis won't say it worked.

## Files

**New:**

- `room_agent/computer/`: `browsers.py`, `browser_ops.py`, `uia.py`, `winput.py`, `pages.py`, `research.py`, `screen.py`, `vision.py`, `context.py`
- `room_agent/abilities/computer.py`: the 15 voice tools
- Tests: `tests/test_browser.py`, `test_screen.py`, `test_research.py`, `computer_live.py`

**Changed:**

- `config.py`, `.env.example`, `.gitignore`
- `actions/core.py`: module list
- `abilities/apps.py`, `abilities/info.py`: removed the old "not built" lines
- `control.py`, `UI/index.html`, `UI/jarvis.js`, `UI/style.css`: Research card and browser row
- `tests/__init__.py`, `tests/__main__.py`

**No new dependencies.** It uses Windows UI Automation through `comtypes`, plus `lxml`, `requests`, `numpy` and `ddgs`, all already installed.

## Browser compatibility

| Browser | On this PC | Status |
|---|---|---|
| Opera | Yes (default) | Address, tabs and page text read live. Opening and clicking are tested offline only. |
| Chrome, Edge, Brave | Yes | Same engine as Opera; opening and clicking are tested offline only. |
| Opera GX | No | Detected by install path; untested. |
| Firefox | No | Opens with `-new-tab`; its accessibility tree differs, so untested. |

## Built vs planned

| Planned | Status |
|---|---|
| Parts 1–3, 6–8 | Done |
| Part 4 (screen) | Done |
| Part 5: clicking and typing via accessibility | Done |
| Part 5: vision-guided clicking | Last resort only, after "look at my screen". It refuses old screenshots, moved windows and buy/delete buttons. Its effect is **not** verified. |
| Generic "press keyboard shortcuts" | Not built: an unverifiable key press could only be reported as "sent". Navigation keys are built in, with checks. |
| Browser automation sessions (Playwright etc.) | Not used: they would control a separate browser, not your Opera. |

## Tests (automated, offline)

| Suite | Checks | Covers |
|---|---|---|
| `test_browser` | 54 | Scenarios A–D, G, J, browser choice, addresses, tabs, clicking, typing, scrolling, stale page, interruptions, failures, page text can't trigger actions |
| `test_screen` | 30 | Scenario F, PNG and scaling, coordinates, stale screenshots, private windows, permission, only-when-asked |
| `test_research` | 22 | Scenarios H and I, real page fetching from a local server, citations match the pages, failures reported, cancellation, dashboard report |

**Full safe suite: 38 of 38 suites passed** (including all earlier ones). The details are in `tests/report.json`.

**Real-world results** (read-only, your Opera):

- Address read: 125 ms
- Page text: 36 ms
- 11 tabs listed: 44 ms

**Not run yet:** the acting part (`--hardware`), and a real vision call.

## Costs

The automated tests make no API calls. Estimates (not measured):

- **One "look at my screen":** about $0.002–0.004 with Claude Haiku 4.5.
- **Deep research:** about 4,000 extra input tokens on the normal model, roughly $0.001 on gpt-5-mini. Searching itself is free (DuckDuckGo).
- **Opening, searching and clicking:** no AI calls when said simply ("open YouTube").

## Privacy and safety

- **Screenshots:**
  - Only when you ask about the screen in that turn.
  - Asked once first (`SCREEN_VISION=ask`); `off` or `allow` change that.
  - Only the window in front; never stored.
  - Refused for password managers, sign-in, banking and payment pages, and private messages, and when a password field has the focus.
  - Off entirely with `SCREEN_VISION=off`.
- **Never:**
  - typing into password, code or card fields
  - clicking CAPTCHAs
  - closing tabs
  - changing the default browser
- **Buy, pay, send, post or delete buttons:** need a separate confirmed click; Jarvis asks you first.
- **Words from a web page can't make Jarvis act:** clicking, typing and opening need your own words in that turn, otherwise Jarvis asks.
- **Research reads only public addresses:** never this PC or your home network.
- **Browsing context:** kept in memory only, expires after 15 minutes, and is never long-term memory.
- **Dashboard:** keeps only the last 5 research reports (`research.json`, git-ignored).

## Limitations

- **"Last used browser"** is only what Jarvis saw this session, not Windows history.
- **A page's first read can take up to 2.5 s.** Chromium builds the page's accessibility tree on first request.
- **Scrolling may report "not confirmed"** on pages that don't report their scroll position.
- **"Second result" is precise on Google and YouTube.** Other sites use a general rule.
- **Research can't read pages** that need JavaScript or a login. Those are listed as "couldn't read".

## Live testing

1. Restart Jarvis so it loads the new tools:
   ```
   .\.venv\Scripts\python.exe main.py
   ```
2. Optional: run the real-browser test. It opens one example.com tab, clicks its link and goes back:
   ```
   .\.venv\Scripts\python.exe -m tests.computer_live --act
   ```
3. With Opera in front, say each of these and check the result:

| Say | Expect |
|---|---|
| "Open YouTube" | New Opera tab with YouTube, "Opened YouTube in Opera." |
| "Open YouTube in Chrome" | Chrome opens it |
| "Search for AI assistants" | YouTube results, same tab |
| "Open the second one" | The second video opens |
| "Go back" | Back to the results |
| "Search Google for Python tutorials" | Google results |
| "Read this page" | A short summary of the real page |
| "Switch to my YouTube tab" | That tab comes to the front |
| "Scroll down" | The page moves (or Jarvis says it couldn't confirm) |
| "What am I looking at?" | Asks permission once; say yes; then describes the window |
| "Research the best speech recognition models" | A short spoken comparison; sources on the dashboard (Research card) |
| "Open source 2" | That source opens |
| Talk over Jarvis during research | It stops and says so |

If something fails, add `DIAGNOSTICS=1` to `.env` and check the `tool` lines in `logs\diagnostics-*.jsonl` (see LIVE_VALIDATION.md).
