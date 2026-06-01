# Insurance Document Puller

Pull policy documents from carrier portals via a browser-automation backend.  
Supported carriers: **Geico**, **Allstate**, **Progressive**, **Liberty Mutual**.

---

## Quick start (Docker)

```bash
cp .env.example .env
# Optional but strongly recommended: set PROXY_URL to a residential proxy
docker-compose up --build
```

- Frontend: http://localhost:3000  
- Backend API: http://localhost:8000  
- Docs (Swagger): http://localhost:8000/docs

### Local dev (no Docker)

**One-time setup** (installs venv, deps, and browser binaries):

```bash
./setup.sh
```

Then seed carrier sessions once (opens a real browser window for you to log in):

```bash
cd backend && source .venv/bin/activate
python save_geico_session.py    # follow prompts, complete MFA + trust device
python save_allstate_session.py # follow prompts, log in
```

**Start the backend**

```bash
# Start Redis separately: docker run -p 6379:6379 redis:7-alpine
cd backend && source .venv/bin/activate
uvicorn main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173 — proxies /api → localhost:8000
```

---

## Flow

```
Browser           Frontend             Backend (FastAPI)        Carrier Portal
  │                  │                       │                        │
  │  pick carrier    │                       │                        │
  │  + credentials ──▶ POST /start-session ──▶ background task ───────▶ goto login URL
  │                  │                       │                        │ fill creds
  │                  │◀── { session_id } ────│                        │ submit
  │  poll every 1s ──▶ GET /session-status   │                        │
  │                  │        ...            │  mfa_required ◀────────│ MFA page
  │  MFA prompt ◀────│                       │                        │
  │  enter code ─────▶ POST /submit-mfa ─────▶ wait_for_mfa() wakes  │
  │                  │                       │ submit code ───────────▶
  │                  │  fetching_docs        │◀────── logged in ───────│
  │                  │        ...            │ get_documents() ────────▶
  │                  │        ...            │◀── PDF URLs/downloads ──│
  │                  │  completed ◀──────────│ store in Redis          │
  │  document list ◀─│                       │                        │
  │  click View PDF  │                       │                        │
  │◀── GET /document/{sid}/{doc_id} ─────────│ stream from Redis       │
```

End-to-end latency target: **< 8 seconds** from MFA submit to first PDF on screen.

---

## Filling in carrier selectors

Carrier scrapers are in `backend/carriers/`. Each file has `# TODO` comments marking the selectors to fill in once you have real credentials.

**Workflow:**

```bash
# 1. Set HEADLESS=false SLOW_MO=500 in .env so you can watch the browser
# 2. Run the backend locally
# 3. Submit credentials via the UI
# 4. Open DevTools in the launched Chrome window
# 5. Inspect the login form / MFA page / documents page
# 6. Right-click element → Copy → Copy selector
# 7. Paste into the relevant carrier file
```

Key methods to fill per carrier:

| Method | What to provide |
|--------|----------------|
| `login()` | Selectors for username, password, submit button; selector to detect MFA page vs success |
| `submit_mfa()` | Selector for code input, submit button, success indicator |
| `get_documents()` | Selectors for document rows, name, and download link |

---

## Allstate — Akamai Bot Manager

Allstate runs **Akamai Bot Manager Advanced**, which binds its `_abck` cookie to a specific browser fingerprint (Canvas hash, WebGL, device IDs, mouse-movement timing). Any fresh Chromium context has a different fingerprint — Akamai rejects the login even if JS stealth patches are applied.

### How this app handles it

Two modes, controlled by environment variables:

**Mode A — Persistent Chrome profile (recommended for production)**

```bash
# Run once on any machine with Chrome installed to seed the session
cd backend && python save_allstate_session.py
# → logs in manually, saves profile to ~/.allstate-chrome-profile

# Then start the backend normally — automation reuses the same profile
uvicorn main:app --reload
```

The profile directory contains Akamai's approved fingerprint. The automation reopens Chrome with that same `user-data-dir`, so Akamai sees the identical browser it approved during the seeding step. This is *not* "ship with my personal cookies" — the seeding script works from scratch on any machine and can run on a VPS via VNC or a headful Docker container.

On each login request the backend strips the Allstate session cookies (so it always authenticates with the credentials the user submitted) but preserves the Akamai `_abck` and `bm_sz` fingerprint cookies. This means any user can enter their own Allstate credentials in the UI and get their own documents — they all share the same pre-approved browser fingerprint.

**Mode B — CDP (demo / dev)**

```bash
# 1. Launch Chrome with remote debugging enabled (any machine)
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/allstate-chrome

# 2. Log in at https://myaccountrwd.allstate.com in that Chrome window

# 3. Start backend pointing to it
ALLSTATE_CDP_URL=http://127.0.0.1:9222 uvicorn main:app --reload
```

The backend connects over CDP *after* login — Akamai's sensor already ran and set the cookie before automation touched the browser, so there's nothing to spoof.

### Why not just use a residential proxy?

A residential IP helps with Akamai's IP-reputation layer but not the fingerprint-binding layer. The `_abck` cookie ties to the specific browser that passed the sensor challenge. A new Playwright context on the same residential IP still fails because its Canvas/WebGL fingerprint differs. The persistent profile approach sidesteps the sensor entirely.

---

## Anti-bot strategy

### What we implement

