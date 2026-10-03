"""
Pre-provisioning job (spec §4.1). The night before, pull Contacts and Payments
for the campaign and pre-create accounts — one per Zeffy contact UUID, aliased by
contact:<uuid> and email:<normalized>, pre-loaded with any Betting-Ticket chips
already bought. Most of the room then arrives already registered AND funded,
killing the check-in bottleneck.

It reuses the webhook's mapper and the same idempotent §3.2 credit path, so it is
safe to re-run and safe to run alongside the live webhook: a payment is credited
exactly once no matter which path sees it first (shared payment_ref guard).

    export ZEFFY_API_KEY=...          # read-only key; never commit it
    export DATABASE_URL=postgres://...
    python provision.py --match "night at the races"          # or --dry-run
"""
from __future__ import annotations
import argparse
import os
import sys

import db as DB
from mapper import map_payment, read_zeffy_fields, normalize_email


def campaign_title(c):
    for k in ("title", "name", "description"):
        v = c.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "(untitled)"


def match_campaigns(campaigns, name_match):
    nm = (name_match or "").lower()
    return [c for c in campaigns if nm in campaign_title(c).lower()]


def _contact_identity(c):
    """Adapter for a Zeffy contact record -> {contactId, email, label}."""
    contact_id = c.get("id") or c.get("contact") or None
    email = normalize_email(c.get("email"))
    name = ((c.get("first_name") or "") + " " + (c.get("last_name") or "")).strip()
    return contact_id, email, (name or email or contact_id)


def plan(zeffy, name_match):
    """Read-only preview of what provisioning WOULD do. Touches no database."""
    campaigns = match_campaigns(zeffy.list_campaigns(), name_match)
    ids = [c.get("id") for c in campaigns]
    payments = []
    for cid in ids or [None]:
        payments += zeffy.list_payments(cid)
    contacts = []
    for cid in ids or [None]:
        contacts += zeffy.list_contacts(cid)

    chips = 0
    would_credit = would_skip = 0
    flags = []
    for p in payments:
        res = map_payment(p)
        if res["flags"]:
            would_skip += 1
            flags += res["flags"]
            continue
        for t in res["topups"]:
            chips += t["cents"]
            would_credit += 1
    return {
        "campaigns": [campaign_title(c) for c in campaigns],
        "contacts": len(contacts), "payments": len(payments),
        "would_credit_payments": would_credit, "chips_cents": chips,
        "would_skip": would_skip, "flags": flags,
    }


def provision(conn, zeffy, name_match, register_contacts=True):
    """Do the provisioning. Returns a summary dict. Idempotent."""
    campaigns = match_campaigns(zeffy.list_campaigns(), name_match)
    if not campaigns:
        return {"error": "no matching campaign", "match": name_match}
    ids = [c.get("id") for c in campaigns]

    summary = {
        "campaigns": [campaign_title(c) for c in campaigns],
        "contacts_registered": 0, "payments_processed": 0,
        "chips_credited_cents": 0, "credited": 0, "duplicates": 0,
        "skipped": 0, "flags": [],
    }

    # Phase A — register everyone we know about, so cash walk-ins are findable
    # by email at the door even before they pay. No money moves here.
    if register_contacts:
        for cid in ids:
            for c in zeffy.list_contacts(cid):
                contact_id, email, label = _contact_identity(c)
                if not contact_id and not email:
                    continue
                DB.resolve_identity(conn, contact_id, email, label)
                summary["contacts_registered"] += 1

    # Phase B — walk the payments; register + fund from confirmed chip buys.
    for cid in ids:
        for p in zeffy.list_payments(cid):
            summary["payments_processed"] += 1
            res = map_payment(p)
            if res["flags"]:                      # failed / refunded / no identity
                summary["skipped"] += 1
                summary["flags"] += res["flags"]
                continue
            # register the buyer even if they only bought drinks/entry (no chips)
            f = read_zeffy_fields(p)
            if f["contact_id"] or normalize_email(f["email"]):
                DB.resolve_identity(conn, f["contact_id"],
                                    normalize_email(f["email"]), f["name"] or None)
            for t in res["topups"]:
                out = DB.topup_identity(
                    conn,
                    {"contactId": t["contactId"], "email": t["email"], "label": t["label"]},
                    t["cents"], t["source"], t["ref"],
                )
                if out.get("duplicate"):
                    summary["duplicates"] += 1
                else:
                    summary["credited"] += 1
                    summary["chips_credited_cents"] += t["cents"]
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Zeffy pre-provisioning (spec §4.1)")
    ap.add_argument("--match", default=os.environ.get("CAMPAIGN_MATCH", "night at the races"),
                    help="case-insensitive campaign title match")
    ap.add_argument("--dry-run", action="store_true",
                    help="preview only; touch no database")
    ap.add_argument("--no-contacts", action="store_true",
                    help="skip Phase A (payments only)")
    args = ap.parse_args(argv)

    from zeffy_client import ZeffyClient
    zeffy = ZeffyClient()
    if not zeffy.api_key:
        sys.exit("ERROR: set ZEFFY_API_KEY (read-only key) first.")

    if args.dry_run:
        rep = plan(zeffy, args.match)
        print("DRY RUN — nothing written.")
        print(f"  campaigns: {rep['campaigns']}")
        print(f"  contacts: {rep['contacts']}   payments: {rep['payments']}")
        print(f"  would credit {rep['would_credit_payments']} payments "
              f"= ${rep['chips_cents']/100:,.2f} in chips")
        print(f"  would skip {rep['would_skip']} (failed/refunded/no-identity)")
        return

    conn = DB.connect()
    try:
        s = provision(conn, zeffy, args.match, register_contacts=not args.no_contacts)
    finally:
        conn.close()
    if s.get("error"):
        sys.exit(f"ERROR: {s['error']} (match={s.get('match')!r})")
    print("Provisioning complete.")
    print(f"  campaigns:            {s['campaigns']}")
    print(f"  contacts registered:  {s['contacts_registered']}")
    print(f"  payments processed:   {s['payments_processed']}")
    print(f"  chips credited:       ${s['chips_credited_cents']/100:,.2f} "
          f"({s['credited']} new, {s['duplicates']} already done)")
    print(f"  skipped:              {s['skipped']} (failed/refunded/no-identity)")


if __name__ == "__main__":
    main()
