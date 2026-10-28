"""Allowlisted root .env fallback for local services; never loads broker credentials."""

import os
from contextlib import contextmanager
from pathlib import Path

from atlas.config import AtlasError

ROOT = Path(__file__).resolve().parents[2]
SERVICE_NAMES = frozenset(
    {
        "ATLAS_ENV",
        "ATLAS_HISTORY_DB",
        "ATLAS_TELEGRAM_ENABLED",
        "ATLAS_TELEGRAM_CARDS_ENABLED",
        "ATLAS_TELEGRAM_BOT_TOKEN",
        "ATLAS_TELEGRAM_CHAT_ID",
    }
)


def service_values():
    values = {n: os.environ[n] for n in SERVICE_NAMES if n in os.environ}
    path = ROOT / ".env"
    if path.is_file():
        try:
            if path.stat().st_size > 65536:
                raise AtlasError("Local service configuration file is too large.")
            saved = {}
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                name, separator, value = line.partition("=")
                name = name.strip().removeprefix("export ").strip()
                if separator and name in SERVICE_NAMES and name not in values:
                    if name in saved:
                        raise AtlasError("Duplicate local service configuration name: " + name)
                    value = value.strip()
                    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    saved[name] = value
            values.update(saved)
        except (OSError, UnicodeError):
            raise AtlasError("Cannot read repository-root local service configuration.") from None
    if values.get("ATLAS_HISTORY_DB"):
        path = Path(values["ATLAS_HISTORY_DB"])
        values["ATLAS_HISTORY_DB"] = str(
            (ROOT / path).resolve() if not path.is_absolute() else path
        )
    return values


@contextmanager
def service_environment():
    values = service_values()
    added = {n: v for n, v in values.items() if n not in os.environ}
    os.environ.update(added)
    try:
        yield
    finally:
        for name in added:
            os.environ.pop(name, None)
