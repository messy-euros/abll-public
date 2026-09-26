"""Suite 04 — integration seam (map -> credit), ported against Postgres
(spec §4.2 -> §3.2). The seam is webhook.ingest(conn, payment); the ledger is
the real database. Same 9 assertions as the browser suite, including the one an
integration test uniquely catches: a redelivery must credit only once."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402
import webhook  # noqa: E402
from mapper import BETTING_RATE_ID  # noqa: E402

DRINK_RATE = "c5fd0e98-c247-4b8a-ace6-a595efe3c02f"


def bet(a):
    return {"amount": a, "rate_id": BETTING_RATE_ID, "rate_title": "Betting Ticket"}


def drink():
    return {"amount": 200, "rate_id": DRINK_RATE, "rate_title": "Alcoholic Drink Ticket"}


def entry(a):
    return {"amount": a, "rate_id": "entry-rate-uuid", "rate_title": "GA + Buffet"}


P = {
 "mixed": {"id": "pay_db1", "status": "succeeded", "refund_status": "none", "refunds": [],
           "contact": "contact-uuid-1",
           "buyer": {"email": "Jane@Example.com ", "first_name": "Jane", "last_name": "Doe"},
           "items": [drink(), drink(), bet(1000), bet(1000), bet(1000)]},
 "betsOnly": {"id": "pay_db2", "status": "succeeded", "refund_status": "none", "refunds": [],
              "contact": "contact-uuid-2",
              "buyer": {"email": "sam@example.com", "first_name": "Sam", "last_name": "Lee"},
              "items": [bet(1000)] * 5},
 "entryTicket": {"id": "pay_a1", "status": "succeeded", "refund_status": "none", "refunds": [],
                 "contact": "contact-uuid-4",
                 "buyer": {"email": "e@x.com", "first_name": "E", "last_name": "T"},
                 "items": [entry(5000)]},
 "failed": {"id": "pay_db5", "status": "failed", "refund_status": "none", "refunds": [],
            "contact": "c5", "buyer": {"email": "f@x.com"}, "items": [bet(1000)]},
 "refunded": {"id": "pay_db6", "status": "succeeded", "refund_status": "refunded",
              "refunds": [{}], "contact": "c6", "buyer": {"email": "r@x.com"},
              "items": [bet(1000)]},
}


def ingest(c, payment):
    return webhook.ingest(c, payment)


SUITE = [
 ("raw mixed payment -> betting balance is chips only, keyed by contact", lambda c: (
  ingest(c, P["mixed"]),
  eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-1"}), 3000,
     "3x$10 chips, drinks excluded"))[-1]),

 ("chip payment then cash-by-email -> one account, balances combine", lambda c: (
  ingest(c, P["mixed"]),
  DB.topup_identity(c, {"email": "jane@example.com"}, 2000, "cash"),
  eq(len(DB.accounts(c)), 1, "one account"),
  eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-1"}), 5000, "chips + cash"))[-1]),

 ("duplicate webhook (same payment twice) -> credited once", lambda c: (
  ingest(c, P["mixed"]),
  ingest(c, P["mixed"]),
  eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-1"}), 3000,
     "ref must survive the seam"))[-1]),

 ("failed payment -> no credit, flag surfaced to caller", lambda c: _failed(c)),

 ("refunded payment -> no credit, flag surfaced", lambda c: _refunded(c)),

 ("entry-ticket payment -> no betting credit, no account churn", lambda c: (
  ingest(c, P["entryTicket"]),
  eq(len(DB.accounts(c)), 0, "entry tickets grant nothing"))[-1]),

 ("two people, two payments -> separate balances", lambda c: (
  ingest(c, P["mixed"]),
  ingest(c, P["betsOnly"]),
  eq(len(DB.accounts(c)), 2, "two accounts"),
  eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-1"}), 3000, "A"),
  eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-2"}), 5000, "B"))[-1]),

 ("bet after credit draws down through either alias", lambda c: (
  ingest(c, P["mixed"]),
  DB.debit_account(c, DB.lookup_identity(c, "contact-uuid-1", None), 1000),
  eq(DB.balance_for_identity(c, {"email": "jane@example.com"}), 2000, "$30 - $10 bet"))[-1]),

 ("crash -> recover, end-to-end balances intact", lambda c: _recover(c)),
]


def _failed(c):
    r = ingest(c, P["failed"])
    eq(len(DB.accounts(c)), 0, "no account")
    ok(any(f["kind"] == "skipped_status" for f in r["flags"]), "flag returned")


def _refunded(c):
    r = ingest(c, P["refunded"])
    eq(len(DB.accounts(c)), 0, "no account")
    ok(any(f["kind"] == "skipped_refunded" for f in r["flags"]), "flag returned")


def _recover(c):
    ingest(c, P["mixed"])
    DB.topup_identity(c, {"email": "jane@example.com"}, 2000, "cash")
    ingest(c, P["betsOnly"])
    b1 = DB.balance_for_identity(c, {"contactId": "contact-uuid-1"})
    DB.rebuild_from_log(c)
    eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-1"}), b1, "A after recover")
    eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-2"}), 5000, "B after recover")


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("Suite 04 - integration seam (Postgres)", SUITE)
    sys.exit(1 if (r or e) else 0)
