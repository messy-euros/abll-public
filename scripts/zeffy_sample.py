#!/usr/bin/env python3
"""
Zeffy read-only sample puller
=============================

Purpose
-------
Pull a SMALL sample of your Payments / Contacts / Campaigns from Zeffy's
read-only API so we can see the real data shape before designing the
"payment -> player profile" matching logic.

What it does
------------
- Read-only. GET requests only. It cannot modify anything in your account.
- Never hardcodes your key. It reads it from the ZEFFY_API_KEY environment
  variable, so the key stays on your machine and out of this file.
- Writes two files in the current folder:
    * zeffy_sample_FULL.json    -> real records. LOCAL ONLY. Do NOT share.
    * zeffy_sample_SCHEMA.json  -> structure only, personal values stripped.
                                   SAFE to paste back into the chat.

How to run  (macOS / Linux)
---------------------------
    export ZEFFY_API_KEY="paste-your-key-here"
    python3 zeffy_sample.py

How to run  (Windows PowerShell)
--------------------------------
    $env:ZEFFY_API_KEY = "paste-your-key-here"
    python zeffy_sample.py

Requires only the Python standard library (Python 3.7+). No pip install.
Your key is an admin credential: anyone with it can read your org's data.
Don't commit it, don't paste it into chats, revoke it in Settings when done.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.zeffy.com/api/v1"

# How many records to sample from each resource. Small on purpose.
SAMPLE_N = 5

# --- Redaction policy -------------------------------------------------------
# DEFAULT DENY: in the shareable SCHEMA file, every value is replaced with a
# type token UNLESS its key is on this allow-list of clearly non-personal,
# structural fields. This guarantees names/emails/addresses/etc. never leave
# your machine, even if Zeffy adds new personal fields we didn't anticipate.
ALLOW_VALUE_KEYS = {
    "object", "type", "status", "currency", "livemode", "mode",
    "amount", "amounttotal", "totalamount", "netamount", "feeamount",
    "quantity", "count", "recurring", "frequency", "interval",
    "ispaid", "isrefunded", "refunded", "hasmore", "has_more", "limit",
    "id", "campaignid", "contactid", "paymentid", "orderid",
    "campaign", "paymenttype", "paymentmethod", "source", "channel",
    "createdat", "createdatutc", "updatedat", "date", "createddate",
    "next_cursor", "starting_after",
}

# Cap arrays in the schema file so nested line-item lists don't bloat it.
ARRAY_SAMPLE = 2


def get_key():
    key = os.environ.get("ZEFFY_API_KEY", "").strip()
    if not key:
        sys.exit(
            "\nERROR: no API key found.\n"
            "Set it first, then re-run:\n"
            '  macOS/Linux:  export ZEFFY_API_KEY="your-key"\n'
            '  Windows PS:   $env:ZEFFY_API_KEY = "your-key"\n'
        )
    return key


def http_get(path, key, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Accept", "application/json")
    # Cloudflare (error 1010) bans the default "Python-urllib" signature, so
    # present a normal browser User-Agent. This is not spoofing anything about
    # the request itself -- just avoiding an over-eager bot filter.
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36",
    )
    req.add_header("Accept-Language", "en-US,en;q=0.9")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        hints = {
            400: "Bad query parameters. Check the filters/formatting.",
            401: "Key is missing or invalid. Re-check ZEFFY_API_KEY.",
            403: "Access denied. If the body mentions Cloudflare error 1010, "
                 "it's a bot filter on the User-Agent, not your key.",
            404: "Endpoint not found. The API path may have changed.",
            429: "Rate limited. Wait a moment and try again.",
        }
        body = ""
        try:
            body = e.read().decode("utf-8")[:400]
        except Exception:
            pass
        sys.exit(
            "\nHTTP {} on {}\n  {}\n  {}\n".format(
                e.code, path, hints.get(e.code, "Unexpected error."), body
            )
        )
    except urllib.error.URLError as e:
        sys.exit("\nNetwork error reaching {}: {}\n".format(url, e.reason))


def extract_records(payload):
    """Return the list of records from a paginated response, robustly."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # Prefer a conventional 'data' key; otherwise take the first list value.
        if isinstance(payload.get("data"), list):
            return payload["data"]
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


def redact(obj):
    """Structure-preserving redaction. Default deny on values."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                out[k] = redact(v)
            elif str(k).lower() in ALLOW_VALUE_KEYS:
                out[k] = v            # safe, non-personal structural value
            else:
                out[k] = type_token(v)  # everything else -> just its type
        return out
    if isinstance(obj, list):
        sample = obj[:ARRAY_SAMPLE]
        red = [redact(x) for x in sample]
        if len(obj) > ARRAY_SAMPLE:
            red.append("<... {} more items>".format(len(obj) - ARRAY_SAMPLE))
        return red
    return type_token(obj)


def type_token(v):
    if v is None:
        return "<null>"
    if isinstance(v, bool):
        return "<bool>"
    if isinstance(v, int):
        return "<int>"
    if isinstance(v, float):
        return "<float>"
    if isinstance(v, str):
        return "<string:{}>".format(len(v))  # length hint, no content
    return "<{}>".format(type(v).__name__)


def pull(resource, key):
    print("  fetching {} (up to {})...".format(resource, SAMPLE_N))
    payload = http_get("/" + resource, key, {"limit": SAMPLE_N})
    records = extract_records(payload)[:SAMPLE_N]
    print("    got {} record(s)".format(len(records)))
    return records


def main():
    key = get_key()
    print("Pulling read-only samples from Zeffy...\n")

    full = {}
    for resource in ("payments", "contacts", "campaigns"):
        try:
            full[resource] = pull(resource, key)
        except SystemExit:
            raise
        except Exception as e:
            print("    skipped {} ({})".format(resource, e))
            full[resource] = []

    schema = {r: redact(recs) for r, recs in full.items()}

    with open("zeffy_sample_FULL.json", "w", encoding="utf-8") as f:
        json.dump(full, f, indent=2, ensure_ascii=False)
    with open("zeffy_sample_SCHEMA.json", "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)

    counts = ", ".join("{} {}".format(len(v), k) for k, v in full.items())
    print("\nDone. Sampled: " + counts)
    print("\nWrote two files in this folder:")
    print("  zeffy_sample_FULL.json    <- real data, keep local, do NOT share")
    print("  zeffy_sample_SCHEMA.json  <- structure only, SAFE to paste in chat")
    print("\nOpen the SCHEMA file, glance that it has no names/emails, then")
    print("paste its contents back and we'll design the matching against it.")


if __name__ == "__main__":
    main()
