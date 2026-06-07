"""Reverse proxy for the Teltonika router WebUI.

Routes all browser requests through HA:
  /api/teltonika_proxy/{entry_id}/      → coordinator.router_base_url
  /api/teltonika_proxy/{entry_id}_ext/  → coordinator.external_url

Rewriting strategy:
- Follow redirects server-side (no browser redirect loops)
- Inject <base href> for relative paths
- Rewrite root-relative paths in HTML ATTRIBUTES only (src=, href=, action=)
- Do NOT touch JavaScript code (causes SPA routing loops)
- Strip X-Frame-Options / CSP headers
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
    "transfer-encoding", "connection", "keep-alive", "content-length",
    "content-security-policy", "x-frame-options", "x-content-type-options",
})

_SESSION_KEY = f"{DOMAIN}_proxy_session"

# Injected before any router JS to prevent iframe-escape loops.
# Makes the page believe it is NOT running inside an iframe.
_ANTI_IFRAME_SCRIPT = """<script>
(function(){try{
  // Spoof window.top/parent/frameElement so router JS thinks it's top-level
  var _w=window;
  function _self(){return _w;}
  Object.defineProperty(_w,'top',         {configurable:true,get:_self});
  Object.defineProperty(_w,'parent',       {configurable:true,get:_self});
  Object.defineProperty(_w,'frameElement', {configurable:true,get:function(){return null;}});
  // Block window.top.location escape attempts
  var _desc=Object.getOwnPropertyDescriptor(Location.prototype,'href');
  if(_desc&&_desc.set){
    Object.defineProperty(Location.prototype,'href',{
      get:_desc.get,
      set:function(u){
        // Let relative paths pass (they resolve via <base href> through proxy)
        _desc.set.call(this,u);
      }
    });
  }
}catch(e){}})();
</script>"""

# HTML attributes whose values are URLs
_URL_ATTRS = re.compile(
    r"""((?:src|href|action|data-src|data-href|data-url|poster|formaction)
         \s*=\s*)
        (["\'])          # opening quote
        (/[^"\'>\s]*)   # root-relative path starting with /
        \2               # closing quote
    """,
    re.VERBOSE | re.IGNORECASE,
)

# CSS url() with root-relative path
_CSS_URL = re.compile(r"""url\((['"]?)(/[^)'"\s]+)\1\)""")


async def get_proxy_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    session: aiohttp.ClientSession | None = hass.data.get(_SESSION_KEY)
    if session is None or session.closed:
        connector = aiohttp.TCPConnector(ssl=False, limit=20)
        session = aiohttp.ClientSession(connector=connector)
        hass.data[_SESSION_KEY] = session
    return session


def _rewrite_html(body: bytes, router_base: str, proxy_base: str) -> bytes:
    """
    Rewrite HTML so assets and links go through the proxy.

    1. Inject <base href> for relative paths
    2. Rewrite root-relative paths in HTML attributes only
       (/assets/app.js → /api/teltonika_proxy/{id}/assets/app.js)
    3. Do NOT touch <script> contents — JS path logic must stay intact
    """
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body

    proxy_base = proxy_base.rstrip("/") + "/"

    # ── 1. Inject <base href> + anti-iframe script ───────────────────────
    base_tag = f'<base href="{proxy_base}">'
    inject   = base_tag + _ANTI_IFRAME_SCRIPT
    text, n = re.subn(
        r"(<head(?:\s[^>]*)?>)", rf"\1{inject}",
        text, count=1, flags=re.IGNORECASE,
    )
    if n == 0:
        text = inject + text

    # ── 2. Rewrite HTML attribute values — protect only inline JS content ──
    # Split into: [non-script, full-script-block, non-script, ...]
    # For each script block: rewrite opening tag attrs, leave JS content alone.
    segments = re.split(r"(<script[^>]*>.*?</script>)", text,
                        flags=re.IGNORECASE | re.DOTALL)

    def _fix_attr(m: re.Match) -> str:
        attr_eq, q, url = m.group(1), m.group(2), m.group(3)
        if url.startswith("//") or "teltonika_proxy" in url:
            return m.group(0)
        return f"{attr_eq}{q}{proxy_base}{url.lstrip('/')}{q}"

    def _fix_script_block(block: str) -> str:
        """Rewrite <script src="..."> opening tag, leave JS content untouched."""
        m2 = re.match(
            r"(<script[^>]*>)(.*?)(</script>)",
            block, flags=re.IGNORECASE | re.DOTALL,
        )
        if m2:
            opening = _URL_ATTRS.sub(_fix_attr, m2.group(1))
            return opening + m2.group(2) + m2.group(3)
        return block

    rewritten = []
    for i, seg in enumerate(segments):
        if i % 2 == 1:
            rewritten.append(_fix_script_block(seg))   # rewrite tag, not content
        else:
            rewritten.append(_URL_ATTRS.sub(_fix_attr, seg))

    return "".join(rewritten).encode("utf-8")


def _rewrite_css(body: bytes, proxy_base: str) -> bytes:
    """Rewrite root-relative url() in CSS."""
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return body

    proxy_base = proxy_base.rstrip("/") + "/"

    def _fix(m: re.Match) -> str:
        q, url = m.group(1), m.group(2)
        if "teltonika_proxy" in url:
            return m.group(0)
        return f"url({q}{proxy_base}{url.lstrip('/')}{q})"

    return _CSS_URL.sub(_fix, text).encode("utf-8")


class TeltonikaProxyView(HomeAssistantView):
    url           = "/api/teltonika_proxy/{entry_id}/{path:.*}"
    name          = "api:teltonika_proxy"
    requires_auth = False
    cors_allowed  = False

    async def _proxy(self, request: web.Request, entry_id: str, path: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

        is_ext  = entry_id.endswith("_ext")
        real_id = entry_id[:-4] if is_ext else entry_id

        domain_data = hass.data.get(DOMAIN)
        if not isinstance(domain_data, dict):
            return web.Response(status=503, text="Teltonika integration not loaded")

        coordinator = domain_data.get(real_id)
        if coordinator is None:
            return web.Response(status=404, text=f"Integration not found: {real_id}")

        router_base = (
            getattr(coordinator, "external_url",    "").rstrip("/")
            if is_ext else
            getattr(coordinator, "router_base_url", "").rstrip("/")
        )
        if not router_base:
            return web.Response(
                status=503,
                text=f"{'External URL' if is_ext else 'Router URL'} not configured",
            )

        proxy_base = f"/api/teltonika_proxy/{entry_id}/"
        target     = f"{router_base}/{path}" if path else router_base
        if request.query_string:
            target += f"?{request.query_string}"

        from urllib.parse import urlparse
        fwd: dict[str, str] = {"Host": urlparse(router_base).netloc}
        for k, v in request.headers.items():
            if k.lower() not in _SKIP_REQUEST:
                fwd[k] = v
        if request.cookies:
            fwd["Cookie"] = "; ".join(f"{k}={v}" for k, v in request.cookies.items())

        body    = await request.read()
        session = await get_proxy_session(hass)

        try:
            async with session.request(
                request.method, target,
                headers=fwd,
                data=body or None,
                allow_redirects=True,
                max_redirects=10,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp_body = await resp.read()
                ct = resp.headers.get("Content-Type", "").lower()

                if "html" in ct:
                    resp_body = _rewrite_html(resp_body, router_base, proxy_base)
                elif "css" in ct:
                    resp_body = _rewrite_css(resp_body, proxy_base)

                out: dict[str, str] = {}
                for k, v in resp.headers.items():
                    kl = k.lower()
                    if kl in _SKIP_RESPONSE:
                        continue
                    if kl == "set-cookie":
                        v = re.sub(r";\s*Domain=[^;]+",   "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*Secure\b",        "", v, flags=re.IGNORECASE)
                        v = re.sub(r";\s*SameSite=[^;]+",  "", v, flags=re.IGNORECASE)
                    out[k] = v

                return web.Response(status=resp.status, body=resp_body, headers=out)

        except aiohttp.ClientConnectorError as err:
            _LOGGER.warning("Proxy: cannot connect to %s: %s", router_base, err)
            return web.Response(
                status=502,
                content_type="text/html",
                text=f"<h2>Cannot connect to router</h2><p>{router_base}</p><p>{err}</p>",
            )
        except aiohttp.ClientError as err:
            _LOGGER.warning("Proxy error %s: %s", target, err)
            return web.Response(status=502, text=f"Proxy error: {err}")
        except Exception:
            _LOGGER.exception("Proxy unexpected error: %s %s", request.method, target)
            return web.Response(status=500, text="Internal proxy error — see HA logs")

    async def get   (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def post  (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def put   (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def delete(self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
    async def patch (self, r, entry_id, path=""): return await self._proxy(r, entry_id, path)
