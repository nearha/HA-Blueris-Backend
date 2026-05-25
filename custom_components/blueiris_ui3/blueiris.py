from dataclasses import dataclass
from hashlib import md5
import json
import time
from urllib.parse import urlencode

from aiohttp import ClientError

from .const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME

class BlueIrisError(Exception):
    pass

@dataclass
class BlueIrisConfig:
    host: str
    port: int
    ssl: bool
    username: str
    password: str

    @property
    def base_url(self):
        scheme = "https" if self.ssl else "http"
        default_port = 443 if self.ssl else 80
        port = "" if self.port == default_port else f":{self.port}"
        return f"{scheme}://{self.host}{port}"

    @classmethod
    def from_entry_data(cls, data):
        return cls(
            host=str(data[CONF_HOST]).strip(),
            port=int(data.get(CONF_PORT) or 80),
            ssl=bool(data.get(CONF_SSL, False)),
            username=str(data.get(CONF_USERNAME) or ""),
            password=str(data.get(CONF_PASSWORD) or ""),
        )

class BlueIrisClient:
    def __init__(self, session, config):
        self._session = session
        self._config = config
        self._bi_session = ""
        self._login_at = 0.0

    @property
    def base_url(self):
        return self._config.base_url

    async def async_login(self, force=False):
        if self._bi_session and not force and time.time() - self._login_at < 20 * 60:
            return self._bi_session
        challenge = await self._post_json({"cmd": "login"})
        challenge_session = self._extract_session(challenge)
        if not challenge_session:
            raise BlueIrisError("Blue Iris did not return a login session")
        response = md5(f"{self._config.username}:{challenge_session}:{self._config.password}".encode("utf-8")).hexdigest()
        auth = await self._post_json({"cmd": "login", "session": challenge_session, "response": response})
        if auth.get("result") != "success":
            raise BlueIrisError(str(auth.get("reason") or auth.get("data") or "login failed"))
        self._bi_session = self._extract_session(auth) or challenge_session
        self._login_at = time.time()
        return self._bi_session

    async def async_command(self, cmd, payload=None):
        body = {"cmd": cmd, **(payload or {})}
        body["session"] = await self.async_login()
        result = await self._post_json(body)
        if result.get("result") == "fail" and "session" in str(result).lower():
            body["session"] = await self.async_login(force=True)
            result = await self._post_json(body)
        if result.get("result") == "fail":
            raise BlueIrisError(str(result.get("reason") or result.get("data") or f"{cmd} failed"))
        return result

    async def async_camlist(self):
        return await self.async_command("camlist")

    async def async_ui3_url(self, group="index", profile="1080p^", timeout=0, maximize=True):
        session = await self.async_login()
        query = {"session": session, "group": group or "index", "p": profile or "1080p^", "timeout": str(timeout)}
        if maximize:
            query["maximize"] = "1"
        return f"{self.base_url}/ui3.htm?{urlencode(query)}"

    async def _post_json(self, body):
        try:
            resp = await self._session.post(
                f"{self.base_url}/json",
                data=json.dumps(body),
                headers={"Content-Type": "text/plain"},
                timeout=15,
            )
            text = await resp.text()
        except ClientError as err:
            raise BlueIrisError(str(err)) from err
        except TimeoutError as err:
            raise BlueIrisError("timeout connecting to Blue Iris") from err
        if resp.status >= 400:
            raise BlueIrisError(f"HTTP {resp.status}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise BlueIrisError("Blue Iris returned invalid JSON") from err

    @staticmethod
    def _extract_session(payload):
        session = payload.get("session")
        if isinstance(session, str):
            return session
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("session"), str):
            return data["session"]
        return ""

def _group_display_name(group_id, raw_name):
    name = str(raw_name or group_id).strip()
    if name.startswith("+"):
        name = name[1:].strip()
    if str(group_id).strip().lower() == "index" or name.lower() in {"all cameras", "all"}:
        return "Todas"
    return name or group_id


def groups_from_camlist(payload):
    groups = []
    seen = set()
    data = payload.get("data")
    if not isinstance(data, list):
        return [{"id": "index", "name": "Todas"}]
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("group"), list):
            continue
        group_id = str(item.get("optionValue") or item.get("id") or "").strip()
        if not group_id:
            continue
        key = group_id.lower()
        if key in seen:
            continue
        seen.add(key)
        name = _group_display_name(group_id, item.get("optionDisplay") or item.get("name") or group_id)
        groups.append({"id": group_id, "name": name})
    if "index" not in seen:
        groups.insert(0, {"id": "index", "name": "Todas"})
    return groups
