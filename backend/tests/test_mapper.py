"""Suite 03 — Zeffy -> top-up mapper, ported verbatim (spec §4.2). Pure
function; needs no database. Same 10 assertions as the browser suite."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mapper import map_payment, BETTING_RATE_ID  # noqa: E402

DRINK_RATE = "c5fd0e98-c247-4b8a-ace6-a595efe3c02f"


def bet(a):
    return {"object": "item", "type": "ticket", "amount": a, "currency": "usd",
            "rate_id": BETTING_RATE_ID, "rate_title": "Betting Ticket"}


def drink():
    return {"object": "item", "type": "ticket", "amount": 200, "currency": "usd",
            "rate_id": DRINK_RATE, "rate_title": "Alcoholic Drink Ticket"}


def entry(a):
    return {"object": "item", "type": "ticket", "amount": a, "currency": "usd",
            "rate_id": "entry-rate-uuid", "rate_title": "General Admission + Buffet"}


P = {
 "mixed": {"id": "pay_db1", "status": "succeeded", "refund_status": "none",
           "refunds": [], "contact": "contact-uuid-1",
           "buyer": {"email": "Jane@Example.com ", "first_name": "Jane", "last_name": "Doe"},
           "items": [drink(), drink(), bet(1000), bet(1000), bet(1000)]},
 "betsOnly": {"id": "pay_db2", "status": "succeeded", "refund_status": "none",
              "refunds": [], "contact": "contact-uuid-2",
              "buyer": {"email": "sam@example.com", "first_name": "Sam", "last_name": "Lee"},
              "items": [bet(1000)] * 5},
 "drinksOnly": {"id": "pay_db3", "status": "succeeded", "refund_status": "none",
                "refunds": [], "contact": "contact-uuid-3",
                "buyer": {"email": "d@x.com", "first_name": "D", "last_name": "K"},
                "items": [drink(), drink(), drink()]},
 "entryTicket": {"id": "pay_a1", "status": "succeeded", "refund_status": "none",
                 "refunds": [], "contact": "contact-uuid-4",
                 "buyer": {"email": "e@x.com", "first_name": "E", "last_name": "T"},
                 "items": [entry(5000)]},
 "messyEmail": {"id": "pay_db4", "status": "succeeded", "refund_status": "none",
                "refunds": [], "contact": None,
                "buyer": {"email": "  MESSY@Example.com ", "first_name": "M", "last_name": "X"},
                "items": [bet(1000)]},
 "failed": {"id": "pay_db5", "status": "failed", "refund_status": "none",
            "refunds": [], "contact": "c5", "buyer": {"email": "f@x.com"},
            "items": [bet(1000)]},
 "refunded": {"id": "pay_db6", "status": "succeeded", "refund_status": "refunded",
              "refunds": [{}], "contact": "c6", "buyer": {"email": "r@x.com"},
              "items": [bet(1000)]},
 "noEmailHasContact": {"id": "pay_db7", "status": "succeeded", "refund_status": "none",
                       "refunds": [], "contact": "contact-uuid-7",
                       "buyer": {"first_name": "No", "last_name": "Email"},
                       "items": [bet(1000), bet(1000)]},
 "noIdentity": {"id": "pay_db8", "status": "succeeded", "refund_status": "none",
                "refunds": [], "contact": None, "buyer": {}, "items": [bet(1000)]},
}


def _mixed_amounts(_c):
    r = map_payment(P["mixed"])
    eq(len(r["topups"]), 1, "topups")
    eq(r["topups"][0]["cents"], 3000, "3x$10 chips, excluding the two $2 drinks")


def _identity(_c):
    t = map_payment(P["mixed"])["topups"][0]
    eq(t["player"], "contact-uuid-1", "player=contactId")
    eq(t["contactId"], "contact-uuid-1", "contactId")
    eq(t["email"], "jane@example.com", "email normalised")
    eq(t["label"], "Jane Doe", "label")
    eq(t["ref"], "pay_db1", "ref")


SUITE = [
 ("mixed drinks+bets -> credit the BETS only, not the drinks", _mixed_amounts),
 ("bets-only -> sum of betting tickets",
  lambda _c: eq(map_payment(P["betsOnly"])["topups"][0]["cents"], 5000, "5x$10")),
 ("drinks-only -> no credit, no error", lambda _c: (
  eq(len(map_payment(P["drinksOnly"])["topups"]), 0, "no topups"),
  eq(len(map_payment(P["drinksOnly"])["flags"]), 0, "no flags"))[-1]),
 ("entry-ticket payment -> no betting credit",
  lambda _c: eq(len(map_payment(P["entryTicket"])["topups"]), 0, "no chips")),
 ("identity keys on contact UUID; email + name carried", _identity),
 ("no contact -> falls back to normalised email as player",
  lambda _c: eq(map_payment(P["messyEmail"])["topups"][0]["player"],
                "messy@example.com", "player=email when no contact")),
 ("failed payment -> no credit, flagged", lambda _c: (
  eq(len(map_payment(P["failed"])["topups"]), 0, "none"),
  ok(any(f["kind"] == "skipped_status" for f in map_payment(P["failed"])["flags"]),
     "skipped_status"))[-1]),
 ("refunded payment -> no credit, flagged", lambda _c: (
  eq(len(map_payment(P["refunded"])["topups"]), 0, "none"),
  ok(any(f["kind"] == "skipped_refunded" for f in map_payment(P["refunded"])["flags"]),
     "skipped_refunded"))[-1]),
 ("no email but has contact -> still credited by contact id", lambda _c: (
  eq(len(map_payment(P["noEmailHasContact"])["topups"]), 1, "credited"),
  eq(map_payment(P["noEmailHasContact"])["topups"][0]["player"], "contact-uuid-7", "player"),
  eq(map_payment(P["noEmailHasContact"])["topups"][0]["cents"], 2000, "2x$10"))[-1]),
 ("no email AND no contact -> flagged for exception desk", lambda _c: (
  eq(len(map_payment(P["noIdentity"])["topups"]), 0, "none"),
  ok(any(f["kind"] == "missing_identity" for f in map_payment(P["noIdentity"])["flags"]),
     "missing_identity"))[-1]),
]


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("Suite 03 - Zeffy mapper (pure)", SUITE)
    sys.exit(1 if (r or e) else 0)
