"""Interactive local-only secret entry and nonce-verified private-chat discovery."""

import argparse
import getpass
import os
import secrets
import sys

from atlas.config import AtlasError
from atlas.main import main
from atlas.notifications import TelegramProvider, TelegramSettings


def discover_private_chat(token):
    nonce = secrets.token_hex(12)
    print("In your PRIVATE conversation with your BotFather-created bot, send:")
    print("/atlas_setup " + nonce)
    input("Press Enter here only after sending that exact message: ")
    provider = TelegramProvider(TelegramSettings(enabled=True, token=token, discovery_only=True))
    updates, error = provider.request("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
    if error or not isinstance(updates, list):
        raise AtlasError(
            "Private-chat discovery failed; no credentials or response details are shown."
        )
    matches = set()
    for update in updates:
        message = update.get("message", {}) if isinstance(update, dict) else {}
        if not isinstance(message, dict):
            continue
        chat, sender = message.get("chat", {}), message.get("from", {})
        if (
            isinstance(chat, dict)
            and isinstance(sender, dict)
            and chat.get("type") == "private"
            and chat.get("id") == sender.get("id")
            and type(chat.get("id")) is int
            and chat["id"] > 0
            and type(sender.get("id")) is int
            and sender.get("is_bot") is not True
            and message.get("text") == "/atlas_setup " + nonce
        ):
            matches.add(str(chat["id"]))
    if len(matches) != 1:
        raise AtlasError(
            "No unique nonce-verified private chat. Retry setup; no recipient was selected."
        )
    print("Private chat verified locally. Its ID is not displayed or stored.")
    return matches.pop()


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Secure local Telegram configuration for one command"
    )
    parser.add_argument("--action", choices=("test", "send", "worker"), default="test")
    parser.add_argument("--discover-chat", action="store_true")
    parser.add_argument("--history-db")
    parser.add_argument(
        "--recipients",
        action="store_true",
        help="Use the persistent owner-approved recipient registry",
    )
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        raise AtlasError("Telegram secret entry requires your own interactive local terminal.")
    names = ("ATLAS_TELEGRAM_ENABLED", "ATLAS_TELEGRAM_BOT_TOKEN", "ATLAS_TELEGRAM_CHAT_ID")
    previous = {name: os.environ.get(name) for name in names}
    try:
        from atlas.history import HistoryStore
        from atlas.telegram_recipients_store import recipient_mode

        if recipient_mode(HistoryStore(args.history_db)) != args.recipients:
            raise AtlasError("Use --recipients for enabled verified-recipient mode only.")
        if args.recipients and args.discover_chat:
            raise AtlasError("Register chats through atlas.telegram_recipients, not the sender.")
        token = getpass.getpass("Telegram bot token (masked, never stored): ")
        if args.recipients:
            chat_id = ""
        elif args.discover_chat:
            chat_id = discover_private_chat(token)
        else:
            chat_id = getpass.getpass("Private chat ID (masked): ")
        TelegramSettings(True, token, chat_id, True, multi_recipient=args.recipients)
        os.environ.update(dict(zip(names, ("true", token, chat_id), strict=True)))
        command = ["notifications-" + args.action, "--allow-synthetic"]
        if args.history_db:
            command += ["--history-db", args.history_db]
        return main(command)
    except AtlasError as error:
        print("Atlas: " + str(error))
        return 1
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(run())
