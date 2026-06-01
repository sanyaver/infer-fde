import asyncio
import os
from urllib.parse import urlparse
from patchright.async_api import async_playwright

PROXY_URL = os.getenv("PROXY_URL")

def parse_proxy(url: str) -> dict:
    """Split proxy URL into Playwright's expected format with separate credentials."""
    p = urlparse(url)
    config = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        config["username"] = p.username      # urlparse auto-decodes %3D → =
    if p.password:
        config["password"] = p.password
    return config

async def test():
    proxy = parse_proxy(PROXY_URL) if PROXY_URL else None
    print(f"Proxy config: {proxy}")

    async with async_playwright() as pw:
        print("1. Launching browser...")
        browser = await pw.chromium.launch(
            headless=False,
            proxy=proxy,
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        # Patch navigator.webdriver before any navigation
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
            window.chrome = { runtime: {} };
        """)
        print("2. Browser ready")

        print("3. Navigating to example.com...")
        await page.goto("https://example.com", wait_until="domcontentloaded")
        print(f"   Title: {await page.title()}")

        print("4. Checking IP via ipinfo.io...")
        await page.goto("https://ipinfo.io/json", wait_until="domcontentloaded")
        body = await page.inner_text("body")
        print(f"   IP info: {body[:200]}")

        print("5. Navigating to geico.com login...")
        await page.goto(
            "https://ecams.geico.com/",
            wait_until="commit",   # less strict — just wait for response headers
            timeout=30_000,
        )
        print(f"   URL after nav: {page.url}")
        await page.wait_for_timeout(3000)
        print(f"   Title: {await page.title()}")

        print("All done — check the browser window")
        await page.wait_for_timeout(5000)
        await browser.close()

asyncio.run(test())
