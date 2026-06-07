"""Config flow + Options flow for Teltonika Extended."""
from __future__ import annotations
import logging
from typing import Any

import voluptuous as vol
from teltasync import Teltasync

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_EXTERNAL_ICON, CONF_EXTERNAL_PANEL, CONF_EXTERNAL_TITLE, CONF_EXTERNAL_URL,
    CONF_MAX_BACKUPS, CONF_SIDEBAR_PANEL, CONF_VERIFY_SSL,
    DEFAULT_EXTERNAL_ICON, DEFAULT_EXTERNAL_PANEL,
    DEFAULT_MAX_BACKUPS, DEFAULT_SIDEBAR_PANEL, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_USERNAME, default="admin"): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_VERIFY_SSL, default=False): bool,
})


def _normalize_url(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    if not host.endswith("/api"):
        host = f"{host}/api"
    return host


class TeltonikaExtendedConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> "TeltonikaOptionsFlow":
        return TeltonikaOptionsFlow(config_entry)

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
                    data={**user_input, CONF_HOST: base_url},
                )

        return self.async_show_form(
            step_id="user", data_schema=SCHEMA, errors=errors
        )


class TeltonikaOptionsFlow(OptionsFlow):
    """Options flow — configure max backup versions."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_max = self._config_entry.options.get(
            CONF_MAX_BACKUPS, DEFAULT_MAX_BACKUPS
        )
        opts = self._config_entry.options
        current_sidebar  = opts.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL)
        current_ext      = opts.get(CONF_EXTERNAL_PANEL, DEFAULT_EXTERNAL_PANEL)
        current_ext_url  = opts.get(CONF_EXTERNAL_URL, "")
        current_ext_title= opts.get(CONF_EXTERNAL_TITLE, "")
        current_ext_icon = opts.get(CONF_EXTERNAL_ICON, DEFAULT_EXTERNAL_ICON)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                # Router WebUI proxy panel
                vol.Required(CONF_SIDEBAR_PANEL, default=current_sidebar): bool,
                # External URL panel
                vol.Required(CONF_EXTERNAL_PANEL, default=current_ext): bool,
                vol.Optional(CONF_EXTERNAL_URL,   default=current_ext_url):   str,
                vol.Optional(CONF_EXTERNAL_TITLE, default=current_ext_title): str,
                vol.Optional(CONF_EXTERNAL_ICON,  default=current_ext_icon):  str,
                # Backup
                vol.Required(CONF_MAX_BACKUPS, default=current_max): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=50)
                ),
            }),
        )
