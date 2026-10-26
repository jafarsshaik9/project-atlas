"""Process-environment configuration; never represent secret values."""

import os
from dataclasses import dataclass, field


class AtlasError(Exception):
    """Safe, user-facing error with no upstream payload attached."""


@dataclass(frozen=True)
class Settings:
    environment: str = "demo"
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.environment != "demo":
            raise AtlasError("ATLAS_ENV must be demo. Other environments are disabled.")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=os.environ.get("ATLAS_ENV", "demo"),
            api_key=os.environ.get("TRADING212_API_KEY", ""),
            api_secret=os.environ.get("TRADING212_API_SECRET", ""),
        )

    def require_credentials(self) -> None:
        values = (self.api_key, self.api_secret)
        if any(not v.strip() or v.startswith("replace_with_") for v in values):
            raise AtlasError("Set demo API Key and API Secret using the local secure prompt.")
        if ":" in self.api_key:
            raise AtlasError(
                "API Key contains a colon. Enter only the raw API Key at the first prompt, "
                "then enter the API Secret separately at the second prompt."
            )
        for label, value in (("API Key", self.api_key), ("API Secret", self.api_secret)):
            if "\x16" in value:
                raise AtlasError(
                    f"{label} contains a Ctrl+V control character. "
                    "Try Shift+Insert at the masked prompt, or run "
                    "scripts/broker-check.ps1 -UseClipboard locally."
                )
            if any(c.isspace() for c in value):
                raise AtlasError(
                    f"{label} contains whitespace or a line break. "
                    "Copy only its value and paste it as a single line in the local prompt."
                )
            if any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise AtlasError(
                    f"{label} contains a hidden or non-ASCII character. "
                    "Re-copy the original raw value from Trading 212 into the local prompt."
                )
            if value.startswith(("'", '"', "Basic ")) or value.endswith(("'", '"')):
                raise AtlasError(
                    f"{label} appears to include quotes or an authentication prefix. "
                    "Enter only the raw value; Atlas builds the authentication header."
                )
