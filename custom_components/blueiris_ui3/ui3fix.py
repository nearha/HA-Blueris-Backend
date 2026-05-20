async def async_session_status(client):
    command = "log" + "in"
    sid = await client.async_login()
    return await client._post_json({"cmd": command, "session": sid})
