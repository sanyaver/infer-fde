import asyncio
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from carriers import REGISTRY
from session_manager import SessionManager
from stealth import (
    apply_stealth, browser_pool,
    connect_cdp, connect_allstate_cdp, new_persistent_context,
    ALLSTATE_CDP_URL, ALLSTATE_PROFILE_DIR,
    GEICO_CDP_URL, GEICO_PROFILE_DIR,
)

# ── Globals ────────────────────────────────────────────────────────────────────
# Active Playwright page objects keyed by session_id.
# Lives in-memory (single process). For multi-worker deploys, migrate to
# a dedicated browser-worker service (e.g. a separate Playwright microservice)
# and communicate over a message queue.
_active_pages: dict[str, object] = {}

session_manager = SessionManager()
_playwright = None


async def _get_playwright():
    global _playwright
    if _playwright is None:
        from patchright.async_api import async_playwright
        _playwright = await async_playwright().start()
    return _playwright


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    pw = await _get_playwright()
    browser_pool.set_playwright(pw)
    # Warm up browser pool in background — don't block startup
    asyncio.create_task(browser_pool.warmup())
    yield
    await browser_pool.close_all()
    await session_manager.close()
    if _playwright:
        await _playwright.stop()


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="InsurancePull API", lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ────────────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    carrier: str
    username: str
    password: str


class MFARequest(BaseModel):
    session_id: str
    code: str


# ── Background login task ──────────────────────────────────────────────────────

async def _run_login(session_id: str, carrier_name: str, username: str, password: str):
    browser = context = page = None
    # Allstate uses a persistent Chrome profile so Akamai's fingerprint cookie
    # (_abck) is preserved across runs. Geico uses the patchright browser pool —
    # patchright's built-in stealth patches pass PerimeterX; a fresh real-Chrome
    # profile (no history, no extensions) actually scores worse with PX than a
    # well-patched Chromium context does.
    use_persistent = carrier_name == "allstate"
    try:
        pw = await _get_playwright()
        browser_pool.set_playwright(pw)

        if carrier_name == "allstate" and ALLSTATE_CDP_URL:
            browser, context = await connect_allstate_cdp(pw)
        elif carrier_name == "allstate":
            context = await new_persistent_context(pw, ALLSTATE_PROFILE_DIR)
        elif carrier_name == "geico" and GEICO_CDP_URL:
            browser, context = await connect_cdp(pw, GEICO_CDP_URL)
        else:
            browser, context = await browser_pool.acquire()

        page = await context.new_page()
        await apply_stealth(page)

        _active_pages[session_id] = page
        carrier = REGISTRY[carrier_name]["class"]()

        await session_manager.update_session(session_id, {"status": "logging_in"})
        mfa_required = await carrier.login(page, username, password)

        if mfa_required:
            await session_manager.update_session(session_id, {"status": "mfa_required"})
            mfa_code = await session_manager.wait_for_mfa(session_id, timeout=120.0)

            if not mfa_code:
                await session_manager.update_session(
                    session_id, {"status": "error", "error": "MFA timeout — please try again"}
                )
                return

            ok = await carrier.submit_mfa(page, mfa_code)
            if not ok:
                await session_manager.update_session(
                    session_id, {"status": "error", "error": "Invalid MFA code"}
                )
                return

        await session_manager.update_session(session_id, {"status": "fetching_docs"})
        documents = await carrier.get_documents(page)

        doc_metadata = []
        for doc in documents:
            try:
                pdf_bytes = await carrier.download_document(page, doc)
                await session_manager.store_document(session_id, doc.id, pdf_bytes)
                doc_metadata.append({
                    "id": doc.id,
                    "name": doc.name,
                    "type": doc.doc_type,
                    "available": True,
                })
            except Exception as exc:
                doc_metadata.append({
                    "id": doc.id,
                    "name": doc.name,
                    "type": doc.doc_type,
                    "available": False,
                    "error": str(exc),
                })

        await session_manager.update_session(
            session_id, {"status": "completed", "documents": doc_metadata}
        )

    except PermissionError as exc:
        await session_manager.update_session(
            session_id, {"status": "error", "error": str(exc)}
        )
    except Exception as exc:
        await session_manager.update_session(
            session_id,
            {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
        )
    finally:
        _active_pages.pop(session_id, None)
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if use_persistent and context:
            # Close the persistent context — profile is automatically saved to disk.
            # Next run opens a fresh context from the same profile dir.
            try:
                await context.close()
            except Exception:
                pass
        elif browser and context:
            await browser_pool.release(browser, context)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/carriers")
async def list_carriers():
    return [
        {"id": k, "label": v["label"]}
        for k, v in REGISTRY.items()
    ]


@app.post("/start-session")
async def start_session(req: StartSessionRequest, background_tasks: BackgroundTasks):
    if req.carrier not in REGISTRY:
        raise HTTPException(400, f"Unknown carrier '{req.carrier}'")
    if not req.username or not req.password:
        raise HTTPException(400, "username and password are required")

    session_id = str(uuid.uuid4())
    await session_manager.create_session(session_id, req.carrier)

    background_tasks.add_task(
        _run_login, session_id, req.carrier, req.username, req.password
    )

    return {"session_id": session_id}


@app.get("/session-status/{session_id}")
async def session_status(session_id: str):
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    # Don't leak credentials back to client
    return {k: v for k, v in session.items() if k != "mfa_code"}


@app.post("/submit-mfa")
async def submit_mfa(req: MFARequest):
    session = await session_manager.get_session(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session["status"] != "mfa_required":
        raise HTTPException(400, f"Session not awaiting MFA (status={session['status']})")
    if not req.code.strip():
        raise HTTPException(400, "MFA code is required")

    await session_manager.update_session(
        req.session_id,
        {"status": "mfa_submitted", "mfa_code": req.code.strip()},
    )
    return {"ok": True}


@app.get("/documents/{session_id}")
async def get_documents(session_id: str):
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session["status"] != "completed":
        raise HTTPException(400, f"Documents not ready (status={session['status']})")
    return {"documents": session.get("documents", [])}


@app.get("/document/{session_id}/{doc_id}")
async def get_document(session_id: str, doc_id: str):
    pdf_bytes = await session_manager.get_document(session_id, doc_id)
    if not pdf_bytes:
        raise HTTPException(404, "Document not found or expired")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{doc_id}.pdf"',
            "Cache-Control": "private, max-age=3600",
        },
    )
