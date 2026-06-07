"""Reverse proxy for the Teltonika router WebUI.

Routes all browser requests through HA so the router is never
directly accessed from the browser. Works from the internet via
HA's HTTPS endpoint.

Proxy path:  /api/teltonika_proxy/{entry_id}/{path}
Router URL:  https://192.168.7.1/{path}

HTML responses have URLs rewritten so all links/assets/forms go
through the proxy. Cookies are forwarded transparently.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Headers never forwarded (hop-by-hop or HA-internal)
_SKIP_REQUEST_HEADERS = frozenset({
    "host", "content-length", "transfer-encoding", "connection",
    "keep-alive", "upgrade", "proxy-authenticate", "proxy-authorization",
    "te", "trailers",
    # HA auth — router has its own auth
    "authorization",
    "x-ingress-path",
})
_SKIP_RESPONSE_HEADERS = frozenset({
    "transfer-encoding", "connection", "keep-alive",
    "content-length",   # aiohttp recalculates
    # CSP blocks iframe embedding — remove it (we are the frame host)
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
})


# ---------------------------------------------------------------------------
# URL rewriting helpers
# ---------------------------------------------------------------------------

def _to_proxy(url: str, router_base: str, proxy_base: str) -> str:
    """Convert an absolute or root-relative router URL to a proxy URL."""
    if url.startswith(router_base):
        rest = url[len(router_base):].lstrip("/")
        return f"{proxy_base}{rest}"
    if url.startswith("/") and not url.startswith("//"):
        return f"{proxy_base}{url.lstrip('/')}"
    return url


def _rewrite_html(body: bytes, router_base: str, proxy_base: str) -> bytes:
    """
    Rewrite HTML so all resource loads and form submissions go through proxy.

    Strategy:
    1. Inject <base href=proxy_base> → fixes most relative URLs
    2. Rewrite remaining absolute/root-relative hrefs/srcs/actions
    3. Patch fetch()/XMLHttpRequest paths in inline <script> blocks
    """
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body

    # ── 1. Inject <base> tag ─────────────────────────────────────────────
    base_tag = f'<base href="{proxy_base}">'
    if re.search(r"<head\b", text, re.IGNORECASE):
        text = re.sub(
            r"(<head\b[^>]*>)",
            rf"\1{base_tag}",
            text, count=1, flags=re.IGNORECASE,
        )
    else:
        text = base_tag + text

    # ── 2. Rewrite attribute values ──────────────────────────────────────
    def _rewrite_attr(m: re.Match) -> str:
        attr, quote, url = m.group(1), m.group(2), m.group(3)
        return f'{attr}={quote}{_to_proxy(url, router_base, proxy_base)}{quote}'

    # href, src, action, data-src
    text = re.sub(
        r'(href|src|action|data-src)=(["\'])((?:https?://|/)[^"\']*)\2',
        _rewrite_attr, text, flags=re.IGNORECASE,
    )

    # ── 3. Patch JS fetch / XMLHttpRequest absolute paths ────────────────
    # Replace fetch("/api/...") with fetch("/api/teltonika_proxy/{id}/api/...")
    text = text.replace(
        f'"{router_base}/',
        f'"{proxy_base}',
    )
    # Root-relative API calls in JS strings: "/api/" → proxy
    text = re.sub(
        r"""([`'"])(/(?!api/teltonika_proxy)[a-zA-Z][^`'"?#\s]*)""",
        lambda m: f'{m.group(1)}{proxy_base}{m.group(2).lstrip("/")}',
        text,
    )

    return text.encode("utf-8")


def _rewrite_css(body: bytes, router_base: str, proxy_base: str) -> bytes:
    """Rewrite url(...) references in CSS files."""
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body

    def _fix_url(m: re.Match) -> str:
        url = m.group(1).strip("'\"")
        new_url = _to_proxy(url, router_base, proxy_base)
        return f"url({new_url})"

    text = re.sub(r"url\(([^)]+)\)", _fix_url, text)
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Proxy view
# ---------------------------------------------------------------------------

class TeltonikaProxyView(HomeAssistantView):
    """Reverse proxy — serves Teltonika WebUI through HA."""

    url  = "/api/teltonika_proxy/{entry_id}/{path:.*}"
    name = "api:teltonika_proxy"
    requires_auth = False   # Router has its own login page
    cors_allowed  = False

    # One proxy session shared across all requests (no SSL verify)
    _proxy_session: aiohttp.ClientSession | None = None

    @classmethod
    def _get_session(cls) -> aiohttp.ClientSession:
        if cls._proxy_session is None or cls._proxy_session.closed:
            connector = aiohttp.TCPConnector(ssl=False, limit=50)
            cls._proxy_session = aiohttp.ClientSession(connector=connector)
        return cls._proxy_session

    async def _proxy(
        self, request: web.Request, entry_id: str, path: str
    ) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            return web.Response(status=404, text="Teltonika integration not found")

        router_base: str = coordinator.router_base_url  # https://192.168.7.1
        proxy_base  = f"/api/teltonika_proxy/{entry_id}/"
        target_url  = f"{router_base}/{path}"
        if request.query_string:
            target_url += f"?{request.query_string}"

        # ── Forward request headers ───────────────────────────────────────
        from urllib.parse import urlparse
        fwd_headers: dict[str, str] = {}
        for k, v in request.headers.items():
            if k.lower() not in _SKIP_REQUEST_HEADERS:
                fwd_headers[k] = v
        fwd_headers["Host"] = urlparse(router_base).netloc

        # Forward cookies from browser (router session)
        if request.cookies:
            fwd_headers["Cookie"] = "; ".join(
                f"{k}={v}" for k, v in request.cookies.items()
            )

        body = await request.read()

        session = self._get_session()
        try:
            async with session.request(
                request.method,
                target_url,
                headers=fwd_headers,
                data=body or None,
                allow_redirects=False,
            ) as resp:
                content_type = resp.content_type or "application/octet-stream"
                charset      = resp.charset or "utf-8"
                resp_body    = await resp.read()

                # ── Rewrite content ───────────────────────────────────────
                if "html" in content_type:
                    resp_body = _rewrite_html(resp_body, router_base, proxy_base)
                elif "css" in content_type:
                    resp_body = _rewrite_css(resp_body, router_base, proxy_base)

                # ── Build response headers ────────────────────────────────
                out_headers: dict[str, str] = {}
                for k, v in resp.headers.items():
                    kl = k.lower()
                    if kl in _SKIP_RESPONSE_HEADERS:
                        continue
                    if kl == "location":
                        v = _to_proxy(v, router_base, proxy_base)
                    if kl == "set-cookie":
                        # Strip Domain/Secure so browser accepts on HA origin
                        v = re.sub(r";\s*Domain=[^;]+", "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*Secure",       "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*SameSite=[^;]+","", v, flags=re.IGNORECASE)
                    out_headers[k] = v

                return web.Response(
                    status=resp.status,
                    body=resp_body,
                    content_type=content_type,
                    headers=out_headers,
                )

        except aiohttp.ClientError as err:
            _LOGGER.error("Proxy %s %s: %s", request.method, target_url, err)
            return web.Response(status=502, text=f"Proxy error: {err}")

    async def get(self, request: web.Request, entry_id: str, path: str = "") -> web.Response:
        return await self._proxy(request, entry_id, path)

    async def post(self, request: web.Request, entry_id: str, path: str = "") -> web.Response:
        return await self._proxy(request, entry_id, path)

    async def put(self, request: web.Request, entry_id: str, path: str = "") -> web.Response:
        return await self._proxy(request, entry_id, path)

    async def delete(self, request: web.Request, entry_id: str, path: str = "") -> web.Response:
        return await self._proxy(request, entry_id, path)

    async def patch(self, request: web.Request, entry_id: str, path: str = "") -> web.Response:
        return await self._proxy(request, entry_id, path)
