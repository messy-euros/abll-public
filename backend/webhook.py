"""
Webhook receiver core (spec §4.2, §4.3). Framework-agnostic so it can be tested
without a network: a thin HTTP adapter lives in app.py.

The webhook body is an UNTRUSTED CLAIM, not an instruction (spec §4.3). We:
  1. optionally verify an HMAC signature over the raw body, then
  2. re-fetch the payment by id from the read API and trust ONLY that record,
  3. map() the authoritative payment (chips-only), and
  4. credit via the idempotent §3.2 path.

`ingest` is the map->credit seam (suite 04). `process_event` wraps it with the
verification that makes a public endpoint safe.
"""
from __future__ import annotations
import hashlib
import hmac
import json

import db as DB
import mapper


# ---- the seam: map -> credit (suite 04) -----------------------------------
def ingest(conn, payment):
    """Map an authoritative payment and credit each resulting top-up. The
    payment `ref` is carried through so a redelivery is idempotent. Returns
    {'flags': [...], 'credited': cents}."""
    result = mapper.map_payment(payment)
    credited = 0
    for t in result["topups"]:
        DB.topup_identity(
            conn,
            {"contactId": t["contactId"], "email": t["email"], "label": t["label"]},
            t["cents"], t["source"], t["ref"],
        )
        credited += t["cents"]
    return {"flags": result["flags"], "credited": credited}


# ---- authenticity -----------------------------------------------------------
def verify_signature(raw_body: bytes, header_sig: str | None, secret: str | None):
    """HMAC-SHA256 over the raw body, constant-time compared. Only enforced when
    a secret is configured. (spec §4.3 step 1 — the belt; re-fetch is braces.)"""
    if not secret:
        return True                      # no secret configured -> skip to re-fetch
    if not header_sig:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = header_sig.split("=")[-1].strip()   # tolerate "sha256=<hex>"
    return hmac.compare_digest(expected, provided)


def _extract_payment_id(body: dict):
    """Zeffy nests the payment under data; be defensive about the exact shape."""
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if isinstance(data, dict) and data.get("id"):
        return data["id"]
    return body.get("id") or body.get("payment_id")


def process_event(conn, zeffy, raw_body: bytes, headers=None, secret=None):
    """Handle one webhook delivery. Returns (http_status, response_dict).

    Return 200 quickly on success; Zeffy retries non-200 and idempotency makes
    retries harmless. Verification failures credit nothing."""
    headers = headers or {}

    # 1. optional signature check over the RAW body (before parsing)
    sig = headers.get("X-Zeffy-Signature") or headers.get("x-zeffy-signature")
    if not verify_signature(raw_body, sig, secret):
        return 401, {"error": "bad signature"}

    # 2. parse the untrusted claim just far enough to learn the payment id
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 400, {"error": "invalid json"}
    payment_id = _extract_payment_id(body)
    if not payment_id:
        return 400, {"error": "no payment id"}

    # 3. re-fetch by id and trust ONLY the authoritative record (defeats spoofs)
    payment = zeffy.get_payment(payment_id)
    if payment is None:
        return 404, {"error": "payment not found", "ref": payment_id}

    # 4. map + credit (idempotent). Never trust amounts/items from the POST body.
    outcome = ingest(conn, payment)
    return 200, {"ok": True, **outcome}