| Layer | Technique | Addresses |
|-------|-----------|-----------|
| **JS stealth** | `playwright-stealth` — patches `navigator.webdriver`, Chrome runtime, plugins enumeration, permissions API, language/timezone spoofing (~20 patches) | PerimeterX, basic Akamai bot scoring |
| **Residential proxy** | Configurable via `PROXY_URL` — routes all browser traffic through a residential IP | Akamai IP reputation, ThreatMetrix network fingerprint |
| **Human-like typing** | `page.type()` with 40–120ms per-keystroke delay + random pre-action jitter | Behavioral heuristics in Akamai BM and ThreatMetrix |
| **Realistic browser context** | Real user-agent, viewport, locale, timezone; accept-language header matching proxy geo | Basic fingerprint checks |
| **Browser pool warmup** | Pre-launches 2 browser contexts on startup | Cuts ~2s from first-request latency |
| **Session reuse** | Completed contexts are cleared and returned to pool (not closed) | Reduces cold-start penalty for repeat runs |

### What we don't fully solve (and why)

**TLS/JA3 fingerprinting** — Akamai Advanced Bot Protection and some PerimeterX deployments fingerprint the TLS ClientHello (JA3/JA4 hash). Bundled Chromium produces a slightly different TLS hello than retail Chrome. Fix: launch with `channel="chrome"` to use the system-installed Chrome binary, or use a JA3-spoofing proxy (Mitmproxy + custom extension). Not implemented here because it requires a specific Chrome version installed on the server.

**Canvas/WebGL entropy** — Some ThreatMetrix deployments capture canvas fingerprints. `playwright-stealth` patches the Canvas API but not WebGL RENDERER strings in all cases. Fix: run on a real GPU VM headfully. Not practical in a container.

**Cookie trust / IP history** — If a carrier has never seen this IP + device combination, it may enforce a step-up auth (CAPTCHA, knowledge-based auth). A residential proxy with sticky sessions mitigates this but doesn't eliminate it for fresh IPs.

**CAPTCHA** — If a carrier presents reCAPTCHA or hCaptcha, the current implementation will stall. Fix: integrate a CAPTCHA solving service (2captcha, CapSolver) or add manual-challenge support in the UI.

### Residential proxy recommendation

Get a proxy from **BrightData** (residential, rotating), **Smartproxy**, or **Oxylabs**. Set:

```
PROXY_URL=http://username:password@gate.smartproxy.com:10000
```

Use sticky sessions (same IP per session) to avoid IP changes mid-login triggering fraud alerts.

---

## Session persistence

### Geico — MFA once, then never again

After completing MFA the backend automatically saves the full browser storage state (cookies + localStorage) to `backend/geico_session.json`. On every subsequent run it restores those cookies first; if Geico's dashboard loads, login succeeds without MFA. The "Trust this browser" checkbox is always checked during MFA so Geico marks the device as trusted. The session file is also refreshed after each successful cookie-restore so rotated cookies extend the effective expiry.

To manually seed (e.g. first deploy to a new server):

```bash
cd backend && python save_geico_session.py
# Follow prompts → complete MFA + trust device → session saved
```

### App sessions (Redis)

Sessions are stored in Redis with a 1-hour TTL. If a user re-runs the same carrier:

- The old session expires naturally.
- A new browser context is acquired from the pool.
- The carrier sees a fresh login — no cookie re-use between runs (intentional: avoids stale session errors).

For true session reuse (skip login on second run), you could serialize `context.storage_state()` to Redis and restore it — not implemented here because carrier sessions typically expire within hours and this feature has diminishing returns vs. the added complexity.

---

## Production deployment notes

- **Workers**: `uvicorn ... --workers 1` is intentional. Active browser sessions live in-memory; multi-worker requires an external browser microservice. Scale horizontally by running multiple single-worker containers behind a load balancer with sticky sessions.
- **Memory**: Each Chromium instance uses ~200–400MB. Size your host accordingly (2 pool browsers + active sessions ≈ 1GB headroom).
- **Secrets**: Never commit `.env`. Inject `PROXY_URL` via your cloud provider's secret manager at deploy time.
- **Timeout**: The nginx `proxy_read_timeout 180s` covers 120s MFA wait + buffer. Adjust if you change `wait_for_mfa` timeout.

---

## Project structure

```
backend/
  main.py              — FastAPI app, background login task, routes
  session_manager.py   — Redis session + document storage
  stealth.py           — Browser stealth, proxy config, pool warmup
  carriers/
    base.py            — Abstract BaseCarrier with shared helpers
    geico.py           — Geico scraper (PerimeterX; optional session reuse)
    allstate.py        — Allstate scraper (Akamai; persistent profile or CDP mode)
    progressive.py     — Progressive scraper
    liberty_mutual.py  — Liberty Mutual scraper

frontend/
  src/App.jsx          — Single-file React app: form → status polling → MFA → PDF viewer
  vite.config.js       — Dev proxy /api → localhost:8000
  nginx.conf           — Prod proxy /api → backend:8000

docker-compose.yml     — redis + backend + frontend
.env.example           — Environment variable reference
```

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/carriers` | List supported carriers |
| POST | `/start-session` | Begin login flow → `{ session_id }` |
| GET | `/session-status/{id}` | Poll session state |
| POST | `/submit-mfa` | Submit MFA code |
| GET | `/documents/{id}` | Document list (after completed) |
| GET | `/document/{session_id}/{doc_id}` | Stream PDF bytes |
| GET | `/health` | Healthcheck |
