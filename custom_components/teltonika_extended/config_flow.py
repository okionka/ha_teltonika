"""Config flow for Teltonika Extended."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from teltasync import Teltasync

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_VERIFY_SSL, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_USERNAME, default="admin"): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_VERIFY_SSL, default=False): bool,
})


def _normalize_url(host: str) -> str:
    """Ensure host is a full RutOS API base URL, e.g. https://192.168.7.1/api"""
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    # RutOS REST API lives under /api — append if missing
    if not host.endswith("/api"):
        host = f"{host}/api"
    return host


class TeltonikaExtendedConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = _normalize_url(user_input[CONF_HOST])
            await self.async_set_unique_id(base_url)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(
                self.hass, verify_ssl=user_input[CONF_VERIFY_SSL]
            )
            client = Teltasync(
                base_url=base_url,
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                session=session,
                verify_ssl=user_input[CONF_VERIFY_SSL],
            )
            try:
                info = await client.get_system_info()
                title = (
                    getattr(info.static, "hostname", None)
                    or getattr(info.static, "device_name", None)
                    or base_url
                )
            except Exception as err:
                _LOGGER.error("Connection to %s failed: %s", base_url, err)
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"Teltonika {title}",
                    data={
                        CONF_HOST: base_url,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=SCHEMA, errors=errors
        )
