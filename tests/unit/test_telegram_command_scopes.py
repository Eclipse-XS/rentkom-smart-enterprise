from unittest.mock import patch

from scripts.configure_telegram_commands import ADMIN_COMMANDS, USER_COMMANDS, configure_commands


def test_command_scope_configuration_matches_access_matrix():
    calls = []

    def fake_api(token, method, payload):
        calls.append((method, payload))
        if method == "getMyCommands":
            commands = ADMIN_COMMANDS if payload["scope"]["type"] == "chat" else USER_COMMANDS
            return {"ok": True, "result": commands}
        return {"ok": True, "result": True}

    with patch("scripts.configure_telegram_commands.telegram_api", side_effect=fake_api):
        configure_commands("not-a-real-token", 123456)

    deletes = [payload for method, payload in calls if method == "deleteMyCommands"]
    assert len(deletes) == 20
    assert {payload["scope"]["type"] for payload in deletes} == {
        "default", "all_private_chats", "all_group_chats", "all_chat_administrators", "chat",
    }
    assert {payload.get("language_code") for payload in deletes} == {None, "uk", "ru", "en"}
    sets = [payload for method, payload in calls if method == "setMyCommands"]
    assert sets == [
        {"scope": {"type": "default"}, "commands": USER_COMMANDS},
        {"scope": {"type": "chat", "chat_id": 123456}, "commands": ADMIN_COMMANDS},
    ]
