"""
Zeffy read-API client (spec §4.1, §4.3). Two jobs:

  * re-fetch-by-id in the webhook path — trust only the authoritative record,
  * list campaigns / payments / contacts for pre-provisioning.

The read API needs a browser User-Agent to dodge Zeffy's Cloudflare 1010 filter
(spec §4.1). All network access is confined to this module; the rest of the
pipeline stays pure and testable via FakeZeffy. Base path, auth, and pagination
mirror the existing scripts/ pullers.
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.parse
import urllib.request

ZEFFY_API_BASE = os.environ.get("ZEFFY_API_BASE", "https://api.zeffy.com/api/v1")
ZEFFY_API_KEY = os.environ.get("ZEFFY_API_KEY")  # never commit this
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/125.0.0.0 Safari/537.36")


def _extract_records(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


class ZeffyClient:
    """Real read-API client. Endpoint paths are isolated here so they can be
    corrected against Zeffy's current API without touching the pipeline."""

    def __init__(self, api_key=None, base=None, timeout=30):
        self.api_key = api_key or ZEFFY_API_KEY
        self.base = (base or ZEFFY_API_BASE).rstrip("/")
        self.timeout = timeout

    def _get(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.api_key}" if self.api_key else "")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", _BROWSER_UA)          # dodge Cloudflare 1010
        req.add_header("Accept-Language", "en-US,en;q=0.9")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8"))

    def _get_all(self, resource, params=None, cap=100000):
        """Follow next_cursor/has_more pagination; return the full list."""
        params = dict(params or {})
        params.setdefault("limit", 100)
        out = []
        for _ in range(1000):                              # hard page ceiling
            payload = self._get("/" + resource, params)
            recs = _extract_records(payload)
            out.extend(recs)
            cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
            more = isinstance(payload, dict) and payload.get("has_more")
            if not recs or not more or not cursor or len(out) >= cap:
                break
            params["starting_after"] = cursor
        return out[:cap]

    # ---- webhook path -----------------------------------------------------
    def get_payment(self, payment_id):
        """Authoritative payment dict, or None if it doesn't exist."""
        if not payment_id:
            return None
        try:
            return self._get(f"/payments/{payment_id}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    # ---- pre-provisioning path -------------------------------------------
    def list_campaigns(self):
        return self._get_all("campaigns", cap=500)

    def list_payments(self, campaign_id=None):
        params = {"campaign": campaign_id} if campaign_id else None
        return self._get_all("payments", params)

    def list_contacts(self, campaign_id=None):
        params = {"campaign": campaign_id} if campaign_id else None
        return self._get_all("contacts", params)


class FakeZeffy:
    """Test double. Knows only about payments/campaigns/contacts that 'really
    happened' — an unknown id returns None, exactly as the read API would for a
    spoofed request."""

    def __init__(self, payments=None, campaigns=None, contacts=None):
        payments = payments or []
        self._by_id = {p["id"]: p for p in payments}
        self._payments = payments
        self._campaigns = campaigns or []
        self._contacts = contacts or []

    def get_payment(self, payment_id):
        return self._by_id.get(payment_id)

    def list_campaigns(self):
        return list(self._campaigns)

    def list_payments(self, campaign_id=None):
        if campaign_id is None:
            return list(self._payments)
        return [p for p in self._payments if p.get("campaign") == campaign_id]

    def list_contacts(self, campaign_id=None):
        if campaign_id is None:
            return list(self._contacts)
        return [c for c in self._contacts if c.get("campaign") == campaign_id]
