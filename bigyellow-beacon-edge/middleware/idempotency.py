"""
Idempotency-Key middleware for Edge write endpoints.
Caches responses for idempotent replay.
"""

from typing import Dict, Any
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

IDEMPOTENCY_CACHE: Dict[str, Dict[str, Any]] = {}


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
        if not idempotency_key:
            return await call_next(request)

        # Check cache
        cached = IDEMPOTENCY_CACHE.get(idempotency_key)
        if cached:
            return StarletteResponse(
                content=cached["content"],
                status_code=cached["status_code"],
                headers={**dict(cached["headers"]), "X-Cache-Lookup": "HIT-IDEMPOTENT"},
                media_type=cached["media_type"],
            )

        response = await call_next(request)

        if 200 <= response.status_code < 300:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

            IDEMPOTENCY_CACHE[idempotency_key] = {
                "status_code": response.status_code,
                "content": body,
                "headers": dict(response.headers),
                "media_type": response.media_type,
            }

            return StarletteResponse(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        return response
