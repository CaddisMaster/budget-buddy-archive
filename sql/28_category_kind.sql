-- v10.12 Income categories: a category is now explicitly an expense or an
-- income category ('kind'), killing the implicit "categories are effectively
-- expense categories" rule — the budget cockpit/review and Auto-Categorize
-- list expense-kind only, the dashboard Spending card gains an income view,
-- and Ask's total_for_category sums by the category's kind.
-- Purely additive; existing rows default to 'expense', and the conventional
-- 'Income' category everyone starts with is migrated to income-kind.
-- Ship-day order: apply BEFORE the image pull (normal additive order — unlike
-- sql/27, the spendable DROP, which goes AFTER the pull).
ALTER TABLE categories ADD COLUMN kind VARCHAR(10) NOT NULL DEFAULT 'expense'
    CHECK (kind IN ('expense', 'income'));

UPDATE categories SET kind = 'income' WHERE lower(name) = 'income';
