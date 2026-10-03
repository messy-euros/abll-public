"""Suite 01 — pot/settle ledger, ported verbatim against Postgres (spec §3, §10).
The player string is the account id here (as in the browser suite). Same 13
assertions; the safety they only *modelled* single-threaded is now real DB
transactions. See test_concurrency.py for the part the browser could not test."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, D, run_suite  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402

FUTURE = 10 ** 15  # a lock_at far in the (logical) future


def topup(conn, player, cents, src, ref=None):
    DB.ensure_account(conn, player)
    return DB.topup_account(conn, player, cents, src, ref)


SUITE = [
 ("new player has a zero balance",
  lambda c: eq(DB.balance(c, "nobody"), 0, "balance")),

 ("a top-up increases the balance", lambda c: (
  topup(c, "jane", D(100), "zeffy", "r1"),
  eq(DB.balance(c, "jane"), D(100), "balance"))[-1]),

 ("top-ups accumulate", lambda c: (
  topup(c, "jane", D(100), "zeffy", "r1"),
  topup(c, "jane", D(40), "cash"),
  eq(DB.balance(c, "jane"), D(140), "balance"))[-1]),

 ("a bet debits the balance", lambda c: (
  topup(c, "jane", D(100), "zeffy", "r1"),
  DB.open_race(c, "R", FUTURE),
  ok(DB.place_bet(c, "jane", "R", "3", D(30), 1)["ok"], "bet should be accepted"),
  eq(DB.balance(c, "jane"), D(70), "balance"))[-1]),

 ("a single bet over balance is rejected", lambda c: (
  topup(c, "jane", D(20), "cash"),
  DB.open_race(c, "R", FUTURE),
  ok(not DB.place_bet(c, "jane", "R", "3", D(30), 1)["ok"], "over-balance rejected"),
  eq(DB.balance(c, "jane"), D(20), "balance untouched"))[-1]),

 ("no double-spend: two full-balance bets, second rejected", lambda c: (
  topup(c, "jane", D(100), "zeffy", "r1"),
  DB.open_race(c, "R", FUTURE),
  ok(DB.place_bet(c, "jane", "R", "3", D(100), 1)["ok"], "first should pass"),
  ok(not DB.place_bet(c, "jane", "R", "5", D(100), 1)["ok"], "second rejected"),
  ok(DB.balance(c, "jane") >= 0, "balance must not go negative"))[-1]),

 ("a bet on a locked race is rejected", lambda c: (
  topup(c, "sam", D(50), "cash"),
  DB.open_race(c, "R", FUTURE),
  DB.lock_race(c, "R"),
  ok(not DB.place_bet(c, "sam", "R", "2", D(10), 1)["ok"], "locked rejects"))[-1]),

 ("a bet past post time is rejected", lambda c: (
  topup(c, "sam", D(50), "cash"),
  DB.open_race(c, "R", 100),
  ok(not DB.place_bet(c, "sam", "R", "2", D(10), 200)["ok"], "past post rejects"))[-1]),

 ("duplicate payment webhook is idempotent", lambda c: (
  topup(c, "lee", D(100), "zeffy", "ZFY-1"),
  topup(c, "lee", D(100), "zeffy", "ZFY-1"),
  eq(DB.balance(c, "lee"), D(100), "must credit the ref only once"))[-1]),

 ("crash then recover preserves balances", lambda c: _crash_recover(c)),

 ("settle conserves money exactly (incl. rounding)", lambda c: _conserve(c)),

 ("payout is proportional to stake", lambda c: _proportional(c)),

 ("hostile input (negative / zero / NaN) is rejected", lambda c: (
  topup(c, "x", D(50), "cash"),
  DB.open_race(c, "R", FUTURE),
  ok(not DB.place_bet(c, "x", "R", "3", -D(10), 1)["ok"], "negative rejected"),
  ok(not DB.place_bet(c, "x", "R", "3", 0, 1)["ok"], "zero rejected"),
  ok(not DB.place_bet(c, "x", "R", "3", float("nan"), 1)["ok"], "NaN rejected"),
  eq(DB.balance(c, "x"), D(50), "balance untouched"))[-1]),
]


def _crash_recover(c):
    topup(c, "a", D(100), "zeffy", "r1")
    topup(c, "b", D(40), "cash")
    DB.open_race(c, "R", FUTURE)
    DB.place_bet(c, "a", "R", "3", D(30), 1)
    DB.place_bet(c, "b", "R", "3", D(10), 1)
    DB.settle(c, "R", "3")
    before = (DB.balance(c, "a"), DB.balance(c, "b"))
    DB.rebuild_from_log(c)  # wipe cache tables, replay the log
    eq(DB.balance(c, "a"), before[0], "a after recover")
    eq(DB.balance(c, "b"), before[1], "b after recover")


def _conserve(c):
    for i, p in enumerate(["a", "b", "cc"]):
        topup(c, p, D(100), "zeffy", "r" + str(i))
    DB.open_race(c, "R", FUTURE)
    DB.place_bet(c, "a", "R", "3", D(30), 1)
    DB.place_bet(c, "b", "R", "3", D(10), 1)
    DB.place_bet(c, "cc", "R", "7", D(30), 1)
    s = DB.settle(c, "R", "3")
    paid = sum(p["cents"] for p in s["payouts"])
    eq(paid + s["houseCut"], s["pot"], "payouts + house must equal pot")


def _proportional(c):
    for i, p in enumerate(["a", "b", "cc"]):
        topup(c, p, D(100), "zeffy", "r" + str(i))
    DB.open_race(c, "R", FUTURE)
    DB.place_bet(c, "a", "R", "3", D(40), 1)
    DB.place_bet(c, "b", "R", "3", D(20), 1)
    DB.place_bet(c, "cc", "R", "1", D(40), 1)
    s = DB.settle(c, "R", "3")
    pa = next(p["cents"] for p in s["payouts"] if p["player"] == "a")
    pb = next(p["cents"] for p in s["payouts"] if p["player"] == "b")
    ok(abs(pa / pb - 2) < 0.02, f"$40 winner ~2x the $20 winner (got {pa/pb:.2f}x)")


if __name__ == "__main__":
    g, r, e = run_suite("Suite 01 — pot/settle ledger (Postgres)", SUITE)
    sys.exit(1 if (r or e) else 0)
