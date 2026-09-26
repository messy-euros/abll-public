"""NEW — true concurrency (spec §10). The browser suites ran single-threaded and
could only *model* the overspend guard. Here N real threads with N real DB
connections hammer one account at once; the row-lock in §3.1 must hold."""
import sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, D, reset, run_suite  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402

FUTURE = 10 ** 15


def _parallel(n, worker):
    """Run worker(i) in n threads, each with its own connection, released at a
    barrier so they truly contend. Returns the list of results."""
    results = [None] * n
    barrier = threading.Barrier(n)

    def run(i):
        conn = DB.connect()
        try:
            barrier.wait()
            results[i] = worker(i, conn)
        finally:
            conn.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def drain_one_race(c):
    """$100 balance, 50 simultaneous $10 bets on one race → exactly 10 win,
    balance lands at 0, never negative."""
    DB.ensure_account(c, "whale")
    DB.topup_account(c, "whale", D(100), "cash")
    DB.open_race(c, "R", FUTURE)
    c.commit()

    res = _parallel(50, lambda i, conn: DB.place_bet(conn, "whale", "R", "3", D(10), 1))
    wins = sum(1 for r in res if r and r["ok"])

    eq(wins, 10, "exactly 10 of 50 $10 bets should win against a $100 balance")
    eq(DB.balance(c, "whale"), 0, "final balance must be exactly 0")
    nbets = c.execute("SELECT count(*) FROM bets WHERE race_id='R'").fetchone()[0]
    eq(nbets, 10, "exactly 10 bet rows recorded")
    ok(DB.balance(c, "whale") >= 0, "balance never negative")


def full_balance_across_races(c):
    """Isolate the *balance* row-lock (not the race lock): 20 threads each bet
    the FULL $100 balance, each on its own race → exactly one can win."""
    DB.ensure_account(c, "whale")
    DB.topup_account(c, "whale", D(100), "cash")
    for i in range(20):
        DB.open_race(c, f"R{i}", FUTURE)
    c.commit()

    res = _parallel(
        20, lambda i, conn: DB.place_bet(conn, "whale", f"R{i}", "3", D(100), 1)
    )
    wins = sum(1 for r in res if r and r["ok"])

    eq(wins, 1, "only ONE full-balance bet may win across 20 concurrent races")
    eq(DB.balance(c, "whale"), 0, "final balance must be exactly 0")


SUITE = [
 ("50 concurrent $10 bets on a $100 balance -> exactly 10 win, no overspend",
  drain_one_race),
 ("20 concurrent full-balance bets across races -> exactly 1 wins",
  full_balance_across_races),
]


if __name__ == "__main__":
    g, r, e = run_suite("NEW — true concurrency (Postgres, real threads)", SUITE)
    sys.exit(1 if (r or e) else 0)
