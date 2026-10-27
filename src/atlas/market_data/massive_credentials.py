"""Lazy, single-variable credentials; never populate the global process environment."""

import os
import re
from pathlib import Path

from atlas.market_data.providers import ProviderError


def load_massive_key(*, authorized=False, path=None):
    if not authorized:
        raise ProviderError("MASSIVE_ACCESS_NOT_AUTHORISED")
    value = os.environ.get("MASSIVE_API_KEY")
    if value is None:
        target = Path(path) if path is not None else Path(__file__).resolve().parents[3] / ".env"
        matches = []
        try:
            if target.stat().st_size > 65536:
                raise ProviderError("MASSIVE_CREDENTIAL_FILE_TOO_LARGE")
            with target.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    name, separator, candidate = line.partition("=")
                    if separator and name.strip() == "MASSIVE_API_KEY":
                        matches.append(candidate.strip())
        except (OSError, UnicodeError):
            raise ProviderError("MASSIVE_CREDENTIAL_UNAVAILABLE") from None
        if len(matches) != 1:
            raise ProviderError("MASSIVE_CREDENTIAL_MISSING_OR_AMBIGUOUS")
        value = matches[0]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", value)
        or value.lower().startswith(("replace_", "your_"))
    ):
        raise ProviderError("MASSIVE_CREDENTIAL_INVALID_OR_EMPTY")
    return value
