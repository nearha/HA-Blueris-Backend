from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import async_register_views
from .blueiris import BlueIrisClient, BlueIrisConfig
from .const import DATA_CLIENTS, DATA_VIEWS_REGISTERED, DOMAIN

async def async_setup_entry(hass, entry):
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_CLIENTS, {})
    if not hass.data[DOMAIN].get(DATA_VIEWS_REGISTERED):
        async_register_views(hass)
        hass.data[DOMAIN][DATA_VIEWS_REGISTERED] = True
    hass.data[DOMAIN][DATA_CLIENTS][entry.entry_id] = BlueIrisClient(
        async_get_clientsession(hass), BlueIrisConfig.from_entry_data(entry.data)
    )
    return True

async def async_unload_entry(hass, entry):
    hass.data.get(DOMAIN, {}).get(DATA_CLIENTS, {}).pop(entry.entry_id, None)
    return True
