"""
AegisCoder -- token authentication middleware.

Design:
  - Localhost (127.0.0.1) connections are always trusted -- no token needed.
    This covers the desktop pywebview app.
  - All other connections (phone via Tailscale, another machine on LAN)
    must supply the ACCESS_TOKEN.
  - If REMOTE_ACCESS_ENABLED is False, all non-localhost connections are
    rejected outright regardless of token.

Token delivery:
  HTTP requests:   Authorization: Bearer <token>
  WebSocket:       ws://host:port/ws/chat?token=<token>
    (WebSocket connections cannot set custom headers in browsers, so the
    token travels as a query parameter and is promoted to auth by this
    middleware before the handler sees it.)

The mobile frontend stores the token in sessionStorage after the user
enters their PIN on the auth screen, and includes it automatically in
all subsequent requests.

See master plan -- mobile access section.
"""
import logging
from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from engine.config import ACCESS_TOKEN, REMOTE_ACCESS_ENABLED

log = logging.getLogger(__name__)

# Routes that are always public (no token required even from remote)
PUBLIC_PATHS = {"/", "/index.html", "/favicon.ico"}
# Paths that start with these prefixes are also public (static assets)
PUBLIC_PREFIXES = ("/static/", "/assets/")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that enforces token auth for non-localhost connections.
    """

    def __init__(self, app: Any):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        client_ip = _get_client_ip(request)

        # Always trust localhost
        if _is_localhost(client_ip):
            return await call_next(request)

        # Remote access disabled entirely
        if not REMOTE_ACCESS_ENABLED:
            log.warning("Remote access attempt blocked (disabled): %s", client_ip)
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Remote access is not enabled on this instance.",
                    "detail": "Set REMOTE_ACCESS_ENABLED=true in .env to allow remote connections.",
                },
            )

        # Public paths pass through (the auth/PIN page itself must be reachable)
        path = request.url.path
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        # WebSocket upgrade: token comes in as a query param
        if request.headers.get("upgrade", "").lower() == "websocket":
            token = request.query_params.get("token", "")
        else:
            # HTTP: token comes in Authorization header
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.removeprefix("Bearer ").strip()

        if not _valid_token(token):
            log.warning("Auth failure from %s (path=%s)", client_ip, path)
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Invalid or missing access token.",
                    "detail": "Include your token as: Authorization: Bearer <token>",
                },
            )

        log.debug("Remote request authenticated: %s %s", client_ip, path)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP, accounting for X-Forwarded-For if present.
    (Tailscale does not proxy, so request.client.host is correct in practice,
    but being explicit here avoids surprises.)
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _is_localhost(ip: str) -> bool:
    return ip in {"127.0.0.1", "::1", "localhost"}


def _valid_token(provided: str) -> bool:
    if not ACCESS_TOKEN:
        # No token configured -- deny all remote access for safety
        log.error(
            "ACCESS_TOKEN is not set in .env. "
            "Remote connections cannot be authenticated. "
            "Run scripts/Setup-Remote.ps1 to generate a token."
        )
        return False
    return provided == ACCESS_TOKEN