"""Reverse proxy for the Teltonika router WebUI.

Routes all browser requests through HA:
  /api/teltonika_proxy/{entry_id}/      → coordinator.router_base_url
  /api/teltonika_proxy/{entry_id}_ext/  → coordinator.external_url

Both strip X-Frame-Options / CSP so the WebUI can be embedded in HA.
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
    "authorization",
    "x-ingress-path", "x-forwarded-for", "x-real-ip",
})
_SKIP_RESPONSE = frozenset({
    "transfer-encoding", "connection", "keep-alive",
    "content-length",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
})

_SESSION_KEY = f"{DOMAIN}_proxy_session"


async def get_proxy_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Return (or lazily create) the shared proxy aiohttp session."""
    session: aiohttp.ClientSession | None = hass.data.get(_SESSION_KEY)
    if session is None or session.closed:
        # Must be created inside async context (event loop must be running)
        connector = aiohttp.TCPConnector(ssl=False, limit=20)
        session = aiohttp.ClientSession(connector=connector)
        hass.data[_SESSION_KEY] = session
        _LOGGER.debug("Created new proxy aiohttp session")
    return session


# ---------------------------------------------------------------------------
# URL rewriting
# ---------------------------------------------------------------------------

def _proxy_url(raw: str, router_base: str, proxy_base: str) -> str:
    if raw.startswith(router_base):
        return proxy_base + raw[len(router_base):].lstrip("/")
    if raw.startswith("/") and not raw.startswith("//") and "teltonika_proxy" not in raw:
        return proxy_base + raw.lstrip("/")
    return raw


def _rewrite_html(body: bytes, router_base: str, proxy_base: str) -> bytes:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body

    # Inject <base> tag
    base_tag = f'<base href="{proxy_base}">'
    text = re.sub(r"(<head(?:\s[^>]*)?>)", rf"\1{base_tag}", text,
                  count=1, flags=re.IGNORECASE)

    # Rewrite href/src/action attributes
    def _attr(m: re.Match) -> str:
        return f'{m.group(1)}={m.group(2)}{_proxy_url(m.group(3), router_base, proxy_base)}{m.group(2)}'

    text = re.sub(
        r'(href|src|action|data-src)=(["\'])((?:https?://|/)[^"\']*)\2',
        _attr, text, flags=re.IGNORECASE,
    )

    # Rewrite absolute router URLs in JS strings
    safe_base = re.escape(router_base)
    text = re.sub(
        rf'(["\']){safe_base}(/[^"\']*)',
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
        r'url\(([\'"]?)(/[^)\'"]*)\1\)',
        lambda m: f'url({m.group(1)}{proxy_base}{m.group(2).lstrip("/")}{m.group(1)})',
        text,
    )
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# Proxy view
# ---------------------------------------------------------------------------

class TeltonikaProxyView(HomeAssistantView):
    url          = "/api/teltonika_proxy/{entry_id}/{path:.*}"
    name         = "api:teltonika_proxy"
    requires_auth = False
    cors_allowed  = False

    async def _proxy(self, request: web.Request, entry_id: str, path: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

        # Resolve entry — "_ext" suffix → use external_url
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
            getattr(coordinator, "external_url", "").rstrip("/")
            if is_ext else
            getattr(coordinator, "router_base_url", "").rstrip("/")
        )
        if not router_base:
            label = "External URL" if is_ext else "Router base URL"
            return web.Response(status=503,
                                text=f"{label} not configured")

        proxy_base = f"/api/teltonika_proxy/{entry_id}/"
        target     = f"{router_base}/{path}" if path else router_base
        if request.query_string:
            target += f"?{request.query_string}"

        # Forward headers
        from urllib.parse import urlparse
        fwd: dict[str, str] = {"Host": urlparse(router_base).netloc}
        for k, v in request.headers.items():
            if k.lower() not in _SKIP_REQUEST:
                fwd[k] = v
        if request.cookies:
            fwd["Cookie"] = "; ".join(f"{k}={v}" for k, v in request.cookies.items())

        body = await request.read()

        session = await get_proxy_session(hass)
        try:
            async with session.request(
                request.method, target,
                headers=fwd,
                data=body or None,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp_body = await resp.read()
                ct_lower  = (resp.headers.get("Content-Type") or "").lower()

                if "html" in ct_lower:
                    resp_body = _rewrite_html(resp_body, router_base, proxy_base)
                elif "css" in ct_lower:
                    resp_body = _rewrite_css(resp_body, router_base, proxy_base)

                out: dict[str, str] = {}
                for k, v in resp.headers.items():
                    kl = k.lower()
                    if kl in _SKIP_RESPONSE:
                        continue
                    if kl == "location":
                        v = _proxy_url(v, router_base, proxy_base)
                    if kl == "set-cookie":
                        v = re.sub(r";\s*Domain=[^;]+",   "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*Secure\b",        "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*SameSite=[^;]+",  "", v, flags=re.IGNORECASE)
                    out[k] = v

                # Never pass content_type= param — Content-Type lives in out headers
                return web.Response(status=resp.status, body=resp_body, headers=out)

        except aiohttp.ClientConnectorError as err:
            _LOGGER.warning("Proxy: cannot connect to %s: %s", router_base, err)
            return web.Response(status=502,
                                text=f"Cannot connect to router ({router_base})")
        except aiohttp.ClientError as err:
            _LOGGER.warning("Proxy client error for %s: %s", target, err)
            return web.Response(status=502, text=f"Proxy error: {err}")
        except Exception:
            _LOGGER.exception("Proxy unexpected error: %s %s", request.method, target)
            return web.Response(status=500, text="Internal proxy error — see HA logs")

    async def get   (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def post  (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def put   (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def delete(self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def patch (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
