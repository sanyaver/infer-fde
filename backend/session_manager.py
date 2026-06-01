import asyncio
import base64
import json
import os
from typing import Optional

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
SESSION_TTL = 3600  # 1 hour


class SessionManager:
    def __init__(self):
        self._redis_str: Optional[redis.Redis] = None
        self._redis_bytes: Optional[redis.Redis] = None

    async def _str(self) -> redis.Redis:
        if self._redis_str is None:
            self._redis_str = redis.from_url(REDIS_URL, decode_responses=True)
        return self._redis_str

    async def _bytes(self) -> redis.Redis:
        if self._redis_bytes is None:
            self._redis_bytes = redis.from_url(REDIS_URL, decode_responses=False)
        return self._redis_bytes

    async def create_session(self, session_id: str, carrier: str) -> None:
        r = await self._str()
        data = {
            "session_id": session_id,
            "carrier": carrier,
            "status": "pending",
            "error": None,
            "documents": [],
        }
        await r.setex(f"session:{session_id}", SESSION_TTL, json.dumps(data))

    async def get_session(self, session_id: str) -> Optional[dict]:
        r = await self._str()
        raw = await r.get(f"session:{session_id}")
        if not raw:
            return None
        return json.loads(raw)

    async def update_session(self, session_id: str, updates: dict) -> None:
        r = await self._str()
        session = await self.get_session(session_id)
        if session:
            session.update(updates)
            await r.setex(f"session:{session_id}", SESSION_TTL, json.dumps(session))

    async def store_document(self, session_id: str, doc_id: str, data: bytes) -> None:
        r = await self._str()
        encoded = base64.b64encode(data).decode("utf-8")
        await r.setex(f"doc:{session_id}:{doc_id}", SESSION_TTL, encoded)

    async def get_document(self, session_id: str, doc_id: str) -> Optional[bytes]:
        r = await self._str()
        raw = await r.get(f"doc:{session_id}:{doc_id}")
        if not raw:
            return None
        return base64.b64decode(raw)

    async def wait_for_mfa(self, session_id: str, timeout: float = 120.0) -> Optional[str]:
        """Poll Redis every 500ms until user submits MFA code or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            session = await self.get_session(session_id)
            if not session:
                return None
            if session.get("status") == "mfa_submitted":
                code = session.get("mfa_code")
                await self.update_session(session_id, {"status": "mfa_processing"})
                return code
            await asyncio.sleep(0.5)
        return None

    async def close(self) -> None:
        for conn in [self._redis_str, self._redis_bytes]:
            if conn:
                await conn.aclose()
