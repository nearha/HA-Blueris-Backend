import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .blueiris import BlueIrisClient, BlueIrisConfig, BlueIrisError
from .const import DEFAULT_PORT, DOMAIN

class BlueIrisUi3ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            config = BlueIrisConfig(
                host=str(user_input[CONF_HOST]).strip(),
                port=int(user_input.get(CONF_PORT) or DEFAULT_PORT),
                ssl=bool(user_input.get(CONF_SSL, False)),
                username=str(user_input.get(CONF_USERNAME) or ""),
                password=str(user_input.get(CONF_PASSWORD) or ""),
            )
            client = BlueIrisClient(async_get_clientsession(self.hass), config)
            try:
                await client.async_camlist()
            except BlueIrisError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{config.host}:{config.port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Blue Iris UI3", data=dict(user_input))

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_SSL, default=False): bool,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )
