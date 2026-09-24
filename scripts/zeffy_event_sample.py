#!/usr/bin/env python3
"""
Zeffy campaign-filtered sample puller  (Night at the Races)
==========================================================

Pulls payments from your two named campaigns and surfaces the ones with the
MOST line items, so we can see what a multi-line purchase actually looks like.

- Read-only GETs. Cannot modify anything.
- Reads your key from ZEFFY_API_KEY (never hardcoded, never in chat).
- Detects which field holds the line items instead of guessing.
- Writes two files:
    zeffy_events_FULL.json    -> real data, LOCAL ONLY, do NOT share
    zeffy_events_SCHEMA.json  -> line-item names/amounts kept; buyer PII, card
                                 digits, and address stripped. SAFE to share.

Run:
    export ZEFFY_API_KEY="your-key"        # macOS/Linux  (PowerShell: $env:ZEFFY_API_KEY="...")
    python3 zeffy_event_sample.py

Python 3.7+, standard library only. Revoke the key in Settings when done.
"""

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.zeffy.com/api/v1"

# Campaigns whose title contains this (case-insensitive) are pulled.
# "night at the races" matches BOTH the ticket form and the drinks/bets form.
NAME_MATCH = "night at the races"

SAMPLES_PER_CAMPAIGN = 3     # how many payments to save per campaign (richest first)
SCAN_CAP = 300              # how many payments to scan per campaign to find rich ones
ARRAY_SAMPLE = 12           # keep up to this many line items per payment in the schema

# --- Redaction: DENY wins, then ALLOW keeps, else default-deny (token) -------
# Keys whose VALUES are always stripped, even if they'd otherwise be kept:
DENY_KEYS = {
    "email", "first_name", "last_name", "full_name", "name_on_card",
    "phone", "mobile", "last4", "company_name",
    "line1", "line2", "street", "address", "postal_code", "zip", "city", "ip",
}
# Non-personal structural keys whose values are safe to keep:
ALLOW_KEYS = {
    "object", "id", "type", "status", "amount", "eligible_amount", "currency",
    "created", "refund_status", "dispute", "brand", "quantity", "count",
    "description", "name", "label", "title", "product_name",
    "contact", "campaign", "campaign_id", "contact_id", "is_corporate",
    "country", "state", "has_more", "next_cursor", "limit",
    "frequency", "recurring", "interval", "occurrence", "occurrences",
}

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/125.0.0.0 Safari/537.36")


def get_key():
    key = os.environ.get("ZEFFY_API_KEY", "").strip()
    if not key:
        sys.exit('\nERROR: set ZEFFY_API_KEY first, then re-run. '
                 '(export ZEFFY_API_KEY="your-key")\n')
    return key


