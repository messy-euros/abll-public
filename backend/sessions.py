"""
Signed guest sessions (spec §6 step 2, §8). A claim issues a token bound to one
account_id; every bet request carries it and the server re-verifies. The token is
HMAC-signed so a guest can't forge one for someone else's balance, and it carries
an expiry so a found phone link doesn't work forever.

Stateless by design (no server-side session store): the signature IS the
authority. Secret comes from GUEST_SESSION_SECRET; set a real one in production.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import time

SECRET = os.environ.get("GUEST_SESSION_SECRET", "dev-only-insecure-secret")
DEFAULT_TTL = int(os.environ.get("GUEST_SESSION_TTL", "43200"))  # 12h, covers a night


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(account_id: str, ttl: int = DEFAULT_TTL, secret: str = None) -> str:
    secret = secret or SECRET
    payload = {"acct": account_id, "exp": int(time.time()) + ttl}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(token: str, secret: str = None):
    """Return the account_id for a valid, unexpired token, else None."""
    secret = secret or SECRET
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_unb64(body).decode())
    except (ValueError, UnicodeDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("acct")
