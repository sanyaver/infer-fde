"""
Allstate portal scraper.

Portal: https://myaccountrwd.allstate.com/
Detection stack: Akamai Bot Manager.

HOW TO USE:
  1. Run once: python3 save_allstate_session.py
     → logs in manually, seeds ~/.allstate-chrome-profile
  2. Start backend: HEADLESS=false uvicorn main:app --reload
     → automation reuses the same Chrome profile → same Akamai fingerprint → no block

WHY THIS WORKS:
  Akamai's _abck cookie is fingerprint-bound (Canvas, WebGL, device IDs, timing).
  launch_persistent_context reuses the same user-data-dir, so the fingerprint is
  identical every run. Akamai sees the same browser it approved during manual login.
  An ephemeral new_context() has a different fingerprint each time → Akamai blocks it.
"""

from patchright.async_api import Page

from .base import BaseCarrier, Document


class AllstateCarrier(BaseCarrier):
    name = "allstate"
    login_url = "https://myaccountrwd.allstate.com/anon/account/login"
    documents_url = "https://myaccountrwd.allstate.com/secured/documents/policy-documents"

    async def login(self, page: Page, username: str, password: str) -> bool:
        # CDP mode: user already logged in manually in their own Chrome.
        # The page handed to us is whatever tab is active — find the Allstate one.
        from os import getenv
        if getenv("ALLSTATE_CDP_URL"):
            return await self._login_cdp(page)

        # Persistent-profile mode.
        # Clear Allstate session cookies so we always log in fresh with the caller's
        # credentials — but keep Akamai's _abck fingerprint cookie, which is what
        # allows the login to pass bot detection on this seeded profile.
        all_cookies = await page.context.cookies()
        akamai_cookies = [
            c for c in all_cookies
            if c.get("name", "").startswith("_abck")
            or c.get("name", "") in ("bm_sz", "ak_bmsc", "bm_mi")
        ]
        await page.context.clear_cookies()
        if akamai_cookies:
            await page.context.add_cookies(akamai_cookies)

        # Wipe localStorage/sessionStorage from the previous session
        try:
            await page.goto(
                "https://myaccountrwd.allstate.com", wait_until="commit", timeout=10_000
            )
            await page.evaluate("localStorage.clear(); sessionStorage.clear();")
        except Exception:
            pass

        # Fresh login with the caller's credentials
        await page.goto(self.login_url, wait_until="domcontentloaded", timeout=30_000)
        await self._jitter(500, 1000)

        # Akamai may serve index-allstate.html first — a blank JS challenge page that
        # redirects to the real login only after its sensor script passes.
        await self._pass_akamai_challenge(page)

        # Handle "log back in" redirect
        try:
            lb = await page.wait_for_selector(
                'a[href*="logbackin"], a:has-text("log back in")', timeout=3000
            )
            await lb.click()
            await self._pass_akamai_challenge(page)
        except Exception:
            pass

        # Give Akamai's inline sensor script time to finish and let React render the form
        await self._jitter(2000, 3000)

        # Find username field — try multiple selectors in case Allstate updated their form
        username_sel = None
        for sel in [
            "#username",
            'input[name="username"]',
            'input[autocomplete="username"]',
            'input[type="email"]',
            "#email",
            'input[name="email"]',
        ]:
            try:
                await page.wait_for_selector(sel, timeout=8_000, state="visible")
                username_sel = sel
                break
            except Exception:
                continue

        if not username_sel:
            raise RuntimeError(
                f"Allstate: login form not found after 30s. "
                f"URL={page.url} title='{await page.title()}' — "
                "Akamai may be blocking this IP or the portal selectors changed."
            )

        await page.click(username_sel)
        await page.type(username_sel, username, delay=60)
        await self._jitter()

        # Password field — same fallback approach
        password_sel = None
        for sel in ["#password", 'input[name="password"]', 'input[type="password"]']:
            try:
                await page.wait_for_selector(sel, timeout=5_000, state="visible")
                password_sel = sel
                break
            except Exception:
                continue

        if not password_sel:
            raise RuntimeError("Allstate: password field not found.")

        await page.click(password_sel)
        await page.type(password_sel, password, delay=60)
        await self._jitter()

        # Submit — try multiple button patterns
        submitted = False
        for sel in [
            'button[name="frmButton"]',
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Log in")',
            'button:has-text("Sign in")',
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    submitted = True
                    break
            except Exception:
                continue
        if not submitted:
            raise RuntimeError("Allstate: submit button not found.")
        await self._jitter(1500, 2500)

        # MFA method selection — click SMS then Continue
        try:
            await page.wait_for_selector('span[role="checkbox"]', timeout=8_000)
            await page.click('span[role="checkbox"]:has-text("SMS")')
            await self._jitter(500, 800)
            await page.click('span[data-placementid="myas1137"]:has-text("continue")')
            await self._jitter(500, 800)
            return True
        except Exception:
            pass

        try:
            await page.wait_for_url("**/secured/**", timeout=8_000)
            return False
        except Exception:
            pass

        error_el = await page.query_selector('[class*="error"], [role="alert"]')
        if error_el:
            raise PermissionError(f"Login failed: {(await error_el.inner_text()).strip()}")

        raise RuntimeError("Login: unexpected state — check browser window")

    async def _pass_akamai_challenge(self, page: Page, timeout: int = 60) -> None:
        """
        Akamai's index-allstate.html is a blank JS challenge page.
        It collects sensor data and redirects to the real page if the browser passes.
        The single biggest signal Akamai checks: mouse movement.
        Without it, zero mouse events → instant bot flag → challenge never resolves.
        """
        import asyncio, random
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if "index-allstate" not in page.url:
                await self._jitter(400, 800)
                return
            # Simulate natural mouse movement across the blank page
            await page.mouse.move(
                random.randint(100, 1100),
                random.randint(80, 650),
                steps=random.randint(8, 20),
            )
            await asyncio.sleep(random.uniform(0.3, 0.7))
        # If we get here patchright didn't pass the challenge
        raise RuntimeError(
            "Allstate: stuck on Akamai challenge page after 30s. "
            "Use CDP mode instead: start Chrome with --remote-debugging-port=9222, "
            "log in manually, then set ALLSTATE_CDP_URL=http://localhost:9222."
        )

    async def _login_cdp(self, page: Page) -> bool:
        """
        CDP mode: user already logged in manually. Find the authenticated tab.
        We look across all open pages in the connected browser for an Allstate
        secured page, then reuse it — no credentials entered by automation at all.
        """
        context = page.context

        # Find a tab already on the authenticated Allstate dashboard
        allstate_page = None
        for p in context.pages:
            if "myaccountrwd.allstate.com/secured" in p.url and "login" not in p.url:
                allstate_page = p
                break

        if allstate_page is None:
            raise PermissionError(
                "Allstate CDP: no authenticated Allstate tab found. "
                "Log in at https://myaccountrwd.allstate.com in your Chrome window, "
                "wait for the dashboard to load, then try again."
            )

        # Bring that page into scope so get_documents uses it
        # We swap the page reference by navigating our assigned page to the same URL,
        # but since we can't swap the page object, store it on the carrier instead.
        self._cdp_page = allstate_page

        try:
            await allstate_page.wait_for_selector("#NavItemPolicies", timeout=10_000)
        except Exception:
            raise PermissionError(
                "Allstate CDP: tab found but dashboard looks bare — "
                "make sure you're fully logged in and the nav has loaded."
            )

        return False  # No MFA needed — user handled it

    async def submit_mfa(self, page: Page, code: str) -> bool:
        await self._wait_nav(page, "#pinCode", timeout=15_000)
        await self._jitter(300, 500)

        await page.click("#pinCode")
        await page.keyboard.press("Control+a")
        await page.keyboard.type(code, delay=80)
        await self._jitter()

        await page.click('span[data-placementid="myas1137"]:has-text("submit")')
        await self._jitter(1500, 2500)

        try:
            await page.wait_for_url("**/secured/**", timeout=10_000)
            return True
        except Exception:
            pass

        error_el = await page.query_selector('[class*="error"], [role="alert"]')
        if error_el:
            return False

        raise RuntimeError("MFA: unexpected state after code submission")

    async def get_documents(self, page: Page) -> list[Document]:
        if hasattr(self, "_cdp_page"):
            page = self._cdp_page

        await self._jitter(1500, 2500)

        # Expand the Policies nav dropdown — works from any /secured/ page
        try:
            await page.wait_for_selector("#NavItemPolicies", timeout=15_000)
        except Exception:
            raise RuntimeError("Allstate: #NavItemPolicies not found on page")

        expanded = await page.get_attribute("#NavItemPolicies", "aria-expanded")
        if expanded != "true":
            await page.evaluate("document.getElementById('NavItemPolicies').click()")
            await self._jitter(600, 1000)

        # Click the Documents button for the policy (skip Endorsements/Billing/etc.)
        try:
            await page.wait_for_selector(
                '.btn--header.btn--policy[aria-label*="Documents"]', timeout=10_000
            )
        except Exception:
            raise RuntimeError("Allstate: Documents policy button not found after nav expand")

        await page.evaluate("""
            document.querySelector('.btn--header.btn--policy[aria-label*="Documents"]').click()
        """)
        await self._jitter(800, 1400)

        # Collect document links — they appear inline in the dropdown after the click
        try:
            await page.wait_for_selector(
                'a[aria-label*="directed to a new page with pdf"]', timeout=10_000
            )
        except Exception:
            return [Document(id="allstate_placeholder", name="No documents found", doc_type="policy")]

        doc_links = await page.query_selector_all(
            'a[aria-label*="directed to a new page with pdf"]'
        )

        # Collect names first (before any clicks change the DOM)
        doc_names = []
        for link in doc_links:
            aria = await link.get_attribute("aria-label") or ""
            doc_names.append(aria.replace(" will be directed to a new page with pdf", "").strip())

        # Click all links as fast as possible so the tabs open in parallel,
        # then wait for each one to redirect away from waitingToLoad.html.
        # Sequential click+wait would multiply latency by the number of docs.
        import asyncio

        opened_tabs = []
        for j in range(len(doc_links)):
            try:
                async with page.context.expect_page(timeout=8_000) as np_info:
                    await page.evaluate(
                        "document.querySelectorAll"
                        "('a[aria-label*=\"directed to a new page with pdf\"]')"
                        f"[{j}].click()"
                    )
                tab = await np_info.value
                opened_tabs.append((j, tab))
            except Exception:
                opened_tabs.append((j, None))

        async def _resolve_tab(j, tab):
            if tab is None:
                return j, None
            try:
                await tab.wait_for_url(
                    lambda url: "waitingToLoad" not in url and url != "about:blank",
                    timeout=20_000,
                )
                url = tab.url
                await tab.close()
                return j, url
            except Exception:
                try:
                    await tab.close()
                except Exception:
                    pass
                return j, None

        results = await asyncio.gather(*[_resolve_tab(j, tab) for j, tab in opened_tabs])

        docs: list[Document] = []
        for j, pdf_url in sorted(results):
            docs.append(Document(
                id=f"allstate_{j}",
                name=f"{doc_names[j] if j < len(doc_names) else f'Document {j+1}'} (Renter policy)",
                doc_type="policy",
                url=pdf_url,
            ))

        return docs or [Document(id="allstate_placeholder", name="No documents found", doc_type="policy")]

    async def download_document(self, page: Page, doc: Document) -> bytes:
        if hasattr(self, "_cdp_page"):
            page = self._cdp_page

        if doc.url:
            # Always fetch from inside the browser so Allstate's session cookies and
            # any in-memory auth state are included. page.request.get() in CDP mode
            # does not reliably share the live browser cookie jar, so a direct HTTP
            # fetch returns an HTML login/error page instead of the PDF bytes.
            bytes_list = await page.evaluate(
                """async (url) => {
                    const r = await fetch(url, { credentials: "include" });
                    const buf = await r.arrayBuffer();
                    return Array.from(new Uint8Array(buf));
                }""",
                doc.url,
            )
            if not bytes_list:
                raise RuntimeError(f"Empty response fetching {doc.url}")
            data = bytes(bytes_list)
            # Sanity-check: real PDFs start with %PDF
            if not data.startswith(b"%PDF"):
                raise RuntimeError(
                    f"Response for {doc.url} is not a PDF "
                    f"(starts with {data[:40]!r})"
                )
            return data

        if doc.download_selector:
            # Click the link, capture the new tab URL, then fetch from inside the browser
            async with page.context.expect_page() as new_page_info:
                await page.click(doc.download_selector)
            new_page = await new_page_info.value
            await new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
            pdf_url = new_page.url
            bytes_list = await page.evaluate(
                """async (url) => {
                    const r = await fetch(url, { credentials: "include" });
                    const buf = await r.arrayBuffer();
                    return Array.from(new Uint8Array(buf));
                }""",
                pdf_url,
            )
            await new_page.close()
            return bytes(bytes_list)

        raise NotImplementedError(f"No url or selector for document {doc.id}")
