from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .blueiris import BlueIrisError, groups_from_camlist
from .const import DATA_CLIENTS, DEFAULT_PROFILES, DOMAIN


def async_register_views(hass):
    hass.http.register_view(EntriesView)
    hass.http.register_view(GroupsView)
    hass.http.register_view(ProfilesView)
    hass.http.register_view(Ui3UrlView)


def _clients(hass):
    return hass.data[DOMAIN][DATA_CLIENTS]


def _client(hass, entry_id):
    client = _clients(hass).get(entry_id)
    if client is None:
        raise web.HTTPNotFound(reason="Blue Iris UI3 entry not found")
    return client


class EntriesView(HomeAssistantView):
    url = "/api/blueiris_ui3/entries"
    name = "api:blueiris_ui3:entries"
    requires_auth = True

    async def get(self, request):
        hass = request.app["hass"]
        entries = []
        for entry_id in _clients(hass):
            entries.append({"entry_id": entry_id, "title": "Blue Iris UI3"})
        return self.json({"entries": entries})


class GroupsView(HomeAssistantView):
    url = "/api/blueiris_ui3/{entry_id}/groups"
    name = "api:blueiris_ui3:groups"
    requires_auth = True

    async def get(self, request, entry_id):
        client = _client(request.app["hass"], entry_id)
        try:
            camlist = await client.async_camlist()
        except BlueIrisError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        return self.json({"groups": groups_from_camlist(camlist)})


class ProfilesView(HomeAssistantView):
    url = "/api/blueiris_ui3/{entry_id}/profiles"
    name = "api:blueiris_ui3:profiles"
    requires_auth = True

    async def get(self, request, entry_id):
        _client(request.app["hass"], entry_id)
        return self.json({"profiles": DEFAULT_PROFILES, "default_profile": "1080p^"})


class Ui3UrlView(HomeAssistantView):
    url = "/api/blueiris_ui3/{entry_id}/ui3_url"
    name = "api:blueiris_ui3:ui3_url"
    requires_auth = True

    async def get(self, request, entry_id):
        client = _client(request.app["hass"], entry_id)
        query = request.query
        try:
            url = await client.async_ui3_url(
                group=query.get("group", "index"),
                profile=query.get("profile", "1080p^"),
                timeout=int(query.get("timeout", 0)),
                maximize=query.get("maximize", "1") != "0",
            )
        except BlueIrisError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        return self.json({"url": url})
