from __future__ import annotations

import hashlib
import hmac
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_ph = PasswordHasher()
_B62 = string.ascii_letters + string.digits
KEY_PREFIX = "pfn_"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _b62(n: int) -> str:
    return "".join(secrets.choice(_B62) for _ in range(n))


def new_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, secret_hash). Format: pfn_<prefix8>_<secret32>."""
    prefix = _b62(8)
    secret = _b62(32)
    return f"{KEY_PREFIX}{prefix}_{secret}", prefix, sha256(secret)


def parse_api_key(key: str) -> tuple[str, str] | None:
    if not key.startswith(KEY_PREFIX):
        return None
    rest = key[len(KEY_PREFIX):]
    prefix, sep, secret = rest.partition("_")
    if not sep or len(prefix) != 8 or not secret:
        return None
    return prefix, secret


def secret_matches(secret: str, secret_hash: str) -> bool:
    return hmac.compare_digest(sha256(secret), secret_hash)
