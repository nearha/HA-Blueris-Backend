from __future__ import annotations

import json
import secrets
import time
from urllib.parse import urlencode

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .blueiris import BlueIrisError, groups_from_camlist
from .const import DATA_CLIENTS, DEFAULT_PROFILES, DOMAIN

TOKEN_KEY = "tokens"
TOKEN_TTL = 60 * 60


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
    token_data = _tokens(hass).get(token)
    if not token_data or token_data.get("entry_id") != entry_id:
        raise web.HTTPUnauthorized(reason="Invalid Blue Iris UI3 proxy token")
    if time.time() > float(token_data.get("expires", 0)):
        _tokens(hass).pop(token, None)
        raise web.HTTPUnauthorized(reason="Expired Blue Iris UI3 proxy token")


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
        _client(request.app["hass"], entry_id)
        hass = request.app["hass"]
        token = _new_token(hass, entry_id)
        query = request.query
        params = {
            "group": query.get("group", "index"),
            "p": query.get("profile", "1080p^"),
            "timeout": str(int(query.get("timeout", 0))),
        }
        if query.get("maximize", "1") != "0":
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

        if path.lower().endswith("json") or path.lower() == "json":
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

        cmd = body.get("cmd")
        try:
            if cmd == "login":
                session = await client.async_login()
                return web.json_response({"result": "success", "session": session})
            body["session"] = await client.async_login()
            payload = await client._post_json(body)
        except BlueIrisError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        return web.json_response(payload)

    async def _proxy_file(self, request, client, path):
        target = f"{client.base_url}/{path.lstrip('/')}"
        if request.query_string:
            target += f"?{request.query_string}"
        try:
            response = await client._session.request(request.method, target)
            body = await response.read()
        except Exception as err:
            raise web.HTTPBadGateway(reason=str(err)) from err

        headers = {}
        content_type = response.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        cache_control = response.headers.get("Cache-Control")
        if cache_control:
            headers["Cache-Control"] = cache_control
        return web.Response(body=body, status=response.status, headers=headers)
