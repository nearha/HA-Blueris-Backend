import re


async def async_session_status(client):
    command = "log" + "in"
    sid = await client.async_login()
    return await client._post_json({"cmd": command, "session": sid})


_VIDEO_PROFILE_RE = re.compile(r"^(?:\d{3,4}p|4k)(?:\s+vbr)?\^?$", re.IGNORECASE)
_BAD_PROFILE_WORDS = {
    "active",
    "inactive",
    "profile",
    "profiles",
    "default",
    "none",
    "null",
    "true",
    "false",
}


def _clean_profile_id(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().endswith((".wav", ".mp3", ".ogg", ".aac", ".flac", ".m4a")):
        return ""
    if text.lower() in _BAD_PROFILE_WORDS:
        return ""
    return text


def _looks_like_video_profile(value):
    text = _clean_profile_id(value)
    return bool(text and _VIDEO_PROFILE_RE.match(text))


def extract_profiles(payload, fallback):
    found = []
    seen = set()

    def add_item(value):
        if value is None:
            return
        if isinstance(value, str):
            item_id = _clean_profile_id(value)
            item_name = item_id
        elif isinstance(value, dict):
            item_id = _clean_profile_id(
                value.get("id")
                or value.get("value")
                or value.get("profile")
                or value.get("name")
                or value.get("display")
                or ""
            )
            item_name = str(
                value.get("name")
                or value.get("label")
                or value.get("display")
                or value.get("text")
                or item_id
            ).strip()
        else:
            return
        if not _looks_like_video_profile(item_id):
            return
        key = item_id.lower()
        if key in seen:
            return
        seen.add(key)
        found.append({"id": item_id, "name": item_name or item_id})

    def scan(value, depth=0):
        if depth > 5:
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, (str, dict)):
                    add_item(item)
                elif isinstance(item, list):
                    scan(item, depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).lower()
                if "profile" in key_text or "stream" in key_text or _looks_like_video_profile(key):
                    if _looks_like_video_profile(key):
                        if isinstance(item, dict):
                            add_item({"id": key, **item})
                        elif isinstance(item, str):
                            add_item({"id": key, "name": item})
                        else:
                            add_item(key)
                    scan(item, depth + 1)
                elif isinstance(item, (dict, list)):
                    scan(item, depth + 1)

    scan(payload.get("data", payload) if isinstance(payload, dict) else payload)

    for item in fallback:
        add_item(item)

    return found or list(fallback)
