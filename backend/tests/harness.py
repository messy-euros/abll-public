"""Minimal test harness — mirrors the browser suites' 'N pass · N fail' output.
Each test gets a clean database (like imp.make() gives a fresh ledger)."""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402


class AssertionFail(Exception):
    pass


def ok(cond, msg="assertion failed"):
    if not cond:
        raise AssertionFail(msg)


def eq(a, b, msg="value"):
    if a != b:
        raise AssertionFail(f"{msg}: expected {b!r}, got {a!r}")


def D(dollars):
    return round(dollars * 100)


def reset(conn):
    conn.execute(
        "TRUNCATE ledger_entries, account_aliases, balances, bets, races, accounts "
        "RESTART IDENTITY CASCADE"
    )
    conn.commit()


def run_suite(title, tests):
    """tests: list of (name, fn). fn receives a fresh connection."""
    conn = DB.connect()
    g = r = e = 0
    print(f"\n=== {title} ===")
    for name, fn in tests:
        reset(conn)
        try:
            fn(conn)
            g += 1
            print(f"  PASS  {name}")
        except AssertionFail as ex:
            r += 1
            print(f"  FAIL  {name}\n          {ex}")
        except Exception as ex:  # noqa: BLE001
            e += 1
            print(f"  ERROR {name}\n          {type(ex).__name__}: {ex}")
            traceback.print_exc()
    print(f"  ---> {g} pass · {r} fail · {e} error   ({len(tests)} tests)")
    conn.close()
    return g, r, e
