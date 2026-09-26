"""Apply the schema, then run all four suites against Postgres.

    export DATABASE_URL="postgres://user:pass@host:5432/natr"   # optional
    python run_all.py

Exits non-zero if anything is red. Milestone-1 acceptance for spec §3/§5/§10."""
import os
import sys
import pathlib
import db as DB

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE / "tests"))


def apply_schema():
    sql = (HERE / "schema.sql").read_text()
    conn = DB.connect()
    conn.autocommit = True
    # drop everything so the run is repeatable
    conn.execute(
        "DROP TABLE IF EXISTS bets, balances, account_aliases, races, "
        "ledger_entries, accounts CASCADE"
    )
    conn.execute("DROP FUNCTION IF EXISTS ledger_is_append_only() CASCADE")
    conn.execute(sql)
    conn.close()


def main():
    apply_schema()
    import test_pot_settle
    import test_identity
    import test_concurrency
    import test_webhook_idempotency
    from harness import run_suite

    totals = [0, 0, 0]
    for title, suite in [
        ("Suite 01 - pot/settle ledger (Postgres)", test_pot_settle.SUITE),
        ("Suite 02 - identity / aliases / merge (Postgres)", test_identity.SUITE),
        ("NEW - true concurrency (real threads)", test_concurrency.SUITE),
        ("NEW - webhook redelivery idempotency", test_webhook_idempotency.SUITE),
    ]:
        g, r, e = run_suite(title, suite)
        totals[0] += g
        totals[1] += r
        totals[2] += e

    print("\n" + "=" * 60)
    print(f"TOTAL: {totals[0]} pass · {totals[1]} fail · {totals[2]} error")
    print("=" * 60)
    sys.exit(1 if (totals[1] or totals[2]) else 0)


if __name__ == "__main__":
    main()
