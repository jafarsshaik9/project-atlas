"""Explicit local controls. Starting the website never starts a notification sender."""

import getpass
import os
import sys
import time
from pathlib import Path

from atlas.activity import ActivityEvent, EventType
from atlas.config import AtlasError, Settings
from atlas.dashboard_security import DashboardAuth, DashboardSettings
from atlas.history import SCHEMA_VERSION, HistoryStore
from atlas.notifications import (
    TelegramProvider,
    TelegramSettings,
    deliver_one,
    enqueue_test,
    format_message,
)


def run(args):
    Settings(environment=os.environ.get("ATLAS_ENV", "demo"))
    if args.command == "notifications-design-preview":
        from atlas.telegram_preview import save_previews

        destination = save_previews()
        print("FICTIONAL OFFLINE PREVIEW — NO MESSAGE SENT")
        print("Open " + str(destination / "index.html"))
        return 0
    store = HistoryStore(args.history_db)
    if args.command == "dashboard-setup":
        if not sys.stdin.isatty():
            raise AtlasError("Password setup requires your own interactive local terminal.")
        password = getpass.getpass("Create workspace password (12+ characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise AtlasError("Passwords did not match.")
        DashboardAuth(store).set_password(password)
        print(
            "Workspace password configured locally; existing sessions revoked. No broker contacted."
        )
        return 0
    if args.command == "history-backup":
        if not args.backup_path:
            raise AtlasError("Specify a new --backup-path in an ignored local directory.")
        store.backup(Path(args.backup_path))
        print("Consistent SQLite backup completed. Keep it private and outside Git.")
        return 0
    if args.command in ("dashboard", "dashboard-check"):
        settings = DashboardSettings(
            args.host, args.port, args.lan, args.allow_insecure_lan, args.tls_cert, args.tls_key
        )
        configured = DashboardAuth(store).configured()
        if args.command == "dashboard-check":
            with store.connect() as db:
                good = db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            if not good:
                raise AtlasError("Database integrity check failed.")
            print(f"SYNTHETIC RESEARCH ONLY; SQLite schema {SCHEMA_VERSION}, integrity OK.")
            print("Workspace authentication: " + ("configured" if configured else "setup required"))
            print(
                "Local binding validated. No execution endpoint or background notification sender."
            )
            return 0
        if not configured:
            raise AtlasError("Run dashboard-setup locally before starting the dashboard.")
        try:
            import uvicorn

            from atlas.web.app import create_app
        except ModuleNotFoundError:
            raise AtlasError(
                "Install the approved dashboard extra: pip install -e '.[dev,dashboard]'."
            ) from None
        if settings.lan and not settings.secure:
            print("LAN HTTP is unencrypted. Use only trusted private Wi-Fi; TLS is recommended.")
        scheme = "https" if settings.secure else "http"
        print(f"Project Atlas: {scheme}://{settings.host}:{settings.port} — SYNTHETIC ONLY")
        print("Stop with Ctrl+C. No firewall, router or public tunnel changes are made.")
        uvicorn.run(
            create_app(store, settings, paper_db=getattr(args, "paper_db", None)),
            host=settings.host,
            port=settings.port,
            proxy_headers=False,
            server_header=False,
            access_log=False,
            ssl_certfile=settings.certificate,
            ssl_keyfile=settings.private_key,
        )
        return 0
    if args.command == "notifications-preview":
        selected = store.run()
        rows = (
            store.events(
                selected["run_id"],
                kinds=(
                    EventType.FILL,
                    EventType.PARTIAL,
                    EventType.BREACH,
                    EventType.ERROR,
                    EventType.KILL,
                ),
                limit=1,
            )[0]
            if selected
            else []
        )
        if not rows:
            print(
                "No confirmed fills or alerts to preview. "
                "Run a persisted synthetic research fixture first."
            )
        else:
            print(
                "OFFLINE PREVIEW — NO MESSAGE SENT\n"
                + format_message(ActivityEvent.model_validate(rows[0]))
            )
        return 0
    if not args.allow_synthetic:
        raise AtlasError("Telegram sending requires explicit --allow-synthetic.")
    from atlas.telegram_recipients_store import recipient_mode

    settings = TelegramSettings.from_environment(
        allow_synthetic=True, multi_recipient=recipient_mode(store)
    )
    store.record_notification_configuration(settings.enabled)
    if not settings.enabled:
        raise AtlasError("Telegram is disabled. Configure it locally and explicitly enable it.")
    provider = TelegramProvider(settings)
    if args.command == "notifications-test":
        event_id = enqueue_test(store)
        result = deliver_one(store, provider, enabled=True, event_id=event_id)
        print("Telegram connection test: " + result + ". No trade or portfolio change.")
        return 0 if result == "DELIVERED" else 1
    if args.command == "notifications-send":
        result = deliver_one(store, provider, enabled=True)
        print("Notification outbox: " + result)
        return 0 if result in ("DELIVERED", "NOT_DUE") else 1
    print("Opt-in synthetic notification worker started. Stop with Ctrl+C. No broker access.")
    try:
        while True:
            deliver_one(store, provider, enabled=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("Notification worker stopped.")
    return 0
