"""
Anti-bot / stealth configuration for Playwright.

Threat model:
  Most carrier portals run Akamai Bot Manager, PerimeterX, or ThreatMetrix.
  These fingerprint: navigator.webdriver, Chrome runtime flags, canvas/WebGL entropy,
  TCP stack (via server-side analysis), and IP reputation.

What we do:
  1. playwright-stealth  — patches ~20 JS leaks (webdriver flag, chrome runtime,
     permissions API, plugin enumeration, language/platform spoofing, etc.)
  2. Residential proxy   — most critical mitigation. Datacenter IPs are instantly
     flagged by Akamai regardless of JS stealth. Set PROXY_URL to a rotating
     residential proxy (BrightData, Oxylabs, Smartproxy, etc.).
  3. Headful-looking args — strip automation-specific Chrome launch flags.
  4. Realistic viewport / locale / timezone matching the proxy's geo.

Tradeoffs:
  - playwright-stealth is not a silver bullet against TLS fingerprinting (JA3/JA4).
    Carriers that fingerprint TLS (Akamai Advanced) will still see Chromium's TLS
    hello, which is different from a retail Chrome build. Using a real Chrome binary
    (`channel="chrome"`) instead of bundled Chromium helps here.
  - Residential proxies add 200-400ms per request but are required for production.
  - Headless Chromium leaks via certain GPU/Canvas APIs even with stealth patches.
    Running on a VM with a real GPU and headful mode (HEADLESS=false) eliminates this.

Environment variables:
  PROXY_URL    - Rotating residential proxy, e.g. http://user:pass@gate.example.com:7777
  HEADLESS     - "false" to run headed (useful for debugging, required for some carriers)
  SLOW_MO      - Milliseconds between Playwright actions (default 0)
"""

import asyncio
import os
import random
from typing import Optional

from patchright.async_api import Browser, BrowserContext, Page, Playwright

try:
    from playwright_stealth import stealth_async
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

PROXY_URL: Optional[str] = os.getenv("PROXY_URL")
HEADLESS: bool = os.getenv("HEADLESS", "true").lower() != "false"
SLOW_MO: int = int(os.getenv("SLOW_MO", "0"))
BROWSER_CHANNEL: Optional[str] = os.getenv("BROWSER_CHANNEL") or None  # "chrome" to use real Chrome

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 800},
]

import platform as _platform

_IS_LINUX = _platform.system() == "Linux"

# Linux/Docker needs sandbox disabled; on macOS these flags crash the renderer.
_LINUX_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--no-zygote",
]

# Minimal safe args for both platforms
_BASE_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--disable-extensions",
    "--mute-audio",
]

LAUNCH_ARGS = _BASE_ARGS + (_LINUX_ARGS if _IS_LINUX else [])


def _proxy_config() -> Optional[dict]:
    if not PROXY_URL:
        return None
    from urllib.parse import urlparse
    p = urlparse(PROXY_URL)
    config: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        config["username"] = p.username   # urlparse auto-decodes percent-encoding
    if p.password:
        config["password"] = p.password
    return config


async def new_stealth_context(pw: Playwright) -> tuple[Browser, BrowserContext]:
    proxy = _proxy_config()
    ua = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)

    browser = await pw.chromium.launch(
        headless=HEADLESS,
        args=LAUNCH_ARGS,
        proxy=proxy,
        slow_mo=SLOW_MO,
        channel=BROWSER_CHANNEL,
    )
    context = await browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        },
        accept_downloads=True,
    )
    return browser, context


SKIP_STEALTH = os.getenv("SKIP_STEALTH", "false").lower() == "true"

async def apply_stealth(page: Page) -> None:
    if SKIP_STEALTH:
        return
    # playwright-stealth can crash the renderer on some Playwright/Chromium versions.
    # Wrap in try/except so a stealth failure never kills the session.
    try:
        if _STEALTH_AVAILABLE:
            await stealth_async(page)
        else:
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)
    except Exception:
        pass


class BrowserPool:
    """
    Pre-warms a small pool of browser contexts so the first login request
    doesn't pay the ~2s cold-start penalty of launching Chromium.
    """

    def __init__(self, size: int = 2):
        self.size = size
        self._pool: list[tuple[Browser, BrowserContext]] = []
        self._lock = asyncio.Lock()
        self._pw: Optional[Playwright] = None

    def set_playwright(self, pw: Playwright) -> None:
        self._pw = pw

    async def warmup(self) -> None:
        for _ in range(self.size):
            browser, context = await new_stealth_context(self._pw)
            async with self._lock:
                self._pool.append((browser, context))

    async def acquire(self) -> tuple[Browser, BrowserContext]:
        async with self._lock:
            if self._pool:
                return self._pool.pop()
        return await new_stealth_context(self._pw)

    async def release(self, browser: Browser, context: BrowserContext) -> None:
        try:
            await context.clear_cookies()
            async with self._lock:
                if len(self._pool) < self.size:
                    self._pool.append((browser, context))
                    return
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass

    async def close_all(self) -> None:
        async with self._lock:
            for browser, _ in self._pool:
                try:
                    await browser.close()
                except Exception:
                    pass
            self._pool.clear()


browser_pool = BrowserPool(size=2)

ALLSTATE_PROFILE_DIR: str = os.getenv(
    "ALLSTATE_PROFILE_DIR", os.path.expanduser("~/.allstate-chrome-profile")
)
GEICO_PROFILE_DIR: str = os.getenv(
    "GEICO_PROFILE_DIR", os.path.expanduser("~/.geico-chrome-profile")
)

# Set this to http://localhost:9222 when running Chrome with --remote-debugging-port=9222
ALLSTATE_CDP_URL: Optional[str] = os.getenv("ALLSTATE_CDP_URL") or None
GEICO_CDP_URL: Optional[str] = os.getenv("GEICO_CDP_URL") or None


async def connect_cdp(pw: Playwright, cdp_url: str) -> tuple[Browser, BrowserContext]:
    """Connect to a user-started Chrome via CDP. Works for any carrier."""
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = browser.contexts[0]
    return browser, context


async def connect_allstate_cdp(pw: Playwright) -> tuple[Browser, BrowserContext]:
    """
    Connect to a Chrome that the user started manually with --remote-debugging-port.

    Why this works against Akamai:
    - Chrome was started by a human (no automation flags, no CDP at launch time)
    - Akamai's sensor script ran and set _abck during the human login
    - Playwright connects AFTER login — no sensor re-evaluation triggered
    - We only use click-based navigation after connecting, so no page.goto reload
    """
    if not ALLSTATE_CDP_URL:
        raise RuntimeError("ALLSTATE_CDP_URL not set")
    browser = await pw.chromium.connect_over_cdp(ALLSTATE_CDP_URL)
    context = browser.contexts[0]
    return browser, context


async def new_persistent_context(pw: Playwright, profile_dir: str) -> BrowserContext:
    """
    Launch Chrome with a persistent user-data-dir.
    Fallback when CDP connection is not available.
    """
    import os as _os
    _os.makedirs(profile_dir, exist_ok=True)
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=HEADLESS,
        channel="chrome",
        args=LAUNCH_ARGS,
        slow_mo=SLOW_MO,
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        },
        accept_downloads=True,
    )
    return context
