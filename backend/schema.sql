-- Night at the Races — schema (spec §2)
-- Source of truth: an append-only ledger. Derived state (balances, accounts,
-- races) lives in ordinary cache tables written in the SAME transaction as the
-- log row, and can be rebuilt by replaying the log (see db.rebuild_from_log).
-- Money is integer cents everywhere. Never floats.

BEGIN;

-- ---------------------------------------------------------------------------
-- ledger_entries — source of truth, INSERT-only, never updated or deleted
-- ---------------------------------------------------------------------------
CREATE TABLE ledger_entries (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    type         TEXT         NOT NULL
                 CHECK (type IN ('open_account','attach_alias','merge',
                                 'topup','bet','payout')),
    account_id   TEXT,                 -- null only for pure alias/merge rows carrying it in meta
    delta_cents  BIGINT,               -- signed; + topup/payout, - bet (money rows only)
    race_id      TEXT,                 -- for bet / payout
    payment_ref  TEXT,                 -- Zeffy payment id, topups (idempotency key)
    source       TEXT CHECK (source IN ('zeffy','cash')),
    meta         JSONB   NOT NULL DEFAULT '{}'::jsonb
);

-- The idempotency guard: a redelivered webhook physically cannot create a
-- second credit for the same payment. (spec §3.2)
CREATE UNIQUE INDEX ledger_topup_ref_uniq
    ON ledger_entries (payment_ref)
    WHERE type = 'topup' AND payment_ref IS NOT NULL;

-- Append-only enforcement: the log is never updated or deleted. (spec §2)
CREATE OR REPLACE FUNCTION ledger_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only (% attempted)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_no_update BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION ledger_is_append_only();

-- ---------------------------------------------------------------------------
-- Derived / cache tables (written transactionally alongside the log)
-- ---------------------------------------------------------------------------
CREATE TABLE accounts (
    account_id  TEXT PRIMARY KEY,
    label       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The unique `alias` primary key enforces one-account-per-alias and makes
-- resolution an upsert. (spec §5)
CREATE TABLE account_aliases (
    alias       TEXT PRIMARY KEY,     -- e.g. contact:<uuid> or email:<normalized>
    account_id  TEXT NOT NULL REFERENCES accounts(account_id)
);
CREATE INDEX account_aliases_by_account ON account_aliases (account_id);

-- Read model for "how much can this person bet". (spec §2)
CREATE TABLE balances (
    account_id     TEXT PRIMARY KEY REFERENCES accounts(account_id),
    balance_cents  BIGINT NOT NULL DEFAULT 0 CHECK (balance_cents >= 0)
);

CREATE TABLE races (
    race_id        TEXT PRIMARY KEY,
    name           TEXT,
    state          TEXT NOT NULL DEFAULT 'open'
                   CHECK (state IN ('open','locked','settled')),
    lock_at        BIGINT,            -- logical clock in these suites (ms-ish)
    winning_horse  TEXT,
    players_share  NUMERIC NOT NULL DEFAULT 0.5,  -- players' fraction of the pot
    horses         JSONB NOT NULL DEFAULT '[]'::jsonb  -- [{"number":"3","name":"..."}]
);

CREATE TABLE bets (
    bet_id      BIGSERIAL PRIMARY KEY,
    account_id  TEXT NOT NULL,
    race_id     TEXT,
    horse       TEXT,
    cents       BIGINT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX bets_by_race ON bets (race_id);

-- Claim codes for the guest web app (spec §6/§8): a code handed out at check-in
-- or printed on the Zeffy receipt maps to one account, so a guest can claim a
-- signed session on their phone without a password.
CREATE TABLE claim_codes (
    code        TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(account_id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX claim_codes_by_account ON claim_codes (account_id);

COMMIT;
