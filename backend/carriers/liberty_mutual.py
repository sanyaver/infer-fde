"""
Liberty Mutual portal scraper.

Portal: https://account.libertymutual.com/
Detection stack: ThreatMetrix + LM proprietary bot detection.

Notes:
  - React SPA — the URL may not change on navigation; use wait_for_selector instead
    of wait_for_load_state("navigation").
  - MFA: Liberty Mutual typically sends a 6-digit code via text or email.
  - Documents are under "My Account" > "Policy Documents" or "eDocuments".
  - Some LM users are on the newer "MyLM" portal; adjust URLs if needed.

Selectors marked TODO.
"""

from patchright.async_api import Page

from .base import BaseCarrier, Document


class LibertyMutualCarrier(BaseCarrier):
    name = "liberty_mutual"
    login_url = "https://idp.libertymutual.com/app/lmig_myaccount_1/exk2k6x8r5LhMp8hg697/sso/saml"
    # Fallback login URL if above redirects differ
    login_url_alt = "https://www.libertymutual.com/account/login"
    documents_url = "https://account.libertymutual.com/documents"

    async def login(self, page: Page, username: str, password: str) -> bool:
        # LM may redirect through Okta IdP — go to the account root and let it redirect
        await page.goto("https://account.libertymutual.com/", wait_until="commit", timeout=30_000)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await self._jitter(500, 1000)

        # TODO: verify email/username selector
        await self._wait_nav(
            page,
            'input[name="username"], input[name="email"], #username, #email',
            timeout=15_000,
        )
        await self._jitter()

        await self._type_human(
            page,
            'input[name="username"], input[name="email"], #username, #email',
            username,
        )
        await self._jitter()

        # Some LM flows show password on a second step after email
        # TODO: if password field isn't visible after email, click "Next" first
        try:
            next_btn = await page.query_selector(
                '#next-btn, button:has-text("Next"), button:has-text("Continue")'
            )
            if next_btn:
                await next_btn.click()
                await self._jitter(400, 800)
        except Exception:
            pass

        await self._wait_nav(page, 'input[name="password"], #password', timeout=10_000)
        await self._type_human(page, 'input[name="password"], #password', password)
        await self._jitter()

        await page.click(
            'button[type="submit"], #sign-in-btn, button:has-text("Sign in"), '
            'button:has-text("Log in")'
        )

        # React SPA — wait for either MFA page or dashboard
        try:
            await page.wait_for_selector(
                'input[name="passcode"], #passcode, input[autocomplete="one-time-code"], '
                '[class*="mfa"], [class*="verification"]',
                timeout=10_000,
            )
            return True  # MFA required
        except Exception:
            pass

        # TODO: update with a reliable post-login element
        try:
            await page.wait_for_selector(
                '.account-overview, [data-testid="dashboard"], '
                '#policy-cards, [class*="PolicyCard"]',
                timeout=8_000,
            )
            return False
        except Exception:
            pass

        error_el = await page.query_selector('[class*="error"], [role="alert"]')
        if error_el:
            error_text = await error_el.inner_text()
            raise PermissionError(f"Login failed: {error_text.strip()}")

        raise RuntimeError("Login: unexpected page state — update selectors")

    async def submit_mfa(self, page: Page, code: str) -> bool:
        # TODO: verify MFA input selector
        await self._wait_nav(
            page,
            'input[name="passcode"], #passcode, input[autocomplete="one-time-code"]',
            timeout=10_000,
        )
        await self._type_human(
            page,
            'input[name="passcode"], #passcode, input[autocomplete="one-time-code"]',
            code,
        )
        await self._jitter()

        await page.click(
            'button[type="submit"], #verify-btn, button:has-text("Verify")'
        )

        try:
            await page.wait_for_selector(
                '.account-overview, [data-testid="dashboard"], #policy-cards',
                timeout=10_000,
            )
            return True
        except Exception:
            pass

        error_el = await page.query_selector('[class*="error"], [role="alert"]')
        if error_el:
            return False

        raise RuntimeError("MFA: unexpected state after code submission")

    async def get_documents(self, page: Page) -> list[Document]:
        await page.goto(self.documents_url, wait_until="domcontentloaded", timeout=20_000)
        await self._jitter(500, 1200)

        # TODO: inspect Liberty Mutual's documents page and update selectors.
        # Liberty Mutual eDocuments page typically shows a list of PDFs by policy.
        # Example structure (adjust to actual DOM after inspection):
        #   <div class="document-item">
        #     <span class="document-title">Policy Dec Page</span>
        #     <button class="download-btn" data-doc-id="abc123">Download</button>
        #   </div>

        docs: list[Document] = []

        rows = await page.query_selector_all(
            ".document-item, [data-testid='document-item'], .doc-card"
        )
        for i, row in enumerate(rows):
            title_el = await row.query_selector(
                ".document-title, [data-testid='doc-title'], h3, h4"
            )
            link_el = await row.query_selector(
                "a[href*='.pdf'], a[href*='download'], button.download-btn"
            )

            name = (await title_el.inner_text()).strip() if title_el else f"Document {i+1}"

            if link_el:
                tag = await link_el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "a":
                    href = await link_el.get_attribute("href") or ""
                    if not href.startswith("http"):
                        href = f"https://account.libertymutual.com{href}"
                    docs.append(Document(
                        id=f"lm_{i}",
                        name=name,
                        doc_type="policy",
                        url=href,
                    ))
                else:
                    # Button that triggers download popup
                    docs.append(Document(
                        id=f"lm_{i}",
                        name=name,
                        doc_type="policy",
                        download_selector=f".document-item:nth-child({i+1}) button.download-btn",
                    ))
            else:
                docs.append(Document(id=f"lm_{i}", name=name, doc_type="policy"))

        if not docs:
            docs.append(Document(
                id="lm_placeholder",
                name="[Selector TODO] No documents found — update selectors",
                doc_type="policy",
            ))

        return docs
