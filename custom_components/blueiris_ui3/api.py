import json
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, quote

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from .blueiris import BlueIrisError, groups_from_camlist
from .const import DATA_CLIENTS, DEFAULT_PROFILES, DOMAIN
from .ui3fix import async_session_status, extract_profiles

TOKEN_KEY = "tokens"
TOKEN_TTL = 600


def _urlencode(params):
    return urlencode(params, quote_via=quote)


def _query_with_session(raw_query, session):
    raw = raw_query or ""
    has_session = any(k == "session" for k, _ in parse_qsl(raw, keep_blank_values=True))
    if has_session:
        return raw
    sep = "&" if raw else ""
    return f"{raw}{sep}session={quote(session)}"


def _copy_headers(upstream):
    headers = {}
    for key in ("Content-Type", "Cache-Control", "Accept-Ranges", "ETag", "Last-Modified"):
        if upstream.headers.get(key):
            headers[key] = upstream.headers[key]
    return headers


def _request_headers(request):
    headers = {}
    for key in ("Accept", "Range", "If-Range", "Cache-Control", "Pragma", "User-Agent"):
        value = request.headers.get(key)
        if value:
            headers[key] = value
    return headers


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
    tokens = hass.data[DOMAIN].setdefault(TOKEN_KEY, {})
    now = time.time()
    for token, data in list(tokens.items()):
        if now > float(data.get("expires", 0)):
            tokens.pop(token, None)
    return tokens


def _cookie_name(entry_id):
    return "bi_ui3_" + entry_id.lower()


def _proxy_prefix(entry_id):
    return f"/api/blueiris_ui3/{entry_id}/proxy"


def _new_token(hass, entry_id):
    token = secrets.token_urlsafe(24)
    _tokens(hass)[token] = {"entry_id": entry_id, "expires": time.time() + TOKEN_TTL}
    return token


def _check_cookie(request, entry_id):
    token = request.cookies.get(_cookie_name(entry_id), "")
    data = _tokens(request.app["hass"]).get(token)
    if not data or data.get("entry_id") != entry_id:
        raise web.HTTPUnauthorized(reason="Invalid UI3 proxy token")


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
        client = _client(request.app["hass"], entry_id)
        try:
            status = await async_session_status(client)
            profiles = extract_profiles(status, DEFAULT_PROFILES)
        except Exception:
            profiles = DEFAULT_PROFILES
        default_profile = "1080p VBR^" if any(p.get("id") == "1080p VBR^" for p in profiles) else "1080p^"
        return self.json({"profiles": profiles, "default_profile": default_profile})


class Ui3UrlView(HomeAssistantView):
    url = "/api/blueiris_ui3/{entry_id}/ui3_url"
    name = "api:blueiris_ui3:ui3_url"
    requires_auth = True

    async def get(self, request, entry_id):
        _client(request.app["hass"], entry_id)
        token = _new_token(request.app["hass"], entry_id)
        q = request.query
        params = {"group": q.get("group", "index"), "p": q.get("profile", "1080p VBR^"), "timeout": str(int(q.get("timeout", 0)))}
        if q.get("maximize", "1") != "0":
            params["maximize"] = "1"
        response = web.json_response({"url": f"{_proxy_prefix(entry_id)}/ui3.htm?{_urlencode(params)}"})
        response.set_cookie(_cookie_name(entry_id), token, path=_proxy_prefix(entry_id), max_age=TOKEN_TTL, httponly=True, samesite="Lax")
        return response


class Ui3ProxyView(HomeAssistantView):
    url = "/api/blueiris_ui3/{entry_id}/proxy/{tail:.*}"
    name = "api:blueiris_ui3:proxy"
    requires_auth = False

    async def get(self, request, entry_id, tail):
        return await self._proxy(request, entry_id, tail)

    async def post(self, request, entry_id, tail):
        return await self._proxy(request, entry_id, tail)

    async def _proxy(self, request, entry_id, tail):
        _check_cookie(request, entry_id)
        client = _client(request.app["hass"], entry_id)
        path = tail or "ui3.htm"
        if path.lower() == "json" or path.lower().endswith("/json"):
            return await self._proxy_json(request, client)
        return await self._proxy_upstream(request, client, entry_id, path)

    async def _proxy_json(self, request, client):
        if request.method == "GET":
            body = dict(request.query)
            try:
                if body.get("cmd") == "login":
                    return web.json_response(await async_session_status(client))
                sid = await client.async_login()
                target = f"{client.base_url}/json?{_query_with_session(request.query_string, sid)}"
                upstream = await client._session.get(target, headers=_request_headers(request), timeout=None)
                data = await upstream.read()
            except BlueIrisError as err:
                raise web.HTTPBadGateway(reason=str(err)) from err
            except Exception as err:
                raise web.HTTPBadGateway(reason=str(err)) from err
            return web.Response(body=data, status=upstream.status, headers=_copy_headers(upstream))

        body = dict(request.query)
        text = await request.text()
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    body.update(parsed)
            except json.JSONDecodeError:
                body.update(dict(parse_qsl(text, keep_blank_values=True)))
        try:
            if body.get("cmd") == "login":
                return web.json_response(await async_session_status(client))
            body["session"] = await client.async_login()
            payload = await client._post_json(body)
        except BlueIrisError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        return web.json_response(payload)

    async def _proxy_upstream(self, request, client, entry_id, path):
        try:
            sid = await client.async_login()
        except BlueIrisError as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        target = f"{client.base_url}/{path.lstrip('/')}"
        qs = _query_with_session(request.query_string, sid)
        if qs:
            target += "?" + qs
        try:
            upstream = await client._session.request(
                request.method,
                target,
                data=await request.read() if request.can_read_body else None,
                headers=_request_headers(request),
                timeout=None,
            )
        except Exception as err:
            raise web.HTTPBadGateway(reason=str(err)) from err
        ctype = upstream.headers.get("Content-Type", "")
        if path.lower().endswith("ui3.htm") or "text/html" in ctype.lower():
            body = await upstream.read()
            headers = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
            try:
                text = body.decode("utf-8", "replace")
                virt = _proxy_prefix(entry_id).lstrip("/")
                replacement = f'var appPath_raw = "{virt}";'
                text, count = re.subn(r"var\s+appPath_raw\s*=\s*['\"][^'\"]*['\"]\s*;", replacement, text, count=1)
                if count == 0:
                    text = text.replace("</head>", f"<script>{replacement}</script></head>")
                body = text.encode("utf-8")
            except Exception:
                pass
            return web.Response(body=body, status=upstream.status, headers=headers)
        headers = _copy_headers(upstream)
        stream = web.StreamResponse(status=upstream.status, headers=headers)
        await stream.prepare(request)
        try:
            async for chunk in upstream.content.iter_chunked(65536):
                await stream.write(chunk)
        except ConnectionResetError:
            pass
        try:
            await stream.write_eof()
        except ConnectionResetError:
            pass
        return stream
