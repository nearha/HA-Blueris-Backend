async def async_session_status(client):
    command = "log" + "in"
    sid = await client.async_login()
    return await client._post_json({"cmd": command, "session": sid})


def extract_profiles(payload, fallback):
    found = []
    seen = set()

    def add_item(value):
        if value is None:
            return
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return
            item_id = text
            item_name = text
        elif isinstance(value, dict):
            item_id = str(
                value.get("id")
                or value.get("value")
                or value.get("profile")
                or value.get("name")
                or value.get("display")
                or ""
            ).strip()
            item_name = str(
                value.get("name")
                or value.get("label")
                or value.get("display")
                or value.get("text")
                or item_id
            ).strip()
            if not item_id:
                return
        else:
            return
        key = item_id.lower()
        if key in seen:
            return
        seen.add(key)
        found.append({"id": item_id, "name": item_name or item_id})

    def scan(value, depth=0):
        if depth > 4:
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, (str, dict)):
                    add_item(item)
                else:
                    scan(item, depth + 1)
        elif isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).lower()
                if "profile" in key_text:
                    if isinstance(item, dict):
                        for sub_key, sub_item in item.items():
                            if isinstance(sub_item, dict):
                                merged = {"id": sub_key, **sub_item}
                                add_item(merged)
                            elif isinstance(sub_item, str):
                                add_item({"id": sub_key, "name": sub_item})
                            else:
                                add_item(sub_key)
                        if not item:
                            scan(item, depth + 1)
                    else:
                        scan(item, depth + 1)
                elif isinstance(item, (dict, list)):
                    scan(item, depth + 1)

    scan(payload.get("data", payload) if isinstance(payload, dict) else payload)

    # Keep fallback values too, but put discovered real values first.
    for item in fallback:
        add_item(item)

    return found or list(fallback)
