-- Phase: account transfers + account-linked savings goals (v6.0)

-- 1. Transfers are modeled as a linked PAIR of transaction rows: an expense out
--    of account A and an income into account B, both flagged is_transfer = true
--    and sharing a transfer_group_id. is_transfer mirrors is_adjustment — the
--    rows still count toward account balances but are excluded from the
--    income/expense analytics so transfers don't inflate real income/spending.
ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS is_transfer BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE transactions
    ADD COLUMN IF NOT EXISTS transfer_group_id INTEGER;

-- 2. One nextval per transfer ties its two legs together.
CREATE SEQUENCE IF NOT EXISTS transfer_group_seq;

-- 3. Savings goals, each linked to an account. Progress is derived live:
--    saved = current account balance - baseline_amount. A transfer into the
--    linked account therefore advances the goal automatically. baseline_amount
--    lets a goal either count the account's existing balance (0) or start fresh
--    (= balance at creation), so goals can run back-to-back on one account.
CREATE TABLE IF NOT EXISTS goals (
    id SERIAL PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    target_amount NUMERIC(10,2) NOT NULL,
    target_date DATE,
    account_id INTEGER NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    baseline_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
