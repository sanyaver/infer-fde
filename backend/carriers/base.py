import asyncio
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from patchright.async_api import Download, Page, Response


@dataclass
class Document:
    id: str
    name: str
    doc_type: str  # "policy", "id_card", "declaration", "billing", etc.
    url: Optional[str] = None
    download_selector: Optional[str] = None


class BaseCarrier(ABC):
    name: str = ""
    login_url: str = ""
    documents_url: str = ""

    @abstractmethod
    async def login(self, page: Page, username: str, password: str) -> bool:
        """
        Navigate to login page, fill credentials, submit.
        Returns True if MFA is required after login attempt.
        Raises on hard errors (wrong credentials, locked account, etc.).
        """
        ...

    @abstractmethod
    async def submit_mfa(self, page: Page, code: str) -> bool:
        """
        Enter MFA code into the currently-loaded MFA prompt page.
        Returns True on success, False on wrong code.
        """
        ...

    @abstractmethod
    async def get_documents(self, page: Page) -> list[Document]:
        """
        Navigate to the documents section and return metadata for all
        available documents. Actual bytes are fetched by download_document.
        """
        ...

    async def download_document(self, page: Page, doc: Document) -> bytes:
        """
        Download a single document's bytes.
        Default: GET the doc.url using the page's authenticated session cookies.
        Override if the portal requires clicking a button to trigger a download popup.
        """
        if doc.url:
            response: Response = await page.request.get(doc.url)
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status} fetching {doc.url}")
            return await response.body()

        if doc.download_selector:
            return await self._click_download(page, doc.download_selector)

        raise NotImplementedError(f"No url or selector for document {doc.id}")

    async def _click_download(self, page: Page, selector: str) -> bytes:
        """Helper: click a link/button that triggers a file download."""
        async with page.expect_download(timeout=30_000) as dl_info:
            await page.click(selector)
        download: Download = await dl_info.value
        path = await download.path()
        with open(path, "rb") as f:
            return f.read()

    async def _type_human(self, page: Page, selector: str, text: str) -> None:
        """Type with per-keystroke delay to avoid bot detection heuristics."""
        await page.click(selector)
        await page.type(selector, text, delay=random.randint(40, 120))

    async def _jitter(self, lo: int = 300, hi: int = 900) -> None:
        await asyncio.sleep(random.randint(lo, hi) / 1000)

    async def _wait_nav(self, page: Page, selector: str, timeout: int = 15_000) -> None:
        await page.wait_for_selector(selector, timeout=timeout)
