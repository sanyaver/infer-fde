"""
Geico portal scraper.

Portal: https://www.geico.com/account/login/
Detection stack: PerimeterX + Geico's in-house rate limiting.

Notes on Geico's login:
  - Classic server-rendered form (not a React SPA), so the page fully reloads on submit.
  - MFA is typically sent via text/email; code entry is on a separate page.
  - Documents are under "Policy Details" > "Documents" in the account dashboard.

Selectors marked TODO — fill after inspecting with real credentials.
Run HEADLESS=false SLOW_MO=500 first time.
"""

from patchright.async_api import Page

from .base import BaseCarrier, Document


class GeicoCarrier(BaseCarrier):
    name = "geico"
    login_url = "https://ecams.geico.com/"
    documents_url = "https://portfolio.geico.com/dashboard"

    SESSION_FILE = "geico_session.json"

    async def login(self, page: Page, username: str, password: str) -> bool:
        import os, json

        # CDP mode: Chrome is already logged in — find the portfolio tab and reuse it
        if os.getenv("GEICO_CDP_URL"):
            return await self._login_cdp(page)

        # Keep PerimeterX fingerprint cookies from the persistent profile so the
        # challenge is skipped on repeat runs. Clear everything else so we always
        # authenticate with the credentials the user submitted.
        all_cookies = await page.context.cookies()
        px_cookies = [
            c for c in all_cookies
            if c.get("name", "").startswith(("_px", "pxcts"))
        ]
        await page.context.clear_cookies()
        if px_cookies:
            await page.context.add_cookies(px_cookies)

        # Fast path: if portfolio session is still alive, skip login entirely.
        # Go through ecams.geico.com first — that's where PX approved patchright
        # when the session was seeded. PX recognises the same context and issues
        # fresh _px3 cookies. With valid auth cookies ecams redirects straight to
        # portfolio, so the user never sees a login form.
        if os.path.exists(self.SESSION_FILE):
            try:
                with open(self.SESSION_FILE) as f:
                    state = json.load(f)
                await page.context.add_cookies(state.get("cookies", []))

                # Let PX run its challenge on ecams (same domain where session was created)
                await page.goto("https://ecams.geico.com/", wait_until="commit", timeout=30_000)
                await self._pass_px_challenge(page)  # waits until PX passes or redirects

                # If valid auth cookies → ecams already redirected to portfolio.
                # If not yet there, navigate explicitly.
                if "portfolio.geico.com" not in page.url:
                    await page.goto(
                        "https://portfolio.geico.com/dashboard",
                        wait_until="commit", timeout=20_000,
                    )
                    await self._pass_px_challenge_portfolio(page)

                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                if "portfolio.geico.com" in page.url:
                    await page.context.storage_state(path=self.SESSION_FILE)
                    return False
            except Exception:
                pass  # Session expired — fall through to full login
            await page.context.clear_cookies()
            if px_cookies:
                await page.context.add_cookies(px_cookies)

        # Full login — PerimeterX challenge handled by persistent profile + real Chrome
        await page.goto(self.login_url, wait_until="commit", timeout=30_000)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await self._jitter(800, 1500)

        # PerimeterX serves a blank white page while its sensor JS runs.
        # Simulate mouse movement so the sensor collects behavioral data —
        # without it the challenge never resolves and Flutter never loads.
        await self._pass_px_challenge(page)

        await self._jitter(500, 1000)

        # Username field — try several selector patterns in case Geico updated the app
        username_sel = None
        for sel in [
            'input[autocomplete="email"][data-semantics-role="text-field"]',
            'input[data-semantics-role="text-field"]:not([type="password"])',
            'input[autocomplete="email"]',
            'input[type="email"]',
            'input[autocomplete="username"]',
        ]:
            try:
                await page.wait_for_selector(sel, timeout=4_000)
                username_sel = sel
                break
            except Exception:
                continue

        if not username_sel:
            raise RuntimeError(
                f"Geico: username field not found after Flutter init. "
                f"URL={page.url} — Geico may have updated their app selectors."
            )

        # Password field fallbacks
        password_sel = None
        for sel in [
            'input[type="password"][data-semantics-role="text-field"]',
            'input[type="password"]',
        ]:
            try:
                await page.wait_for_selector(sel, timeout=3_000)
                password_sel = sel
                break
            except Exception:
                continue

        if not password_sel:
            raise RuntimeError("Geico: password field not found.")

        # Login button fallbacks
        login_btn_sel = None
        for sel in [
            'flt-semantics[flt-semantics-identifier="null_Button_Default_Title"]',
            'flt-semantics[role="button"]:has-text("Log In")',
            'flt-semantics[role="button"]:has-text("Sign In")',
            'flt-semantics[role="button"]:has-text("Login")',
            'button[type="submit"]',
        ]:
            try:
                el = await page.query_selector(sel)
                if el:
                    login_btn_sel = sel
                    break
            except Exception:
                continue

        if not login_btn_sel:
            raise RuntimeError("Geico: login button not found.")

        # Flutter picks up keyboard events — use type() not fill()
        await page.click(username_sel)
        await page.type(username_sel, username, delay=60)
        await self._jitter()

        await page.click(password_sel)
        await page.type(password_sel, password, delay=60)
        await self._jitter()

        await page.click(login_btn_sel)
        await self._jitter(1500, 2500)

        # Step 1: MFA method selection page — select "Get a Text" then continue
        try:
            text_option = await page.wait_for_selector(
                'flt-semantics[flt-semantics-identifier="mfaOptions_RadioButtons_Get a Text_RadioButton"]',
                timeout=5_000,
            )
            await text_option.click()
            await self._jitter(500, 800)

            # Click Continue/Submit — try specific text matches first, avoid Cancel
            clicked = False
            for btn_text in ["Next", "Continue", "Submit", "Send Code", "Get Code", "Send"]:
                try:
                    btn = await page.wait_for_selector(
                        f'flt-semantics[role="button"]:has-text("{btn_text}")',
                        timeout=2_000,
                    )
                    await btn.click()
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                # Last resort: click primary button by identifier but NOT Cancel
                btns = await page.query_selector_all('flt-semantics[role="button"][flt-tappable]')
                for btn in btns:
                    txt = (await btn.inner_text()).strip().lower()
                    if txt and "cancel" not in txt and "back" not in txt:
                        await btn.click()
                        break
            await self._jitter(1000, 1500)
        except Exception:
            pass  # No method selection page — code input may appear directly

        # Now wait for the code input field
        try:
            await page.wait_for_selector(
                'input[autocomplete="one-time-code"], '
                'input[data-semantics-role="text-field"][type="text"]',
                timeout=8_000,
            )
            return True  # MFA required
        except Exception:
            pass

        # Detect successful login — portfolio.geico.com loads after auth
        try:
            await page.wait_for_url("**/portfolio.geico.com/**", timeout=10_000)
            return False
        except Exception:
            pass

        # Check for error message
        error_el = await page.query_selector(
            'flt-semantics:has-text("incorrect"), flt-semantics:has-text("invalid"), '
            'flt-semantics:has-text("Unable To Find")'
        )
        if error_el:
            raise PermissionError("Login failed: incorrect credentials")

        raise RuntimeError("Login: unexpected state — check browser window")

    async def _login_cdp(self, page: Page) -> bool:
        """
        CDP mode: Chrome is already authenticated. Find the portfolio tab and reuse it.
        PerimeterX already ran against the real browser — no fingerprint mismatch.
        """
        context = page.context

        geico_page = None
        for p in context.pages:
            if "portfolio.geico.com" in p.url:
                geico_page = p
                break

        if geico_page is None:
            raise PermissionError(
                "Geico CDP: no portfolio.geico.com tab found in Chrome. "
                "Open https://portfolio.geico.com/dashboard in your Chrome window, then try again."
            )

        try:
            await geico_page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass

        self._cdp_page = geico_page
        return False  # Already authenticated, no MFA needed

    async def submit_mfa(self, page: Page, code: str) -> bool:
        # Wait for MFA code input to appear
        mfa_sel = 'input[autocomplete="one-time-code"], input[data-semantics-role="text-field"][type="text"]'
        await self._wait_nav(page, mfa_sel, timeout=10_000)
        await self._jitter(300, 600)

        # Focus the input and type via keyboard — Flutter listens to keyboard events
        await page.click(mfa_sel)
        await self._jitter(200, 400)
        # Select all first in case there's placeholder text
        await page.keyboard.press("Control+a")
        await page.keyboard.type(code, delay=80)

        # Also set via JS to ensure Flutter's state is updated
        await page.evaluate(f"""
            const el = document.querySelector('input[autocomplete="one-time-code"]')
                    || document.querySelector('input[data-semantics-role="text-field"][type="text"]');
            if (el) {{
                Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                    .set.call(el, {repr(code)});
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
        """)
        await self._jitter()

        # Submit button
        submit_sel = (
            'flt-semantics[role="button"]:has-text("Submit Code"), '
            'flt-semantics[role="button"]:has-text("Verify"), '
            'flt-semantics[role="button"]:has-text("Submit"), '
            'flt-semantics[role="button"]:has-text("Continue")'
        )
        await page.click(submit_sel)
        await self._jitter(1500, 2500)

        # "Trust this browser" appears BEFORE the portfolio.geico.com redirect.
        # Must handle it here — if we wait for the URL first, we time out while
        # the trust screen is blocking the redirect and the checkbox never gets clicked.
        try:
            await page.wait_for_selector(
                'flt-semantics[flt-semantics-identifier="trustBrowser_Checkboxes_true_CheckboxTitle"]',
                timeout=5_000,
            )
            await page.click(
                'flt-semantics[flt-semantics-identifier="trustBrowser_Checkboxes_true_CheckboxTitle"]',
            )
            await self._jitter(300, 500)
            await page.click(
                'flt-semantics[role="button"]:has-text("Submit Code"), '
                'flt-semantics[role="button"]:has-text("Next"), '
                'flt-semantics[role="button"]:has-text("Continue")',
                timeout=3_000,
            )
            await self._jitter(500, 800)
        except Exception:
            pass  # Trust screen didn't appear — Geico already trusts this device

        # Now wait for the redirect to the portfolio dashboard
        try:
            await page.wait_for_url("**/portfolio.geico.com/**", timeout=15_000)
            # Persist full storage state — cookies + localStorage — so future runs skip MFA
            await page.context.storage_state(path=self.SESSION_FILE)
            return True
        except Exception:
            pass

        error_el = await page.query_selector(
            'flt-semantics:has-text("incorrect"), flt-semantics:has-text("invalid"), '
            'flt-semantics:has-text("try again")'
        )
        if error_el:
            return False

        raise RuntimeError("MFA: unexpected state after code submission")

    async def _pass_px_challenge(self, page: Page, timeout: int = 45) -> None:
        """
        PerimeterX challenge on ecams.geico.com shows a blank white page while
        its sensor JS fingerprints the browser. Mouse movement is the key signal.
        Exits when Flutter renders (login form visible) OR when ecams redirects
        to portfolio (valid auth cookies skipped the login form entirely).
        """
        import asyncio, random
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            # Redirected to portfolio — PX passed and auth cookies were valid
            if "portfolio.geico.com" in page.url:
                return
            # Challenge resolved when Flutter starts rendering
            el = await page.query_selector("flt-semantics, input[data-semantics-role]")
            if el:
                return
            title = (await page.title()).lower()
            if "access denied" in title or "blocked" in title:
                raise RuntimeError(
                    "Geico: PerimeterX blocked the request. "
                    "Try again in a few minutes."
                )
            await page.mouse.move(
                random.randint(100, 1100),
                random.randint(80, 650),
                steps=random.randint(6, 18),
            )
            await asyncio.sleep(random.uniform(0.4, 0.8))

    async def _pass_px_challenge_portfolio(self, page: Page, timeout: int = 20) -> None:
        """Wait for portfolio.geico.com to render past any PX challenge."""
        import asyncio, random
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            title = (await page.title()).lower()
            if "access denied" in title or "blocked" in title:
                raise RuntimeError("Geico: PerimeterX blocked portfolio page.")
            # Any real nav element means the page loaded
            el = await page.query_selector(
                "nav, header, [class*='nav'], [class*='header'], "
                "[id*='nav'], [data-testid]"
            )
            if el:
                return
            await page.mouse.move(
                random.randint(100, 1100), random.randint(80, 650),
                steps=random.randint(6, 15),
            )
            await asyncio.sleep(random.uniform(0.4, 0.7))

    async def get_documents(self, page: Page) -> list[Document]:
        import asyncio

        # In CDP mode use the already-authenticated portfolio tab
        if hasattr(self, "_cdp_page"):
            page = self._cdp_page

        if "portfolio.geico.com" not in page.url:
            await page.goto(self.documents_url, wait_until="domcontentloaded", timeout=20_000)
            await self._jitter(1500, 2500)

        # Capture document-related outgoing request URLs (non-blocking listener)
        request_urls: list[str] = []

        def _on_request(request):
            url = request.url
            if any(kw in url.lower() for kw in [
                "view-document", "declaration", "idcard", "id-card", ".pdf", "/documents/"
            ]):
                request_urls.append(url)

        # Capture URLs from any new tabs that open
        new_tab_urls: list[str] = []

        async def _on_page(new_page):
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
                url = new_page.url
                if url and url.startswith("http"):
                    new_tab_urls.append(url)
                await new_page.close()
            except Exception:
                pass

        page.on("request", _on_request)
        page.context.on("page", _on_page)

        try:
            # Path 1: "Proof of Insurance" tile → declaration page
            for sel in [
                'button:has-text("Proof of Insurance")',
                'a:has-text("Proof of Insurance")',
                '[aria-label*="Proof of Insurance"]',
            ]:
                try:
                    el = await page.wait_for_selector(sel, timeout=5_000)
                    await el.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    await self._jitter(800, 1500)
                    break
                except Exception:
                    continue

            # Try to trigger document view/download buttons
            for sel in [
                'button:has-text("View Declaration Page")',
                'button:has-text("Declaration Page")',
                'button:has-text("Declaration")',
                'a:has-text("Declaration")',
                '.geico-icon--actionable',
                '[class*="icon-arrow-right"]',
                'button.btn--secondary',
                'button:has-text("View")',
            ]:
                try:
                    btns = await page.query_selector_all(sel)
                    for btn in btns[:3]:
                        try:
                            await page.evaluate("el => el.click()", btn)
                            await asyncio.sleep(1.5)
                        except Exception:
                            pass
                    if btns:
                        break
                except Exception:
                    continue

            # Path 2: fallback — "Documents" nav link
            for sel in [
                'nav a:has-text("Documents")',
                'a:has-text("Policy Documents")',
                'a[href*="/documents"]',
                'a:has-text("Documents")',
            ]:
                if request_urls or new_tab_urls:
                    break
                try:
                    el = await page.wait_for_selector(sel, timeout=3_000)
                    await el.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    await self._jitter(1000, 2000)
                    break
                except Exception:
                    continue

            await asyncio.sleep(2)  # Allow new tabs to finish opening

        finally:
            page.remove_listener("request", _on_request)
            page.context.remove_listener("page", _on_page)

        all_urls = list(dict.fromkeys(new_tab_urls + request_urls))
        print(f"[geico] new_tab_urls={new_tab_urls} request_urls={request_urls}")

        docs: list[Document] = []
        for url in all_urls:
            if not url.startswith("http"):
                continue
            url_lower = url.lower()
            if "declaration" in url_lower or "dec-page" in url_lower:
                name, doc_type = "Declaration Page", "declaration"
            elif "idcard" in url_lower or "id-card" in url_lower:
                name, doc_type = "ID Card", "id_card"
            elif "proof" in url_lower:
                name, doc_type = "Proof of Insurance", "policy"
            else:
                name, doc_type = "Policy Document", "policy"
            docs.append(Document(id=f"geico_{len(docs)}", name=name, doc_type=doc_type, url=url))

        return docs or [Document(id="geico_placeholder", name="No documents found", doc_type="policy")]

    async def download_document(self, page: Page, doc: "Document") -> bytes:
        if hasattr(self, "_cdp_page"):
            page = self._cdp_page

        if not doc.url:
            raise RuntimeError(f"No URL for {doc.id}")

        # Fetch bytes via the authenticated session — do NOT navigate the page (that would
        # crash the portfolio tab or trigger a PerimeterX re-challenge on ecams.geico.com).
        response = await page.request.get(doc.url, timeout=30_000)
        content_type = response.headers.get("content-type", "")
        body = await response.body()
        if "application/pdf" in content_type or body[:4] == b"%PDF":
            return body

        # Fallback: open a background tab, render as PDF, close it
        tab = await page.context.new_page()
        try:
            await tab.goto(doc.url, wait_until="domcontentloaded", timeout=20_000)
            return await tab.pdf(format="Letter", print_background=True)
        finally:
            await tab.close()
