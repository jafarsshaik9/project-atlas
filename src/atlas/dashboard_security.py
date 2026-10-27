"""Local password hashing, opaque sessions and explicit network binding policy."""

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import timedelta

from atlas.activity import now_utc
from atlas.config import AtlasError
from atlas.history import secret_hash

COOKIE = "atlas_session"


@dataclass(frozen=True)
class DashboardSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    lan: bool = False
    allow_insecure_lan: bool = False
    certificate: str | None = None
    private_key: str | None = None

    def __post_init__(self):
        if not 1024 <= self.port <= 65535:
            raise AtlasError("Dashboard port must be between 1024 and 65535.")
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            raise AtlasError("Use an explicit loopback or private LAN IP address.") from None
        if address.version != 4:
            raise AtlasError("This local dashboard supports IPv4 binding only.")
        if not address.is_loopback:
            private = any(
                address in ipaddress.ip_network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
            if not self.lan or not private:
                raise AtlasError("LAN access requires --lan and a specific private LAN IP.")
            if not self.secure and not self.allow_insecure_lan:
                raise AtlasError("LAN HTTP is unencrypted; explicitly allow it or configure TLS.")
        elif self.lan:
            raise AtlasError("LAN mode requires the laptop's private Wi-Fi IP.")
        if bool(self.certificate) != bool(self.private_key):
            raise AtlasError("TLS requires both a certificate and private key.")

    @property
    def secure(self):
        return bool(self.certificate and self.private_key)

    @property
    def hosts(self):
        return [self.host, "localhost"] if self.host == "127.0.0.1" else [self.host]


class DashboardAuth:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def derive(password, salt):
        return hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=32768,
            r=8,
            p=1,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        ).hex()

    def configured(self):
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM auth WHERE id=1").fetchone() is not None

    def set_password(self, password):
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise AtlasError("Use a dashboard password containing 12 to 1024 characters.")
        salt = secrets.token_hex(16)
        digest = self.derive(password, salt)
        with self.store.connect(write=True) as db:
            db.execute(
                "INSERT INTO auth VALUES(1,?,?) ON CONFLICT(id) DO UPDATE "
                "SET salt=excluded.salt,digest=excluded.digest",
                (salt, digest),
            )
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM login_limits")

    def login(self, password, client):
        now = now_utc()
        # Both per-client and global limits persist across server restarts.
        keys = [secret_hash(client), "global"]
        with self.store.connect(write=True) as db:
            for key in keys:
                row = db.execute("SELECT * FROM login_limits WHERE key=?", (key,)).fetchone()
                ceiling = 5 if key != "global" else 30
                if row and row["failures"] >= ceiling and row["until_at"] > now.isoformat():
                    return None
            credentials = db.execute("SELECT salt,digest FROM auth WHERE id=1").fetchone()
            salt = credentials["salt"] if credentials else "0" * 32
            digest = self.derive(password, salt)
            valid = credentials is not None and hmac.compare_digest(digest, credentials["digest"])
            if not valid:
                for key in keys:
                    row = db.execute("SELECT * FROM login_limits WHERE key=?", (key,)).fetchone()
                    count = row["failures"] + 1 if row and row["until_at"] > now.isoformat() else 1
                    db.execute(
                        "INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(key) DO UPDATE "
                        "SET failures=excluded.failures,until_at=excluded.until_at",
                        (key, count, (now + timedelta(minutes=5)).isoformat()),
                    )
                return None
            db.execute("DELETE FROM login_limits WHERE key=?", (keys[0],))
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now.isoformat(),))
            token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO sessions VALUES(?,?)",
                (secret_hash(token), (now + timedelta(hours=8)).isoformat()),
            )
        return token

    def valid(self, token):
        if not token or len(token) > 200:
            return False
        with self.store.connect() as db:
            row = db.execute(
                "SELECT expires_at FROM sessions WHERE token_hash=?", (secret_hash(token),)
            ).fetchone()
        return bool(row and row[0] > now_utc().isoformat())

    def logout(self, token):
        with self.store.connect(write=True) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (secret_hash(token or ""),))
