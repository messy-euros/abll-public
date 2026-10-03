"""NEW — live webhook redelivery (spec §10, §3.2). A real redelivery storm:
many concurrent POSTs of the SAME payment must credit exactly once. The unique
partial index on payment_ref is the guard; here it's tested under real
contention, not modelled single-threaded."""
import sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, D, run_suite  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402


def _parallel(n, worker):
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


def redelivery_storm(c):
    """30 concurrent deliveries of the SAME payment_ref -> exactly one credit."""
    DB.ensure_account(c, "guest")
    c.commit()

    res = _parallel(
        30, lambda i, conn: DB.topup_account(conn, "guest", D(10), "zeffy", "ZFY-SAME")
    )
    credited = sum(1 for r in res if r and r["ok"] and not r["duplicate"])

    eq(credited, 1, "exactly one of 30 identical deliveries may credit")
    eq(DB.balance(c, "guest"), D(10), "balance reflects a single $10 credit")
    rows = c.execute(
        "SELECT count(*) FROM ledger_entries WHERE type='topup' AND payment_ref='ZFY-SAME'"
    ).fetchone()[0]
    eq(rows, 1, "exactly one topup row exists for the ref")


def distinct_refs_all_land(c):
    """Sanity: 30 concurrent DISTINCT payments all credit."""
    DB.ensure_account(c, "guest")
    c.commit()

    res = _parallel(
        30, lambda i, conn: DB.topup_account(conn, "guest", D(10), "zeffy", f"ZFY-{i}")
    )
    credited = sum(1 for r in res if r and r["ok"] and not r["duplicate"])

    eq(credited, 30, "all 30 distinct payments should credit")
    eq(DB.balance(c, "guest"), D(300), "balance reflects 30 x $10")


SUITE = [
 ("30 concurrent redeliveries of one payment -> credited exactly once",
  redelivery_storm),
 ("30 concurrent distinct payments -> all credited",
  distinct_refs_all_land),
]


if __name__ == "__main__":
    g, r, e = run_suite("NEW — webhook redelivery idempotency (Postgres)", SUITE)
    sys.exit(1 if (r or e) else 0)
