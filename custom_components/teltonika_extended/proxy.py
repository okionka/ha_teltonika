"""Reverse proxy for the Teltonika router WebUI.

Routes all browser requests through HA:
  Browser → /api/teltonika_proxy/{entry_id}/{path} → https://192.168.7.1/{path}

Rewrites HTML/CSS so all links stay within the proxy path.
Cookies are forwarded transparently (router session cookies).
"""
from __future__ import annotations

import logging
import re

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# HA/HTTP headers that must not be forwarded to the router
_SKIP_REQUEST = frozenset({
    "host", "content-length", "transfer-encoding", "connection",
    "keep-alive", "upgrade", "te", "trailers",
    "authorization",          # HA token — router uses its own auth
    "x-ingress-path",
    "x-forwarded-for",
    "x-real-ip",
})

# Router headers that must not be returned to the browser
_SKIP_RESPONSE = frozenset({
    "transfer-encoding", "connection", "keep-alive",
    "content-length",             # recalculated by aiohttp
    "content-security-policy",    # would block the iFrame
    "x-frame-options",            # would block the iFrame
    "x-content-type-options",
})

# ---------------------------------------------------------------------------
# Session management — one aiohttp session per HA instance (stored in hass.data)
# ---------------------------------------------------------------------------
_SESSION_KEY = f"{DOMAIN}_proxy_session"


