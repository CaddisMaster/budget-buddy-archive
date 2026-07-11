-- Phase: Safe-to-spend removal (v10.12.0)
--
-- Safe to spend (v10.9/v10.10) was removed from the app — the dashboard band,
-- compute_safe_to_spend(), and the per-account "Count in Safe to spend"
-- toggle are gone, so the account.spendable column (sql/23) has no reader
-- left. Drop it rather than keep a dead flag.
--
-- ⚠️ Unlike additive migrations, apply this AFTER the new image is running
-- (`docker compose pull && docker compose up -d` first, then this) — the OLD
-- code still SELECTs a.spendable and would 500 if the column vanished under
-- it. The new code never references the column, so order the other way round.
-- pg_dump backup first, as always.

ALTER TABLE account DROP COLUMN IF EXISTS spendable;
