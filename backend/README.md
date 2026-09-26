# Backend — Night at the Races

The betting-ledger backend, built spec-first: every rule in the four executable
test suites is ported to run against real Postgres, plus the tests the spec says
need real infrastructure (concurrency, live webhook, verification).

## Milestones

- **1 — schema + the three transactions (§3):** `place_bet`, `topup`, `settle`,
  identity resolve/merge (§5), crash recovery. Suites 01 & 02 green on Postgres.
- **2 — webhook receiver (§4):** the `map()` mapper (§4.2), the map→credit seam,
  and authenticity verification (§4.3 — re-fetch by id, optional HMAC). Suites
  03 & 04 green, plus a receiver test proving spoofed/tampered POSTs credit
  nothing.

Still to come (build order §12): pre-provisioning job (§4.1), guest web app +
auth (§6/§8), banker console (§7), deploy + venue-network load test (§9).

## Files

| File | What it is |
|---|---|
| `schema.sql` | Data model (§2): append-only `ledger_entries` + cache tables, idempotency index, one-account-per-alias key, append-only trigger. |
| `db.py` | Data layer: `place_bet` (§3.1), `topup` (§3.2), `settle` (§3.3), identity resolve/merge (§5), `rebuild_from_log` (§2/§9). |
| `mapper.py` | Pure `map_payment()` (§4.2): chips-only credit by `rate_id`, drinks excluded, identity keyed on contact UUID. **Refresh `BETTING_RATE_ID` each year.** |
| `zeffy_client.py` | Read-API client for re-fetch-by-id verification (+ `FakeZeffy` for tests). |
| `webhook.py` | Receiver core (§4.2/§4.3): verify → map → credit. Framework-agnostic. |
| `app.py` | Flask adapter serving `POST /webhooks/zeffy`. **Deploy-time only** — tests don't need it. |
| `tests/` | Suites 01–04 ported, plus concurrency, redelivery, and verification. |
| `run_all.py` | Applies the schema and runs all seven suites. |

## Run the tests

Requires Python 3.10+ and a Postgres you can reach. (No Flask needed — the tests
exercise `webhook.process_event` directly.)

```bash
pip install "psycopg[binary]"
export DATABASE_URL="postgresql://youruser@localhost:5432/postgres"
python run_all.py
```

Expected: `TOTAL: 51 pass · 0 fail · 0 error`.

`run_all.py` **drops and recreates** all tables each run — use a throwaway
database, never one with real data.

## Serve the webhook (deploy-time)

```bash
pip install flask
export ZEFFY_API_KEY=...            # read API, for re-fetch verification
export ZEFFY_WEBHOOK_SECRET=...     # optional, if Zeffy sends a signature
export DATABASE_URL=postgresql://...
flask --app app run                 # point Zeffy's webhook at /webhooks/zeffy
```

Must sit behind HTTPS in production (§4.3, §8). The receiver returns 200 fast;
Zeffy retries non-200 and idempotency makes retries harmless.

## Security notes (§4.3)

The webhook body is treated as an untrusted claim. Nothing in a POST is trusted
for crediting — the receiver re-fetches the payment by id from the read API and
credits only what that authoritative record says. An optional HMAC secret adds a
signature check in front of that. `ZEFFY_API_KEY` / `ZEFFY_WEBHOOK_SECRET` come
from the environment and must never be committed (already in `.gitignore`).
