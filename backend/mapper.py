"""
Zeffy payment -> top-up mapper (spec §4.2). Pure function, ported verbatim from
suite 03. It decides how much *betting* credit a payment grants and whom to
credit. Betting credit comes ONLY from Betting-Ticket items (by rate_id), never
drinks, never the gross. The rate_id is refreshed each year from the new form.
"""
from __future__ import annotations
import os
import re

# Last year's "Betting Ticket" rate. REFRESH THIS each year from the new Zeffy
# form (spec §4.2). Override without editing code via BETTING_RATE_ID.
BETTING_RATE_ID = os.environ.get(
    "BETTING_RATE_ID", "a188e910-2fa6-45fe-b72f-9c2cc49f76b6"
)
# Used only when an item has no matching rate_id (e.g. a hand-added line).
BETTING_TITLE_FALLBACK = re.compile(r"\bbet(ting)?\b", re.IGNORECASE)


def normalize_email(e):
    return (e or "").strip().lower() or None


def is_betting_item(it):
    if it and it.get("rate_id") and it["rate_id"] == BETTING_RATE_ID:
        return True
    return bool(BETTING_TITLE_FALLBACK.search((it or {}).get("rate_title") or ""))


def read_zeffy_fields(pmt):
    """Adapter for the real Zeffy payment shape (spec §4.2)."""
    b = pmt.get("buyer") or {}
    refunds = pmt.get("refunds")
    is_refunded = (
        (pmt.get("refund_status") and pmt.get("refund_status") != "none")
        or (isinstance(refunds, list) and len(refunds) > 0)
    )
    return {
        "id": pmt.get("id"),
        "status": pmt.get("status"),
        "is_refunded": bool(is_refunded),
        "email": b.get("email"),
        "name": ((b.get("first_name") or "") + " " + (b.get("last_name") or "")).strip(),
        "contact_id": pmt.get("contact") or None,
        "items": pmt.get("items") if isinstance(pmt.get("items"), list) else [],
    }


def map_payment(pmt):
    """Return {'topups': [...], 'flags': [...]}.

    A topup is {player, contactId, email, label, cents, source, ref}.
    Flags route the payment to the human exception desk instead of crediting."""
    f = read_zeffy_fields(pmt)
    flags = []

    if f["status"] != "succeeded":
        return {"topups": [], "flags": [
            {"kind": "skipped_status", "status": f["status"], "ref": f["id"]}]}
    if f["is_refunded"]:
        return {"topups": [], "flags": [{"kind": "skipped_refunded", "ref": f["id"]}]}

    email = normalize_email(f["email"])
    contact_id = f["contact_id"]
    cents = sum((it.get("amount") or 0) for it in f["items"] if is_betting_item(it))

    if cents <= 0:                       # drinks-only / entry-only: nothing, no flag
        return {"topups": [], "flags": flags}
    if not email and not contact_id:     # can't credit anyone -> exception desk
        flags.append({"kind": "missing_identity", "ref": f["id"]})
        return {"topups": [], "flags": flags}

    player = contact_id or email
    return {
        "topups": [{
            "player": player, "contactId": contact_id, "email": email or None,
            "label": f["name"] or email or contact_id,
            "cents": cents, "source": "zeffy", "ref": f["id"],
        }],
        "flags": flags,
    }
