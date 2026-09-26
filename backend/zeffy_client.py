"""
Zeffy read-API client (spec §4.1, §4.3). Its one job in the webhook path is
re-fetch-by-id: given a payment id from an untrusted webhook body, fetch the
authoritative record. A spoofed POST names an id that doesn't exist (or whose
real amount/items differ), so trusting only the re-fetched record defeats it.

The read API needs a browser User-Agent to avoid Zeffy's Cloudflare 1010 filter
(spec §4.1). Network calls are confined to this module so the rest of the
pipeline stays a pure, testable function.
"""
from __future__ import annotations
import os
import json
import urllib.request

ZEFFY_API_BASE = os.environ.get("ZEFFY_API_BASE", "https://api.zeffy.com")
ZEFFY_API_KEY = os.environ.get("ZEFFY_API_KEY")  # never commit this
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class ZeffyClient:
    """Real read-API client. Endpoint path is intentionally isolated here so it
    can be corrected against Zeffy's current API without touching the pipeline."""

    def __init__(self, api_key=None, base=None, timeout=5):
        self.api_key = api_key or ZEFFY_API_KEY
        self.base = (base or ZEFFY_API_BASE).rstrip("/")
        self.timeout = timeout

    def get_payment(self, payment_id):
        """Return the authoritative payment dict, or None if it doesn't exist."""
        if not payment_id:
            return None
        url = f"{self.base}/v1/payments/{payment_id}"
        req = urllib.request.Request(url, headers={
            "User-Agent": _BROWSER_UA,               # dodge Cloudflare 1010
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                if r.status != 200:
                    return None
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise


class FakeZeffy:
    """Test double: only knows about payments that 'really happened'. Anything
    else returns None, exactly as the read API would for a spoofed id."""

    def __init__(self, payments):
        self._by_id = {p["id"]: p for p in payments}

    def get_payment(self, payment_id):
        return self._by_id.get(payment_id)
