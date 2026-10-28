"""Lazy, scoped root-dotenv configuration; never populate the process environment."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from atlas.config import AtlasError

ROOT = Path(__file__).resolve().parents[3]
HOSTS = {"live": "api-fxtrade.oanda.com", "practice": "api-fxpractice.oanda.com"}
SYMBOLS = ("EUR_USD", "GBP_USD", "USD_JPY")


@dataclass(frozen=True)
class OandaConfig:
    token: str = field(repr=False)
    account_id: str = field(repr=False)
    environment: str

    def __post_init__(self):
        if self.environment not in HOSTS:
            raise AtlasError("OANDA_ENVIRONMENT must explicitly be live or practice.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", self.token):
            raise AtlasError("Configure a valid nonempty OANDA_API_TOKEN.")
        if not re.fullmatch(r"[0-9]+(?:-[0-9]+){1,5}", self.account_id):
            raise AtlasError("Configure a valid OANDA_ACCOUNT_ID.")

    @property
    def host(self):
        return HOSTS[self.environment]


def load_config(root=ROOT, environ=None):
    """Same root-relative/selective convention as Alpaca; explicit empty wins."""
    environ = os.environ if environ is None else environ
    names = ("OANDA_API_TOKEN", "OANDA_ACCOUNT_ID", "OANDA_ENVIRONMENT")
    found = {n: environ[n] for n in names if n in environ}
    path = Path(root) / ".env"
    try:
        if len(found) < len(names) and path.is_file():
            if path.stat().st_size > 65536:
                raise AtlasError("OANDA configuration file exceeds size limit.")
            assignments = {}
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                name, sep, value = line.partition("=")
                name = name.strip().removeprefix("export ").strip()
                if sep and name in names and name not in found:
                    if name in assignments:
                        raise AtlasError("Duplicate OANDA configuration assignment: " + name)
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    assignments[name] = value
            found.update(assignments)
    except (OSError, UnicodeError):
        raise AtlasError("Cannot read repository-root OANDA configuration.") from None
    missing = [n for n in names if not found.get(n, "").strip()]
    if missing:
        raise AtlasError("Configure nonempty variables: " + ", ".join(missing))
    return OandaConfig(found[names[0]], found[names[1]], found[names[2]])