def _get_proxy_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Return (or create) the shared proxy session with SSL verification off."""
    session = hass.data.get(_SESSION_KEY)
    if session is None or session.closed:
        connector = aiohttp.TCPConnector(ssl=False, limit=20)
        session = aiohttp.ClientSession(connector=connector)
        hass.data[_SESSION_KEY] = session
    return session


# ---------------------------------------------------------------------------
# HTML / CSS URL rewriting
# ---------------------------------------------------------------------------

def _proxy_url(raw: str, router_base: str, proxy_base: str) -> str:
    """Rewrite a single URL to go through the proxy."""
    if raw.startswith(router_base):
        rest = raw[len(router_base):].lstrip("/")
        return f"{proxy_base}{rest}"
    if raw.startswith("/") and not raw.startswith("//") and "teltonika_proxy" not in raw:
        return f"{proxy_base}{raw.lstrip('/')}"
    return raw


def _rewrite_html(body: bytes, router_base: str, proxy_base: str) -> bytes:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body

    # 1. Inject <base> tag so relative paths resolve correctly
    base_tag = f'<base href="{proxy_base}">'
    text = re.sub(
        r"(<head(?:\s[^>]*)?>)",
        rf"\1{base_tag}",
        text, count=1, flags=re.IGNORECASE,
    )

    # 2. Rewrite href / src / action attribute values
    def _attr(m: re.Match) -> str:
        attr, q, url = m.group(1), m.group(2), m.group(3)
        return f'{attr}={q}{_proxy_url(url, router_base, proxy_base)}{q}'

    text = re.sub(
        r'((?:href|src|action|data-src))=(["\'])((?:https?://|/)[^"\']*)\2',
        _attr, text, flags=re.IGNORECASE,
    )

    # 3. Rewrite absolute router base URL in JS strings
    escaped = router_base.replace(".", r"\.")
    text = re.sub(
        rf'(["\']){escaped}(/[^"\']*)',
        lambda m: f'{m.group(1)}{proxy_base}{m.group(2).lstrip("/")}',
        text,
    )

    return text.encode("utf-8")


def _rewrite_css(body: bytes, router_base: str, proxy_base: str) -> bytes:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body
    text = re.sub(
        r"url\((['\"]?)(/[^)'\"]*)(['\"]?)\)",
        lambda m: f"url({m.group(1)}{proxy_base}{m.group(2).lstrip('/')}{m.group(3)})",
        text,
    )
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Proxy view
# ---------------------------------------------------------------------------

class TeltonikaProxyView(HomeAssistantView):
    """HTTP view that reverse-proxies the Teltonika router WebUI."""

    url          = "/api/teltonika_proxy/{entry_id}/{path:.*}"
    name         = "api:teltonika_proxy"
    requires_auth = False   # Router has its own login page
    cors_allowed  = False

    async def _proxy(
        self, request: web.Request, entry_id: str, path: str
    ) -> web.Response:

        hass: HomeAssistant = request.app["hass"]

        # ── Resolve coordinator ────────────────────────────────────────────
        domain_data = hass.data.get(DOMAIN)
        if not domain_data:
            return web.Response(status=503, text="Teltonika integration not loaded")

        coordinator = domain_data.get(entry_id)
        if coordinator is None:
            return web.Response(
                status=404,
                text=f"No Teltonika integration found for id={entry_id}",
            )

        router_base: str = getattr(coordinator, "router_base_url", "")
        if not router_base:
            return web.Response(status=503, text="Router base URL not set")

        proxy_base = f"/api/teltonika_proxy/{entry_id}/"

        # ── Build target URL ───────────────────────────────────────────────
        target = f"{router_base}/{path}" if path else router_base
        if request.query_string:
            target += f"?{request.query_string}"

        # ── Forward headers ────────────────────────────────────────────────
        from urllib.parse import urlparse
        netloc = urlparse(router_base).netloc

        fwd_headers: dict[str, str] = {"Host": netloc}
        for k, v in request.headers.items():
            if k.lower() not in _SKIP_REQUEST:
                fwd_headers[k] = v

        # Forward router session cookies from the browser
        if request.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in request.cookies.items())
            fwd_headers["Cookie"] = cookie_str

        body = await request.read()

        # ── Proxy the request ──────────────────────────────────────────────
        session = _get_proxy_session(hass)
        try:
            async with session.request(
                request.method,
                target,
                headers=fwd_headers,
                data=body if body else None,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                content_type = resp.content_type or "application/octet-stream"
                resp_body    = await resp.read()

                # Rewrite content
                if "html" in content_type:
                    resp_body = _rewrite_html(resp_body, router_base, proxy_base)
                elif "css" in content_type:
                    resp_body = _rewrite_css(resp_body, router_base, proxy_base)

                # Build response headers
                out_headers: dict[str, str] = {}
                for k, v in resp.headers.items():
                    kl = k.lower()
                    if kl in _SKIP_RESPONSE:
                        continue
                    if kl == "location":
                        v = _proxy_url(v, router_base, proxy_base)
                    if kl == "set-cookie":
                        # Strip router domain so cookies work on HA origin
                        v = re.sub(r";\s*Domain=[^;]+",  "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*Secure\b",       "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*SameSite=[^;]+", "", v, flags=re.IGNORECASE)
                    out_headers[k] = v

                return web.Response(
                    status=resp.status,
                    body=resp_body,
                    content_type=content_type,
                    headers=out_headers,
                )

        except aiohttp.ClientConnectorError as err:
            _LOGGER.error("Cannot connect to router at %s: %s", router_base, err)
            return web.Response(
                status=502,
                text=f"Cannot connect to router ({router_base}): {err}",
            )
        except aiohttp.ClientError as err:
            _LOGGER.error("Proxy request failed: %s", err)
            return web.Response(status=502, text=f"Proxy error: {err}")
        except Exception as err:
            _LOGGER.exception("Unexpected proxy error for %s %s", request.method, target)
            return web.Response(status=500, text=f"Internal proxy error: {err}")

    # One handler per HTTP method
    async def get   (self, req, entry_id, path=""): return await self._proxy(req, entry_id, path)
    async def post  (self, req, entry_id, path=""): return await self._proxy(req, entry_id, path)
    async def put   (self, req, entry_id, path=""): return await self._proxy(req, entry_id, path)
    async def delete(self, req, entry_id, path=""): return await self._proxy(req, entry_id, path)
    async def patch (self, req, entry_id, path=""): return await self._proxy(req, entry_id, path)
