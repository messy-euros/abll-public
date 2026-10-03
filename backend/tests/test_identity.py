"""Suite 02 — identity / aliases / merge, ported against Postgres (spec §5, §10).
Same 9 assertions as the browser suite; resolution and merges are now real
transactions and the graph rebuilds from the durable log after a crash."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, run_suite  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402


def tu(c, ident, cents, source, ref=None):
    return DB.topup_identity(c, ident, cents, source, ref)


SUITE = [
 ("same contact id twice -> one account, balances accumulate", lambda c: (
  tu(c, {"contactId": "C"}, 5000, "zeffy", "r1"),
  tu(c, {"contactId": "C"}, 3000, "zeffy", "r2"),
  eq(len(DB.accounts(c)), 1, "one account"),
  eq(DB.balance_for_identity(c, {"contactId": "C"}), 8000, "balance"))[-1]),

 ("same email (case/whitespace) -> one account", lambda c: (
  tu(c, {"email": "Jane@Example.com "}, 1000, "zeffy", "r1"),
  tu(c, {"email": " jane@example.com"}, 2000, "cash"),
  eq(len(DB.accounts(c)), 1, "one account"),
  eq(DB.balance_for_identity(c, {"email": "JANE@EXAMPLE.COM"}), 3000, "balance"))[-1]),

 ("Zeffy payment carries both aliases -> later cash-by-email finds it", lambda c: (
  tu(c, {"contactId": "C", "email": "p@x.com"}, 5000, "zeffy", "r1"),
  tu(c, {"email": "p@x.com"}, 2000, "cash"),
  eq(len(DB.accounts(c)), 1, "one account"),
  eq(DB.balance_for_identity(c, {"contactId": "C"}), 7000, "balance via contact"))[-1]),

 ("two separate accounts later found to be one -> MERGE", lambda c: _merge_case(c)),

 ("different people stay separate", lambda c: (
  tu(c, {"contactId": "C1"}, 1000, "zeffy", "r1"),
  tu(c, {"contactId": "C2"}, 2000, "zeffy", "r2"),
  eq(len(DB.accounts(c)), 2, "two accounts"),
  eq(DB.balance_for_identity(c, {"contactId": "C1"}), 1000, "C1"),
  eq(DB.balance_for_identity(c, {"contactId": "C2"}), 2000, "C2"))[-1]),

 ("new alias attaches to a known account", lambda c: (
  tu(c, {"contactId": "C"}, 1000, "zeffy", "r1"),
  DB.resolve_identity(c, "C", "p@x.com"),
  eq(len(DB.accounts(c)), 1, "still one"),
  eq(DB.balance_for_identity(c, {"email": "p@x.com"}), 1000, "findable by email"))[-1]),

 ("a bet debits the unified (merged) account", lambda c: (
  tu(c, {"contactId": "C", "email": "p@x.com"}, 5000, "zeffy", "r1"),
  DB.debit_account(c, DB.lookup_identity(c, "C", "p@x.com"), 3000),
  eq(DB.balance_for_identity(c, {"contactId": "C"}), 2000, "debited via either alias"))[-1]),

 ("duplicate top-up ref is idempotent across resolution", lambda c: (
  tu(c, {"contactId": "C"}, 5000, "zeffy", "ZF1"),
  tu(c, {"contactId": "C"}, 5000, "zeffy", "ZF1"),
  eq(DB.balance_for_identity(c, {"contactId": "C"}), 5000, "credited once"))[-1]),

 ("crash -> recover rebuilds identity graph AND balances", lambda c: _recover_case(c)),
]


def _merge_case(c):
    tu(c, {"email": "p@x.com"}, 2000, "cash")
    tu(c, {"contactId": "C"}, 5000, "zeffy", "r1")
    eq(len(DB.accounts(c)), 2, "separate so far")
    _acct, merged = DB.resolve_identity(c, "C", "p@x.com")
    ok(merged, "resolve reports merged")
    eq(len(DB.accounts(c)), 1, "merged to one")
    eq(DB.balance_for_identity(c, {"email": "p@x.com"}), 7000, "combined via email")
    eq(DB.balance_for_identity(c, {"contactId": "C"}), 7000, "combined via contact")


def _recover_case(c):
    tu(c, {"contactId": "C", "email": "p@x.com"}, 5000, "zeffy", "r1")
    tu(c, {"email": "p@x.com"}, 2000, "cash")
    tu(c, {"contactId": "C2"}, 1000, "zeffy", "r2")
    b1 = DB.balance_for_identity(c, {"contactId": "C"})
    b2 = DB.balance_for_identity(c, {"contactId": "C2"})
    n = len(DB.accounts(c))
    DB.rebuild_from_log(c)
    eq(DB.balance_for_identity(c, {"contactId": "C"}), b1, "merged acct after recover")
    eq(DB.balance_for_identity(c, {"contactId": "C2"}), b2, "other acct after recover")
    eq(len(DB.accounts(c)), n, "account count after recover")


if __name__ == "__main__":
    g, r, e = run_suite("Suite 02 — identity / aliases / merge (Postgres)", SUITE)
    sys.exit(1 if (r or e) else 0)
