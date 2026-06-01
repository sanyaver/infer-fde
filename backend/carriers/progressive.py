"""
Progressive Insurance portal scraper.

Portal: https://www.progressive.com/account/
Detection stack: Akamai Bot Manager + in-house telemetry.

Selectors marked TODO — fill these in after inspecting the live portal with
real credentials. Use browser DevTools → Elements tab → right-click → Copy selector.
Run with HEADLESS=false and SLOW_MO=500 the first time to watch the flow.
"""

from patchright.async_api import Page

from .base import BaseCarrier, Document


class ProgressiveCarrier(BaseCarrier):
    name = "progressive"
    login_url = "https://www.progressive.com/account/login/"
    documents_url = "https://www.progressive.com/account/documents/"

    async def login(self, page: Page, username: str, password: str) -> bool:
        await page.goto(self.login_url, wait_until="commit", timeout=30_000)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)

        # TODO: verify selector after live inspection
        await self._wait_nav(page, 'input[name="email"], #email')
        await self._jitter()

        await self._type_human(page, 'input[name="email"], #email', username)
        await self._jitter()

        await self._type_human(page, 'input[name="password"], #password', password)
        await self._jitter()

        await page.click('button[type="submit"]')

        # Wait for either: post-login landing OR MFA challenge
        # TODO: replace with selectors you observe after submitting credentials
        try:
            await page.wait_for_selector(
                '[data-testid="mfa-page"], #mfa-code, [class*="verification"], '
                '#authCode, [aria-label*="verification code"]',
                timeout=10_000,
            )
            return True  # MFA required
        except Exception:
            pass

        # Check for successful login (account dashboard present)
        # TODO: replace with a reliable post-login element selector
        try:
            await page.wait_for_selector(
                '[data-testid="account-dashboard"], .account-summary, '
                'a[href*="/account/"]',
                timeout=8_000,
            )
            return False  # Logged in, no MFA
        except Exception:
            pass

        # Check for credential error
        error_el = await page.query_selector(
            '.error-message, [data-testid="login-error"], [role="alert"]'
        )
        if error_el:
            error_text = await error_el.inner_text()
            raise PermissionError(f"Login failed: {error_text.strip()}")

        raise RuntimeError("Login: unexpected page state — check selectors")

    async def submit_mfa(self, page: Page, code: str) -> bool:
        # TODO: verify MFA input selector
        mfa_input = await page.query_selector(
            '#mfa-code, input[name="authCode"], input[autocomplete="one-time-code"], '
            '[aria-label*="verification code"]'
        )
        if not mfa_input:
            raise RuntimeError("MFA input not found — check selectors")

        await mfa_input.click()
        await page.type(
            '#mfa-code, input[name="authCode"], input[autocomplete="one-time-code"], '
            '[aria-label*="verification code"]',
            code,
            delay=60,
        )
        await self._jitter()

        # TODO: verify submit button selector
        await page.click('button[type="submit"]')

        try:
            # TODO: replace with a reliable post-MFA success selector
            await page.wait_for_selector(
                '[data-testid="account-dashboard"], .account-summary',
                timeout=10_000,
            )
            return True
        except Exception:
            error_el = await page.query_selector('.error-message, [role="alert"]')
            if error_el:
                return False
            raise RuntimeError("MFA: unexpected page state after code submission")

    async def get_documents(self, page: Page) -> list[Document]:
        await page.goto(self.documents_url, wait_until="domcontentloaded", timeout=20_000)
        await self._jitter(500, 1200)

        # TODO: inspect the documents page and replace with real selectors.
        # Example structure (adjust to actual DOM):
        #   Each document row: <div class="document-row">
        #     <span class="doc-name">Policy Documents</span>
        #     <a href="/account/documents/download/12345" class="download-link">Download</a>
        #   </div>

        docs: list[Document] = []

        # TODO: replace selector with actual document container
        rows = await page.query_selector_all(".document-row, [data-testid='doc-item']")
        for i, row in enumerate(rows):
            # TODO: adjust sub-selectors to match actual HTML
            name_el = await row.query_selector(".doc-name, [data-testid='doc-name']")
            link_el = await row.query_selector("a[href*='download'], a[href*='pdf']")

            name = (await name_el.inner_text()).strip() if name_el else f"Document {i+1}"
            href = await link_el.get_attribute("href") if link_el else None

            if href and not href.startswith("http"):
                href = f"https://www.progressive.com{href}"

            docs.append(Document(
                id=f"prog_{i}",
                name=name,
                doc_type="policy",
                url=href,
            ))

        # Fallback: if no documents found via selectors, return a placeholder
        if not docs:
            docs.append(Document(
                id="prog_placeholder",
                name="[Selector TODO] No documents found — update selectors",
                doc_type="policy",
            ))

        return docs
