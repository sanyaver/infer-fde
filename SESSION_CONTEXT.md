# Session Context — Insurance Document Puller (Infer FDE Take-Home)

## What this project is
FastAPI + Playwright/patchright backend, React frontend. Pulls policy docs from carrier portals. Supported: **Geico** + **Allstate** (two working carriers = assignment requirement met).

Stack: FastAPI, patchright (patched Playwright), Redis, React/Vite, Docker.

## How to run locally (for demo/Loom)

```bash
# Terminal 1 — Redis
docker run --rm -p 6379:6379 redis:7-alpine

# Terminal 2 — Backend
cd backend
source .venv/bin/activate
HEADLESS=false BROWSER_CHANNEL=chrome ALLSTATE_CDP_URL=http://127.0.0.1:9222 uvicorn main:app --reload --port 8000

# Terminal 3 — Frontend
cd frontend && npm run dev
# → http://localhost:5173
```

## Allstate — status: WORKING ✅

**Anti-bot:** Akamai Bot Manager Advanced. Fingerprint-binds `_abck` cookie to the browser.

**How it works:** CDP mode. User starts Chrome with `--remote-debugging-port=9222`, logs in manually at https://myaccountrwd.allstate.com, then backend connects via CDP and scrapes docs. Akamai never re-challenges because the sensor ran during the human login.

**Start Chrome for Allstate:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/allstate-chrome
```

**Document flow:** `#NavItemPolicies` → `.btn--header.btn--policy[aria-label*="Documents"]` → `a[aria-label*="directed to a new page with pdf"]` links → click each → wait for `waitingToLoad.html` redirect to real PDF URL → parallel tab capture → `page.evaluate(fetch(url))` to download bytes.

**Session:** No session file. CDP connection reuses logged-in Chrome.

**Known working:** 5 docs fetched (Community Service Statement, Credit Card Auth, Privacy Statement, New Business Application x2). Some were `waitingToLoad.html` intermediate pages — code now clicks and waits for real redirect.

## Geico — status: WORKING with session file ✅ / fresh login blocked by PerimeterX ⚠️

**Anti-bot:** PerimeterX. Fingerprint-binds cookies to the browser that passed the challenge.

**Session file approach (reliable):**
- `geico_session.json` stores full auth state (cookies + localStorage).
- Login code restores cookies, goes to `portfolio.geico.com/dashboard`, if it loads → skip login entirely, fetch docs directly.
- **DO NOT delete `geico_session.json`** unless you want to redo MFA.
- Session lasts days; device trust lasts weeks/months.

**Fresh login (requires real Chrome + headed):**
- `HEADLESS=false BROWSER_CHANNEL=chrome` gives best chance at passing PerimeterX.
- `_pass_px_challenge()` runs mouse movement while waiting for Flutter to render.
- If Chrome is blocked (white screen): IP may be rate-limited; wait 10-15 min or change network.
- Session is auto-saved after successful MFA — won't need MFA again after.

**To seed session manually (one-time):**
```bash
cd backend && source .venv/bin/activate
python save_geico_session.py
# → headed Chrome opens, log in + complete MFA + check "Trust this browser" → session saved
```

**MFA flow (submit_mfa):**
1. Checks for "Trust this browser" checkbox FIRST (appears before portfolio redirect)
2. Clicks + confirms trust
3. Waits for portfolio.geico.com redirect
4. Saves full storage state to `geico_session.json`

**Document flow:** Goes to `portfolio.geico.com/dashboard` → tries "Proof of Insurance" tile → fallback to "Documents" nav → `page.on("request")` listener + `context.on("page")` new-tab capture → builds doc list from captured URLs.

**Selectors (Flutter flt-semantics):**
- Username: `input[autocomplete="email"][data-semantics-role="text-field"]` + fallbacks
- Password: `input[type="password"][data-semantics-role="text-field"]`
- Login btn: `flt-semantics[flt-semantics-identifier="null_Button_Default_Title"]` + fallbacks
- MFA input: `input[autocomplete="one-time-code"]`
- Trust checkbox: `flt-semantics[flt-semantics-identifier="trustBrowser_Checkboxes_true_CheckboxTitle"]`

## Key files
```
backend/
  main.py                   — FastAPI app, _run_login background task
  session_manager.py        — Redis session + document storage
  stealth.py                — Browser pool, proxy config, CDP connect helpers
  save_allstate_session.py  — Seeds Allstate Chrome profile (run once)
  save_geico_session.py     — Seeds Geico session file (run once, do MFA)
  carriers/
    allstate.py             — Akamai/CDP approach
    geico.py                — PerimeterX/session-file approach
    base.py                 — BaseCarrier, Document dataclass
frontend/
  src/App.jsx               — Full UI: form → polling → MFA → PDF viewer
```

## Architecture
- Background task per session: `_run_login()` in main.py handles full flow
- MFA: Redis-based wait (`session_manager.wait_for_mfa`, 120s timeout), frontend polls `/session-status`
- Documents stored in Redis as bytes, served via `/document/{session_id}/{doc_id}`
- Browser pool: 2 pre-warmed contexts for non-Allstate carriers
- Allstate always uses persistent profile or CDP (never pool)

## What the evaluators want to hear (Loom talking points)
1. **Two carriers end-to-end** — Geico (PerimeterX, session reuse) + Allstate (Akamai, CDP)
2. **Anti-bot** — PerimeterX: session persistence + real Chrome. Akamai: fingerprint-bound persistent profile / CDP post-auth attach
3. **Not just your Chrome** — Persistent profile strips session cookies, keeps only Akamai fingerprint. Any user's credentials work
4. **Latency** — Allstate: parallel tab capture (all docs opened simultaneously). Geico: session restore path is fast (no login page)
5. **Session reuse** — Geico: session file skips login entirely on repeat runs. Allstate: CDP Chrome stays open

## Current known issues / things NOT to do
- Do NOT delete `geico_session.json` before demo
- Do NOT unset `ALLSTATE_CDP_URL` — Allstate fails without CDP
- Fresh Geico login may show white screen if PerimeterX blocks patchright; solution is `BROWSER_CHANNEL=chrome HEADLESS=false` or wait and retry
- Docker + headed Chrome = needs display (VNC/X11). For demo, run locally not Docker
