-- Phase: Safe-to-spend evaluation — per-account spendable toggle (v10.10.0)
--
-- The v10.9 Safe to spend counted every Bank/Debit balance as spendable cash,
-- but a savings account typed "Bank Account" is money you're deliberately NOT
-- spending. Rather than a new account type, each account gets a "count in
-- Safe to spend" toggle; unticking it removes the account from the liquid set
-- (and, via the existing liquid→non-liquid rule, makes scheduled transfers
-- INTO it count as money leaving the spendable pool). Default true = zero
-- behavior change until an account is unticked. Purely additive.
--
-- Apply BY HAND to prod (pg_dump backup first) BEFORE pulling the new image,
-- so the new code can read `spendable` on the very first load.

BEGIN;

ALTER TABLE public.account
    ADD COLUMN IF NOT EXISTS spendable boolean NOT NULL DEFAULT true;

COMMIT;
