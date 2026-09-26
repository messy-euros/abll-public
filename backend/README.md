# Backend — Night at the Races

The betting-ledger backend, built spec-first: every rule in the four executable
test suites is ported to run against real Postgres, plus the tests the spec says
need real infrastructure (concurrency, live webhook, verification, provisioning).

## Milestones (build order §12)

- **1 — schema + the three transactions (§3):** `place_bet`, `topup`, `settle`,
  identity resolve/merge (§5), crash recovery. Suites 01 & 02 green on Postgres.
- **2 — webhook receiver (§4):** the `map()` mapper (§4.2), the map→credit seam,
  and authenticity verification (§4.3 — re-fetch by id, optional HMAC). Suites
  03 & 04 green, plus spoofed/tampered POSTs credit nothing.
- **3 — pre-provisioning (§4.1):** pull the campaign's contacts + payments the
  night before; register everyone and pre-load chips. Idempotent on re-run and
  shares idempotency with the live webhook.
- **4 — guest web app (§6/§8):** the QR-launched phone page. Claim by email or
  check-in code → a signed session bound to the account; tap a horse, bet chips.
  Every guard (overspend, late tap) lives in the §3.1 transaction, never trusted
  from the phone. Winnings land back on settle.

Still to come: banker console (§7), deploy + venue-network load test (§9).

## Files

| File | What it is |
|---|---|
| `schema.sql` | Data model (§2): append-only `ledger_entries` + cache tables, idempotency index, one-account-per-alias key, append-only trigger. |
| `db.py` | Data layer: `place_bet` (§3.1), `topup` (§3.2), `settle` (§3.3), identity resolve/merge (§5), `rebuild_from_log` (§2/§9). |
| `mapper.py` | Pure `map_payment()` (§4.2): chips-only credit by `rate_id`, drinks excluded, keyed on contact UUID. **Refresh `BETTING_RATE_ID` each year.** |
| `zeffy_client.py` | Read-API client: re-fetch-by-id + list campaigns/payments/contacts (+ `FakeZeffy` for tests). |
| `webhook.py` | Receiver core (§4.2/§4.3): verify → map → credit. Framework-agnostic. |
| `provision.py` | Pre-provisioning job (§4.1). Runnable CLI with `--dry-run`. |
| `guest_api.py` | Guest app core (§6): claim, state, place bet in chips. |
| `sessions.py` | HMAC-signed guest sessions bound to an account (§6/§8). |
| `guest.html` | The QR-launched phone page (served by `app.py`). |
| `app.py` | Flask adapter: webhook + guest routes + serves the page. **Deploy-time only.** |
| `tests/` | Suites 01–04 ported, plus provisioning, guest app, concurrency, redelivery, verification. |
| `run_all.py` | Applies the schema and runs all nine suites. |

## Run the tests

Requires Python 3.10+ and a Postgres you can reach. (No Flask needed.)

```bash
pip install "psycopg[binary]"
export DATABASE_URL="postgresql://youruser@localhost:5432/postgres"
python run_all.py
```

Expected: `TOTAL: 68 pass · 0 fail · 0 error`.

`run_all.py` **drops and recreates** all tables each run — use a throwaway
database, never one with real data.

## Pre-provision the night before (§4.1)

```bash
export ZEFFY_API_KEY=...             # read-only key; never commit it
export DATABASE_URL=postgresql://... # your REAL event database
python provision.py --dry-run              # preview: counts + total chips, no writes
python provision.py --match "night at the races"   # do it for real
```

Safe to run more than once (idempotent), and safe to run while the live webhook
is active — a payment is credited exactly once regardless of which path sees it
first (shared `payment_ref` guard).

## Serve the webhook (deploy-time)

```bash
pip install flask
export ZEFFY_API_KEY=...             # read API, for re-fetch verification
export ZEFFY_WEBHOOK_SECRET=...      # optional, if Zeffy sends a signature
export DATABASE_URL=postgresql://...
flask --app app run                  # point Zeffy's webhook at /webhooks/zeffy
```

Behind HTTPS in production (§4.3, §8). Returns 200 fast; Zeffy retries non-200
and idempotency makes retries harmless.

## Guest app (§6)

The same `flask --app app run` serves the guest phone page at `/` (the QR
target) and its API: `POST /api/claim` (email or check-in code → signed session),
`GET /api/state`, `POST /api/bet`. Set a real `GUEST_SESSION_SECRET` in
production. Issue check-in codes with `db.issue_claim_code(conn, account_id)`.

## Security notes (§4.3)

The webhook body is an untrusted claim — the receiver re-fetches the payment by
id and credits only what that authoritative record says. `ZEFFY_API_KEY` /
`ZEFFY_WEBHOOK_SECRET` come from the environment and must never be committed
(already in `.gitignore`).
