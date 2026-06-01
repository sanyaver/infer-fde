"""
Seeds the Geico Chrome profile so PerimeterX trusts the browser fingerprint.
Run once — after this, the automation logs in without the white-screen block.

Usage:
    python3 save_geico_session.py

What happens:
  1. Opens REAL Chrome (not bundled Chromium) with a persistent profile dir
  2. Navigates to ecams.geico.com — PerimeterX resolves because it sees real Chrome
  3. You log in, complete MFA, check "Trust this browser"
  4. PerimeterX fingerprint + session cookies saved to profile + session file
  5. All future runs skip both the challenge and MFA
"""
import asyncio
import os
from patchright.async_api import async_playwright

PROFILE_DIR = os.getenv("GEICO_PROFILE_DIR", os.path.expanduser("~/.geico-chrome-profile"))
SESSION_FILE = "geico_session.json"


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
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await context.new_page()

        print("Opening Geico login...")
        await page.goto("https://ecams.geico.com/", wait_until="commit")

        print("\n" + "=" * 50)
        print("ACTION REQUIRED:")
        print("1. Log in with your credentials")
        print("2. Complete MFA")
        print('3. Check "Trust this browser"')
        print("4. Wait until portfolio.geico.com dashboard loads")
        print("=" * 50)
        print("\nWaiting for dashboard...")

        await page.wait_for_url("**/portfolio.geico.com/**", timeout=300_000)
        await page.wait_for_timeout(2000)

        await context.storage_state(path=SESSION_FILE)
        print(f"\n✓ Session saved to {SESSION_FILE}")
        print(f"✓ Profile saved to {PROFILE_DIR}")
        print("Future runs will skip MFA and the PerimeterX challenge.")

        await context.close()


asyncio.run(main())
