"""
AegisCoder -- token authentication middleware.

Implemented as a pure ASGI middleware (no BaseHTTPMiddleware subclass,
no starlette imports) so Pylance can fully analyze this module without
needing starlette type stubs to be present.

Design:
  - Localhost (127.0.0.1 / ::1) is always trusted -- no token needed.
    This covers the desktop pywebview app.
  - All other connections must supply ACCESS_TOKEN.
  - If REMOTE_ACCESS_ENABLED is False, all non-localhost connections are
    rejected outright regardless of token.

Token delivery:
  HTTP:      Authorization: Bearer <token>
  WebSocket: ws://host/ws/chat?token=<token>
    (browsers cannot set custom WebSocket headers, so the token travels
    as a query parameter for WebSocket upgrades.)
"""
import json
import logging
from typing import Any, Callable

from engine.config import ACCESS_TOKEN, REMOTE_ACCESS_ENABLED

log = logging.getLogger(__name__)

PUBLIC_PATHS = {"/", "/index.html", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/", "/assets/")


class TokenAuthMiddleware:
    """
    Pure ASGI middleware -- wraps any ASGI app with token auth.
    Compatible with FastAPI's app.add_middleware() call.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self, scope: dict, receive: Callable, send: Callable
    ) -> None:
        # Pass through non-HTTP/WS scopes (lifespan, etc.) untouched
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip: str = client[0] if client else ""

        # Always trust localhost -- the desktop app never needs a token
        if _is_localhost(client_ip):
            await self.app(scope, receive, send)
            return

        # Remote access globally disabled
        if not REMOTE_ACCESS_ENABLED:
            log.warning("Remote access attempt blocked (disabled): %s", client_ip)
            await _json_response(send, 403, {
                "error": "Remote access is not enabled.",
                "detail": "Set REMOTE_ACCESS_ENABLED=true in .env to allow remote connections.",
            })
            return

        # Public paths (the auth/PIN page itself must be reachable)
        path: str = scope.get("path", "")
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Extract token from the right place depending on connection type
        if scope["type"] == "websocket":
            query = scope.get("query_string", b"").decode("utf-8", errors="replace")
            token = _token_from_query(query)
        else:
            headers: dict[bytes, bytes] = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
            token = auth.removeprefix("Bearer ").strip()

        if not _valid_token(token):
            log.warning("Auth failure from %s path=%s", client_ip, path)
            await _json_response(send, 401, {
                "error": "Invalid or missing access token.",
                "detail": "Supply token as: Authorization: Bearer <token>",
            })
            return

        log.debug("Remote auth OK: %s %s", client_ip, path)
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _json_response(send: Callable, status: int, body: dict) -> None:
    """Send a minimal JSON HTTP response via the raw ASGI send callable."""
    encoded = json.dumps(body).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(encoded)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": encoded,
        "more_body": False,
    })


def _token_from_query(query: str) -> str:
    """Extract ?token=... from a raw query string."""
    for part in query.split("&"):
        if part.startswith("token="):
            return part[6:]
    return ""


def _is_localhost(ip: str) -> bool:
    return ip in {"127.0.0.1", "::1", "localhost"}


def _valid_token(provided: str) -> bool:
    if not ACCESS_TOKEN:
        log.error(
            "ACCESS_TOKEN is not set in .env -- all remote connections denied. "
            "Run scripts/Setup-Remote.ps1 to generate a token."
        )
        return False
    return provided == ACCESS_TOKEN