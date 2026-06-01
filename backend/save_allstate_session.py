"""
Run once to log into Allstate manually and seed the Chrome profile.
Subsequent automation runs reuse this profile — same fingerprint, no Akamai block.

Usage:
    python3 save_allstate_session.py

The browser profile is saved to ~/.allstate-chrome-profile.
Set ALLSTATE_PROFILE_DIR env var to override the location.
"""
import asyncio
import os
import subprocess
import sys

from patchright.async_api import async_playwright


def _ensure_chrome() -> None:
    """Auto-install Chrome via playwright if the binary is missing."""
    try:
        from patchright.sync_api import sync_playwright
        with sync_playwright() as p:
            # channel="chrome" resolves to a different executable than chromium
            exe = p.chromium.executable_path
            # Chrome lives one dir up from the chromium dir with a different name
            chrome_exe = exe.replace("chromium", "chrome").replace(
                "Chromium", "Google Chrome"
            )
            if os.path.exists(chrome_exe):
                return
    except Exception:
        pass
    print("Chrome not found — running one-time install (~1 min)…")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chrome"], check=True)
    print("Done.\n")

PROFILE_DIR = os.getenv("ALLSTATE_PROFILE_DIR", os.path.expanduser("~/.allstate-chrome-profile"))


async def main():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    print(f"Using profile: {PROFILE_DIR}")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            channel="chrome",
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await context.new_page()

        # Check if already logged in from a previous profile save
        print("Checking for existing session...")
        await page.goto(
            "https://myaccountrwd.allstate.com/secured/home",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        await page.wait_for_timeout(3000)

        if "secured" in page.url and "login" not in page.url:
            try:
                await page.wait_for_selector("#NavItemPolicies", timeout=8_000)
                print("✓ Already logged in — profile is valid, nothing to do.")
                await context.close()
                return
            except Exception:
                pass

        # Need fresh login
        await page.goto(
            "https://myaccountrwd.allstate.com/anon/account/login",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        await page.wait_for_timeout(2000)

        # Handle "log back in" redirect
        try:
            lb = await page.wait_for_selector(
                'a[href*="logbackin"], a:has-text("log back in")', timeout=3000
            )
            await lb.click()
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            await page.wait_for_timeout(1000)
        except Exception:
            pass

        print("\n" + "=" * 50)
        print("ACTION REQUIRED:")
        print("1. Log in with your Allstate credentials")
        print("2. Complete MFA if prompted")
        print("3. Wait until the dashboard fully loads (nav visible)")
        print("=" * 50)
        print("\nWaiting for dashboard...")

        await page.wait_for_url("**/secured/**", timeout=300_000)
        await page.wait_for_timeout(3000)

        print(f"✓ Profile saved to: {PROFILE_DIR}")
        print("Start the backend with:")
        print(f"  ALLSTATE_PROFILE_DIR={PROFILE_DIR} BROWSER_CHANNEL=chrome HEADLESS=false uvicorn main:app --reload")
        await context.close()


_ensure_chrome()
asyncio.run(main())
