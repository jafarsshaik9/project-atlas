"""Interactive owner-only recipient administration; never sends notifications."""

import argparse
import getpass
import json
import os
import sys

from atlas.config import AtlasError, Settings
from atlas.dashboard_security import DashboardAuth
from atlas.history import HistoryStore
from atlas.notifications import TelegramProvider, TelegramSettings
from atlas.telegram_recipients_store import ALERTS, Recipients, bot_reference
from atlas.telegram_setup import discover_private_chat


def run(argv=None):
    parser = argparse.ArgumentParser(description="Owner-controlled private Telegram subscriptions")
    parser.add_argument(
        "action",
        choices=(
            "add",
            "list",
            "approve",
            "preferences",
            "disable",
            "remove",
            "enable-mode",
            "pause",
            "deliveries",
            "sync-opt-outs",
        ),
    )
    parser.add_argument("alias", nargs="?")
    parser.add_argument("--history-db")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--cards", action="store_true")
    parser.add_argument(
        "--alerts",
        default="trades,risk,system",
        help="Comma-separated trades,risk,system,summary; use none to opt out of all",
    )
    args = parser.parse_args(argv)
    session = None
    auth = None
    try:
        Settings(environment=os.environ.get("ATLAS_ENV", "demo"))
        if not sys.stdin.isatty():
            raise AtlasError(
                "Recipient administration requires your own interactive local terminal."
            )
        alerts = set() if args.alerts == "none" else set(args.alerts.split(","))
        if not alerts <= ALERTS:
            raise AtlasError("Unknown recipient alert category.")
        if args.action in ("add", "approve", "preferences", "disable", "remove") and not args.alias:
            raise AtlasError("A recipient alias is required.")
        store = HistoryStore(args.history_db)
        auth = DashboardAuth(store)
        if not auth.configured():
            raise AtlasError("Run atlas.main dashboard-setup for this history database first.")
        password = getpass.getpass("Atlas owner/dashboard password (masked): ")
        if not 1 <= len(password) <= 1024:
            raise AtlasError("Owner authentication failed.")
        session = auth.login(password, "telegram-admin-local")
        del password
        if not session:
            raise AtlasError("Owner authentication failed or is temporarily rate-limited.")
        registry = Recipients(store)
        if args.action == "list":
            print(json.dumps(registry.listing(session), indent=2))
        elif args.action == "deliveries":
            print(json.dumps(registry.delivery_listing(session), indent=2))
        elif args.action == "add":
            token = getpass.getpass("BotFather token (masked; never given to recipients): ")
            reference = bot_reference(token)
            chat_id = discover_private_chat(token)
            registry.register_verified(args.alias, chat_id, reference, session)
            print("Verified and saved PENDING. Separate owner approval is required; nothing sent.")
        elif args.action == "approve":
            registry.approve(
                args.alias, session, synthetic=args.synthetic, cards=args.cards, alerts=alerts
            )
            print("Owner approval saved. No historical alerts replayed; nothing sent.")
        elif args.action == "preferences":
            registry.preferences(
                args.alias, session, synthetic=args.synthetic, cards=args.cards, alerts=alerts
            )
            print(
                "Subscription saved for future events; unsent deliveries cancelled. Nothing sent."
            )
        elif args.action in ("disable", "remove"):
            registry.disable(args.alias, session, remove=args.action == "remove")
            print("Recipient disabled/removed; unsent deliveries cancelled. Nothing sent.")
        elif args.action in ("enable-mode", "pause"):
            registry.mode(session, paused=args.action == "pause")
            print("Recipient mode saved. Start the sender explicitly; nothing sent.")
        else:
            token = getpass.getpass("BotFather token (masked): ")
            registry.validate_bot(token)
            with store.connect() as db:
                offset = db.execute("SELECT update_offset FROM telegram_config").fetchone()[0]
            provider = TelegramProvider(
                TelegramSettings(enabled=True, token=token, discovery_only=True)
            )
            updates, error = provider.request(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 0,
                    "limit": 100,
                    "allowed_updates": ["message"],
                },
            )
            if error or not isinstance(updates, list):
                raise AtlasError("Private opt-out sync failed; no response details are shown.")
            print(
                f"Private opt-outs applied: {registry.opt_out_updates(updates)}. No messages sent."
            )
        return 0
    except AtlasError as error:
        print("Atlas: " + str(error), file=sys.stderr)
        return 1
    finally:
        if auth is not None and session:
            auth.logout(session)


if __name__ == "__main__":
    raise SystemExit(run())
