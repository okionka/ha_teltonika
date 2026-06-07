"""Reverse proxy for the Teltonika router WebUI.

Routes all browser requests through HA:
  /api/teltonika_proxy/{entry_id}/      → coordinator.router_base_url
  /api/teltonika_proxy/{entry_id}_ext/  → coordinator.external_url

Minimal rewriting strategy:
- Only <base href> is injected (handles most relative paths via browser)
- Redirects followed server-side (prevent redirect loops in browser)
- X-Frame-Options / CSP headers stripped
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

_SKIP_REQUEST = frozenset({
    "host", "content-length", "transfer-encoding", "connection",
    "keep-alive", "upgrade", "te", "trailers",
    "authorization", "x-ingress-path", "x-forwarded-for", "x-real-ip",
})
_SKIP_RESPONSE = frozenset({
    "transfer-encoding", "connection", "keep-alive",
    "content-length",           # recalculated
    "content-security-policy",  # blocks iframe embedding
    "x-frame-options",          # blocks iframe embedding
    "x-content-type-options",
})

_SESSION_KEY = f"{DOMAIN}_proxy_session"


async def get_proxy_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Return (or lazily create) the shared proxy aiohttp session."""
    session: aiohttp.ClientSession | None = hass.data.get(_SESSION_KEY)
    if session is None or session.closed:
        connector = aiohttp.TCPConnector(ssl=False, limit=20)
        session = aiohttp.ClientSession(connector=connector)
        hass.data[_SESSION_KEY] = session
    return session


def _inject_base(body: bytes, proxy_base: str) -> bytes:
    """
    Inject <base href=proxy_base> into HTML <head>.
    This single change makes the browser resolve all relative URLs through
    the proxy without any JS-breaking regex rewrites.
    """
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body

    base_tag = f'<base href="{proxy_base}">'
    # Insert after opening <head> tag (with or without attributes)
    patched, n = re.subn(
        r"(<head(?:\s[^>]*)?>)",
        rf"\1{base_tag}",
        text, count=1, flags=re.IGNORECASE,
    )
    if n == 0:
        # No <head> found — prepend
        patched = base_tag + text

    return patched.encode("utf-8")


class TeltonikaProxyView(HomeAssistantView):
    url           = "/api/teltonika_proxy/{entry_id}/{path:.*}"
    name          = "api:teltonika_proxy"
    requires_auth = False
    cors_allowed  = False

    async def _proxy(
        self, request: web.Request, entry_id: str, path: str
    ) -> web.Response:

        hass: HomeAssistant = request.app["hass"]

        # Resolve entry and target URL
        is_ext  = entry_id.endswith("_ext")
        real_id = entry_id[:-4] if is_ext else entry_id

        domain_data = hass.data.get(DOMAIN)
        if not isinstance(domain_data, dict):
            return web.Response(status=503, text="Teltonika integration not loaded")

        coordinator = domain_data.get(real_id)
        if coordinator is None:
            return web.Response(status=404,
                                text=f"Integration not found: {real_id}")

        router_base = (
            getattr(coordinator, "external_url",    "").rstrip("/")
            if is_ext else
            getattr(coordinator, "router_base_url", "").rstrip("/")
        )
        if not router_base:
            label = "External URL" if is_ext else "Router base URL"
            return web.Response(status=503, text=f"{label} not configured")

        proxy_base = f"/api/teltonika_proxy/{entry_id}/"
        target     = f"{router_base}/{path}" if path else router_base
        if request.query_string:
            target += f"?{request.query_string}"

        # Build forwarded headers
        from urllib.parse import urlparse
        fwd: dict[str, str] = {"Host": urlparse(router_base).netloc}
        for k, v in request.headers.items():
            if k.lower() not in _SKIP_REQUEST:
                fwd[k] = v
        if request.cookies:
            fwd["Cookie"] = "; ".join(
                f"{k}={v}" for k, v in request.cookies.items()
            )

        body = await request.read()
        session = await get_proxy_session(hass)

        try:
            async with session.request(
                request.method, target,
                headers=fwd,
                data=body or None,
                # Follow redirects server-side to prevent browser redirect loops
                allow_redirects=True,
                max_redirects=10,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp_body = await resp.read()

                # Inject <base href> into HTML responses only
                ct = resp.headers.get("Content-Type", "")
                if "html" in ct.lower():
                    resp_body = _inject_base(resp_body, proxy_base)

                # Build response headers (no Content-Type param — use header only)
                out: dict[str, str] = {}
                for k, v in resp.headers.items():
                    if k.lower() in _SKIP_RESPONSE:
                        continue
                    if k.lower() == "set-cookie":
                        v = re.sub(r";\s*Domain=[^;]+",   "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*Secure\b",        "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*SameSite=[^;]+",  "", v, flags=re.IGNORECASE)
                    out[k] = v

                return web.Response(
                    status=resp.status,
                    body=resp_body,
                    headers=out,
                )

        except aiohttp.ClientConnectorError as err:
            _LOGGER.warning("Proxy: cannot connect to %s: %s", router_base, err)
            return web.Response(
                status=502,
                content_type="text/html",
                text=f"<h2>Cannot connect to router</h2><p>{router_base}</p><p>{err}</p>",
            )
        except aiohttp.ClientError as err:
            _LOGGER.warning("Proxy client error %s: %s", target, err)
            return web.Response(status=502, text=f"Proxy error: {err}")
        except Exception:
            _LOGGER.exception("Proxy error: %s %s", request.method, target)
            return web.Response(status=500, text="Internal proxy error — see HA logs")

    async def get   (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def post  (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def put   (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def delete(self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def patch (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
