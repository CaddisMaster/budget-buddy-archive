-- v10.9 Debt-payoff goals: a goal is either saving UP toward a target ('save',
-- the only kind until now) or paying a balance DOWN to $0 ('payoff'). A payoff
-- goal snapshots at creation: baseline_amount = the account's (negative) balance
-- and target_amount = the starting debt (−balance), so the existing projection
-- math runs unchanged — the type only drives wording and the creation flow.
-- Purely additive; existing rows default to 'save'.
ALTER TABLE goals ADD COLUMN goal_type VARCHAR(10) NOT NULL DEFAULT 'save'
    CHECK (goal_type IN ('save', 'payoff'));