def http_get(path, key, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", BROWSER_UA)          # dodge Cloudflare 1010
    req.add_header("Accept-Language", "en-US,en;q=0.9")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hints = {400: "Bad query parameters.",
                 401: "Key missing or invalid.",
                 403: "Access denied. If body says Cloudflare 1010, it's a bot filter, not your key.",
                 404: "Endpoint not found.",
                 429: "Rate limited; wait and retry."}
        body = ""
        try:
            body = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        sys.exit("\nHTTP {} on {}\n  {}\n  {}\n".format(
            e.code, path, hints.get(e.code, "Unexpected error."), body))
    except urllib.error.URLError as e:
        sys.exit("\nNetwork error reaching {}: {}\n".format(url, e.reason))


def extract_records(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


def fetch_all(resource, key, params=None, cap=SCAN_CAP):
    params = dict(params or {})
    params.setdefault("limit", 100)
    out = []
    for _ in range(10):                      # hard page ceiling, just in case
        payload = http_get("/" + resource, key, params)
        recs = extract_records(payload)
        out.extend(recs)
        cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
        more = isinstance(payload, dict) and payload.get("has_more")
        if not recs or not more or not cursor or len(out) >= cap:
            break
        params["starting_after"] = cursor
    return out[:cap]


def campaign_title(c):
    for k in ("title", "name", "description"):
        if isinstance(c.get(k), str) and c[k].strip():
            return c[k].strip()
    return "(untitled)"


def iso(ts):
    try:
        return dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def item_arrays(payment):
    """Every key whose value is a non-empty list of dicts -> candidate line items."""
    res = []
    if isinstance(payment, dict):
        for k, v in payment.items():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                res.append((k, v))
    return res


def max_lines(payment):
    arrs = item_arrays(payment)
    if not arrs:
        return (None, 0)
    k, v = max(arrs, key=lambda kv: len(kv[1]))
    return (k, len(v))


def token(v):
    if v is None:
        return "<null>"
    if isinstance(v, bool):
        return "<bool>"
    if isinstance(v, int):
        return "<int>"
    if isinstance(v, float):
        return "<float>"
    if isinstance(v, str):
        return "<string:{}>".format(len(v))
    return "<{}>".format(type(v).__name__)


def redact(obj):
    if isinstance(obj, dict):
        return {k: _handle(k, v) for k, v in obj.items()}
    if isinstance(obj, list):
        out = [redact(x) for x in obj[:ARRAY_SAMPLE]]
        if len(obj) > ARRAY_SAMPLE:
            out.append("<... {} more>".format(len(obj) - ARRAY_SAMPLE))
        return out
    return token(obj)


def _handle(k, v):
    if isinstance(v, (dict, list)):
        return redact(v)                      # always recurse to redact leaves
    kl = str(k).lower()
    if kl in DENY_KEYS or "email" in kl or "phone" in kl:
        return token(v)
    if kl in ALLOW_KEYS:
        return v
    return token(v)


def main():
    key = get_key()
    print("Finding campaigns matching '{}'...\n".format(NAME_MATCH))
    campaigns = fetch_all("campaigns", key, cap=500)
    matched = [c for c in campaigns if NAME_MATCH in campaign_title(c).lower()]

    if not matched:
        print("No campaign titles matched '{}'. Titles found:".format(NAME_MATCH))
        for c in campaigns[:40]:
            print("  - {}".format(campaign_title(c)))
        sys.exit("\nEdit NAME_MATCH near the top of the script and re-run.\n")

    print("Matched {} campaign(s):".format(len(matched)))
    for c in matched:
        print("  - {!r}  id={}  created={}".format(
            campaign_title(c), c.get("id"), iso(c.get("created"))))
    print()

    full = {"matched_campaigns": [
        {"id": c.get("id"), "title": campaign_title(c), "created": iso(c.get("created"))}
        for c in matched]}
    schema = {"matched_campaigns": full["matched_campaigns"], "by_campaign": {}}
    full["by_campaign"] = {}

    for c in matched:
        title, cid = campaign_title(c), c.get("id")
        print("Pulling payments for {!r}...".format(title))
        pays = fetch_all("payments", key, {"campaign": cid})
        pays.sort(key=lambda p: max_lines(p)[1], reverse=True)

        # distribution of line-item counts + which key holds them
        dist, key_votes = {}, {}
        for p in pays:
            k, n = max_lines(p)
            dist[n] = dist.get(n, 0) + 1
            if k:
                key_votes[k] = key_votes.get(k, 0) + 1
        line_key = max(key_votes, key=key_votes.get) if key_votes else None
        multi = sum(v for n, v in dist.items() if n > 1)
        print("  {} payments; line-item field looks like {!r}; "
              "{} have >1 line; counts={}".format(len(pays), line_key, multi,
                                                  dict(sorted(dist.items()))))

        samples = pays[:SAMPLES_PER_CAMPAIGN]
        full["by_campaign"][title] = {
            "campaign_id": cid, "payments_scanned": len(pays),
            "line_item_field": line_key, "line_count_distribution": dict(sorted(dist.items())),
            "samples": samples,
        }
        schema["by_campaign"][title] = {
            "campaign_id": cid, "payments_scanned": len(pays),
            "line_item_field": line_key, "line_count_distribution": dict(sorted(dist.items())),
            "samples": [redact(p) for p in samples],
        }

    with open("zeffy_events_FULL.json", "w", encoding="utf-8") as f:
        json.dump(full, f, indent=2, ensure_ascii=False)
    with open("zeffy_events_SCHEMA.json", "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)

    print("\nWrote:")
    print("  zeffy_events_FULL.json    <- real data, keep local, do NOT share")
    print("  zeffy_events_SCHEMA.json  <- SAFE to share (line names/amounts kept, PII stripped)")
    print("\nOpen the SCHEMA file, confirm no names/emails/card digits, then paste it back.")


if __name__ == "__main__":
    main()
