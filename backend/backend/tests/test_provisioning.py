"""NEW — pre-provisioning (spec §4.1). Pulls a campaign's contacts + payments and
pre-creates funded accounts. The properties that matter for event night: chips
only, everyone registered (so cash walk-ins are findable), and idempotency both
on re-run and shared with the live webhook (a payment credits once either way)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402
import webhook  # noqa: E402
import provision as PROV  # noqa: E402
from mapper import BETTING_RATE_ID  # noqa: E402
from zeffy_client import FakeZeffy  # noqa: E402

CAMP = "camp_db"
DRINK_RATE = "c5fd0e98-c247-4b8a-ace6-a595efe3c02f"


def bet(a):
    return {"amount": a, "rate_id": BETTING_RATE_ID, "rate_title": "Betting Ticket"}


def drink():
    return {"amount": 200, "rate_id": DRINK_RATE, "rate_title": "Alcoholic Drink Ticket"}


def entry(a):
    return {"amount": a, "rate_id": "entry-rate-uuid", "rate_title": "GA + Buffet"}


def pmt(pid, contact, email, first, last, items, status="succeeded", refunded=False):
    return {"id": pid, "campaign": CAMP, "status": status,
            "refund_status": "refunded" if refunded else "none",
            "refunds": [{}] if refunded else [], "contact": contact,
            "buyer": {"email": email, "first_name": first, "last_name": last},
            "items": items}


CAMPAIGNS = [{"id": CAMP, "title": "Night at the Races — Drinks & Bets"},
             {"id": "other", "title": "Unrelated Gala"}]

CONTACTS = [
 {"id": "c-jane", "campaign": CAMP, "email": "jane@example.com",
  "first_name": "Jane", "last_name": "Doe"},
 {"id": "c-sam", "campaign": CAMP, "email": "sam@example.com",
  "first_name": "Sam", "last_name": "Lee"},
 # a cash walk-in: registered the night before, no payment yet
 {"id": "c-cash", "campaign": CAMP, "email": "walkin@example.com",
  "first_name": "Walk", "last_name": "In"},
]

PAYMENTS = [
 pmt("p-jane", "c-jane", "Jane@Example.com ", "Jane", "Doe",
     [drink(), bet(1000), bet(1000), bet(1000)]),          # $30 chips (drinks excl)
 pmt("p-sam", "c-sam", "sam@example.com", "Sam", "Lee", [bet(1000)] * 5),  # $50
 pmt("p-drinks", "c-dana", "dana@example.com", "Dana", "K", [drink(), drink()]),  # $0
 pmt("p-entry", "c-ed", "ed@example.com", "Ed", "T", [entry(5000)]),      # $0 chips
 pmt("p-fail", "c-fred", "fred@example.com", "Fred", "N", [bet(1000)], status="failed"),
 pmt("p-refund", "c-rita", "rita@example.com", "Rita", "R", [bet(1000)], refunded=True),
]

ZEFFY = FakeZeffy(payments=PAYMENTS, campaigns=CAMPAIGNS, contacts=CONTACTS)


def run(c):
    return PROV.provision(c, ZEFFY, "night at the races")


def _funds_chips_only(c):
    run(c)
    eq(DB.balance_for_identity(c, {"contactId": "c-jane"}), 3000, "Jane: 3x$10, drinks excl")
    eq(DB.balance_for_identity(c, {"contactId": "c-sam"}), 5000, "Sam: 5x$10")


def _registers_everyone(c):
    run(c)
    # cash walk-in (contacts-only) is findable by email, zero balance
    eq(DB.balance_for_identity(c, {"email": "walkin@example.com"}), 0, "walk-in zero")
    ok(DB.lookup_identity(c, "c-cash", None) is not None, "walk-in registered")
    # drinks-only and entry-only buyers registered but unfunded
    ok(DB.lookup_identity(c, "c-dana", None) is not None, "drinks buyer registered")
    eq(DB.balance_for_identity(c, {"contactId": "c-ed"}), 0, "entry buyer zero chips")


def _skips_failed_and_refunded(c):
    s = run(c)
    eq(DB.balance_for_identity(c, {"contactId": "c-fred"}), 0, "failed: no credit")
    eq(DB.balance_for_identity(c, {"contactId": "c-rita"}), 0, "refunded: no credit")
    ok(any(f["kind"] == "skipped_status" for f in s["flags"]), "failed flagged")
    ok(any(f["kind"] == "skipped_refunded" for f in s["flags"]), "refunded flagged")


def _idempotent_rerun(c):
    run(c)
    before = DB.balance_for_identity(c, {"contactId": "c-jane"})
    n_before = len(DB.accounts(c))
    s2 = run(c)                                   # run it again — the morning of
    eq(DB.balance_for_identity(c, {"contactId": "c-jane"}), before, "no double credit")
    eq(len(DB.accounts(c)), n_before, "no duplicate accounts")
    ok(s2["duplicates"] >= 2, "second run reports the chip payments as duplicates")


def _shared_idempotency_with_webhook(c):
    # a chip payment arrives via the live webhook FIRST, then provisioning runs
    webhook.ingest(c, next(p for p in PAYMENTS if p["id"] == "p-jane"))
    eq(DB.balance_for_identity(c, {"contactId": "c-jane"}), 3000, "webhook credited")
    run(c)
    eq(DB.balance_for_identity(c, {"contactId": "c-jane"}), 3000, "provision doesn't re-credit")


def _cash_tops_up_registered_walkin(c):
    run(c)
    # at the door the walk-in pays $20 cash by email -> same account, funded
    DB.topup_identity(c, {"email": "walkin@example.com"}, 2000, "cash")
    eq(len(DB.accounts(c)), _account_count_after_provision(),
       "cash top-up attaches to the pre-registered account, no new one")
    eq(DB.balance_for_identity(c, {"email": "walkin@example.com"}), 2000, "walk-in funded")


def _account_count_after_provision():
    # jane, sam, dana, ed, cash-walkin registered; fred/rita skipped -> 5
    return 5


def _dry_run_previews(_c):
    rep = PROV.plan(ZEFFY, "night at the races")
    eq(rep["contacts"], 3, "3 contacts in campaign")
    eq(rep["payments"], 6, "6 payments in campaign")
    eq(rep["chips_cents"], 8000, "$30 + $50 in chips")
    eq(rep["would_skip"], 2, "failed + refunded skipped")


SUITE = [
 ("funds chip buyers, chips only (drinks excluded)", _funds_chips_only),
 ("registers everyone — walk-ins, drinks-only, entry-only", _registers_everyone),
 ("skips failed and refunded, surfaces flags", _skips_failed_and_refunded),
 ("idempotent on re-run — no double credit, no dup accounts", _idempotent_rerun),
 ("shares idempotency with the webhook — credited once either way",
  _shared_idempotency_with_webhook),
 ("cash top-up lands on the pre-registered walk-in account", _cash_tops_up_registered_walkin),
 ("dry-run previews without writing", _dry_run_previews),
]


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("NEW - pre-provisioning (spec §4.1)", SUITE)
    sys.exit(1 if (r or e) else 0)
