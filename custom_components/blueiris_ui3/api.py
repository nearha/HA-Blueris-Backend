import json
import secrets
import time
from urllib.parse import parse_qsl, urlencode

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .blueiris import BlueIrisError, groups_from_camlist
from .const import DATA_CLIENTS, DEFAULT_PROFILES, DOMAIN

TOKEN_KEY = "tokens"
TOKEN_TTL = 3600


def async_register_views(hass):
    hass.http.register_view(EntriesView)
    hass.http.register_view(GroupsView)
    hass.http.register_view(ProfilesView)
    hass.http.register_view(Ui3UrlView)
    hass.http.register_view(Ui3ProxyView)


def _clients(hass):
    return hass.data[DOMAIN][DATA_CLIENTS]


def _client(hass, entry_id):
    client = _clients(hass).get(entry_id)
    if client is None:
        raise web.HTTPNotFound(reason="Blue Iris UI3 entry not found")
    return client


def _tokens(hass):
    return hass.data[DOMAIN].setdefault(TOKEN_KEY, {})


def _new_token(hass, entry_id):
    token = secrets.token_urlsafe(24)
    _tokens(hass)[token] = {"entry_id": entry_id, "expires": time.time() + TOKEN_TTL}
    return token


def _check_token(hass, entry_id, token):
    data = _tokens(hass).get(token)
    if not data or data.get("entry_id") != entry_id:
        raise web.HTTPUnauthorized(reason="Invalid proxy token")
    if time.time() > float(data.get("expires", 0)):
        _tokens(hass).pop(token, None)
        raise web.HTTPUnauthorized(reason="Expired proxy token")


class EntriesView(HomeAssistantView):
    url = "/api/blueiris_ui3/entries"
    name = "api:blueiris_ui3:entries"
    requires_auth = True

    async def get(self, request):
        return self.json({"entries": [{"entry_id": eid, "title": "Blue Iris UI3"} for eid in _clients(request.app["hass"])]})


class GroupsView(HomeAssistantView):
    url = "/api/blueiris_ui3/{entry_id}/groups"
    name = "api:blueiris_ui3:groups"
    requires_auth = True

    async def get(self, request, entry_id):
        try:
            camlist = await _client(request.app["hass"], entry_id).async_camlist()
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
        _client(request.app["hass"], entry_id)
        token = _new_token(request.app["hass"], entry_id)
        q = request.query
        params = {"group": q.get("group", "index"), "p": q.get("profile", "1080p^"), "timeout": str(int(q.get("timeout", 0)))}
        if q.get("maximize", "1") != "0":
            params["maximize"] = "1"
        return self.json({"url": f"/api/blueiris_ui3/{entry_id}/proxy/{token}/ui3.htm?{urlencode(params)}"})


class Ui3ProxyView(HomeAssistantView):
    url = "/api/blueiris_ui3/{entry_id}/proxy/{token}/{tail:.*}"
    name = "api:blueiris_ui3:proxy"
    requires_auth = False

    async def get(self, request, entry_id, token, tail):
        return await self._proxy(request, entry_id, token, tail)

    async def post(self, request, entry_id, token, tail):
        return await self._proxy(request, entry_id, token, tail)

    async def _proxy(self, request, entry_id, token, tail):
        hass = request.app["hass"]
        _check_token(hass, entry_id, token)
        client = _client(hass, entry_id)
        path = tail or "ui3.htm"
        if path.lower() == "json" or path.lower().endswith("/json"):
            return await self._proxy_json(request, client)
        return await self._proxy_file(request, client, path)

    async def _proxy_json(self, request, client):
        if request.method == "POST":
            try:
                body = json.loads(await request.text() or "{}")
            except json.JSONDecodeError:
                body = {}
        else:
            body = {"cmd": request.query.get("cmd", "")}
        try:
            if body.get("cmd") == "login":
                sid = await client.async_login()
                return web.json_response({"result": "success", "session": sid})
            body["session"] = await client.async_login()
            payload = await client._post_json(body)
        except BlueIrisError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        return web.json_response(payload)

    async def _proxy_file(self, request, client, path):
        try:
            sid = await client.async_login()
        except BlueIrisError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        q = dict(parse_qsl(request.query_string, keep_blank_values=True))
        q.setdefault("session", sid)
        target = f"{client.base_url}/{path.lstrip('/')}"
        if q:
            target += "?" + urlencode(q)
        try:
            upstream = await client._session.request(request.method, target)
            body = await upstream.read()
        except Exception as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        headers = {}
        if upstream.headers.get("Content-Type"):
            headers["Content-Type"] = upstream.headers["Content-Type"]
        if upstream.headers.get("Cache-Control"):
            headers["Cache-Control"] = upstream.headers["Cache-Control"]
        return web.Response(body=body, status=upstream.status, headers=headers)
