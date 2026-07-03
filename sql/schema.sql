-- ============================================================
-- Budget Buddy — Complete Database Schema
-- Run this single file on a fresh database to set up everything
-- ============================================================
 
-- ------------------------------------------------------------
-- Users (must exist before tables that reference it)
-- ------------------------------------------------------------
CREATE TABLE public.users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW(),
    email TEXT,
    weekly_digest BOOLEAN NOT NULL DEFAULT false,
    last_digest_sent_on DATE
);

-- ------------------------------------------------------------
-- Categories
-- ------------------------------------------------------------
CREATE TABLE public.categories (
    id SERIAL PRIMARY KEY,
    name character varying(50) NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Account
-- ------------------------------------------------------------
CREATE TABLE public.account (
    account_id SERIAL PRIMARY KEY,
    account_name character varying(50) NOT NULL,
    type character varying(50) NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Transactions
-- ------------------------------------------------------------
CREATE TABLE public.transactions (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    category_id integer,
    account_id integer,
    transaction_date date NOT NULL DEFAULT CURRENT_DATE,
    transaction_type character varying(10) NOT NULL DEFAULT 'expense',
    created_at timestamp without time zone DEFAULT now(),
    is_recurring BOOLEAN NOT NULL DEFAULT false,
    frequency VARCHAR(20) DEFAULT NULL,
    next_due DATE DEFAULT NULL,
    recur_second_day SMALLINT DEFAULT NULL,
    is_adjustment BOOLEAN NOT NULL DEFAULT false,
    is_transfer BOOLEAN NOT NULL DEFAULT false,
    transfer_group_id integer,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT valid_transaction_type CHECK (transaction_type IN ('expense', 'income'))
);

-- Ties the two legs of a transfer together (one nextval per transfer).
CREATE SEQUENCE public.transfer_group_seq;

-- ------------------------------------------------------------
-- Budgets
-- ------------------------------------------------------------
-- One row per (user_id, category_id) holding a single monthly amount; stores
-- overrides only — a category with no row falls back to its suggested default.
CREATE TABLE public.budgets (
    id SERIAL PRIMARY KEY,
    category_id integer NOT NULL,
    amount numeric(10,2) NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT uq_budget_user_category UNIQUE (user_id, category_id)
);

-- ------------------------------------------------------------
-- Goals (account-linked; save UP toward a target or pay a balance DOWN to $0)
-- ------------------------------------------------------------
-- goal_type 'payoff' (v10.9, sql/20) snapshots at creation: baseline_amount =
-- the account's (negative) balance, target_amount = the starting debt — so the
-- projection math is shared with 'save' and the type only drives wording.
CREATE TABLE public.goals (
    id SERIAL PRIMARY KEY,
    name character varying(80) NOT NULL,
    target_amount numeric(10,2) NOT NULL,
    target_date date,
    account_id integer NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    baseline_amount numeric(10,2) NOT NULL DEFAULT 0,
    goal_type VARCHAR(10) NOT NULL DEFAULT 'save'
        CHECK (goal_type IN ('save', 'payoff')),
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Schedules (recurring income & expense templates, v10.0)
-- ------------------------------------------------------------
-- A schedule is NOT a ledger row — it generates a plain transaction on each
-- due date (run_due_schedules). frequency + anchor_day/second_day (semimonthly)
-- + next_due drive generation. The transactions.is_recurring/frequency/
-- next_due/recur_second_day columns above are legacy as of v10 (always default).
CREATE TABLE public.schedules (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    category_id integer REFERENCES categories(id) ON DELETE RESTRICT,
    account_id integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    transaction_type character varying(10) NOT NULL DEFAULT 'expense'
        CHECK (transaction_type IN ('expense', 'income')),
    frequency character varying(20) NOT NULL,
    anchor_day smallint,
    second_day smallint,
    next_due date NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Foreign Key Constraints
-- ------------------------------------------------------------
ALTER TABLE transactions
    ADD CONSTRAINT fk_transactions_category
    FOREIGN KEY (category_id)
    REFERENCES categories (id)
    ON DELETE RESTRICT;

ALTER TABLE transactions
    ADD CONSTRAINT fk_transactions_account
    FOREIGN KEY (account_id)
    REFERENCES account (account_id)
    ON DELETE RESTRICT;

ALTER TABLE budgets
    ADD CONSTRAINT fk_budgets_category
    FOREIGN KEY (category_id)
    REFERENCES categories (id)
    ON DELETE RESTRICT;

-- ------------------------------------------------------------
-- Insights (v10.1 — cached monthly AI digest, one row per user per month)
-- content is narrative JSON {"summary": ..., "tips": [...]}; the figures it
-- describes are recomputed deterministically server-side, never stored here.
-- ------------------------------------------------------------
CREATE TABLE public.insights (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year smallint NOT NULL,
    month smallint NOT NULL,
    content text NOT NULL,
    model character varying(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_insight_user_period UNIQUE (user_id, year, month)
);

-- v10.2 Forecast — cached month-ahead AI projection narrative (see sql/16).
CREATE TABLE public.forecasts (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year smallint NOT NULL,
    month smallint NOT NULL,
    content text NOT NULL,
    model character varying(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_forecast_user_period UNIQUE (user_id, year, month)
);

-- Goal Coach — cached AI narration of savings-goal pace, one row per
-- (user, month) (see sql/19). Twin of insights/forecasts, pointed at /goals.
CREATE TABLE public.goal_coach (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year smallint NOT NULL,
    month smallint NOT NULL,
    content text NOT NULL,
    model character varying(50),
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_goal_coach_user_period UNIQUE (user_id, year, month)
);

-- ------------------------------------------------------------
-- Transfer schedules (v10.4 — recurring transfers; see sql/17)
-- The transfer-tab twin of `schedules`: run_due_transfers() materializes a
-- paired expense+income transfer (shared transfer_group_id, is_transfer=true) on
-- each due date going forward. Two accounts, no category → its own table.
-- ------------------------------------------------------------
CREATE TABLE public.transfer_schedules (
    id SERIAL PRIMARY KEY,
    amount numeric(10,2) NOT NULL,
    description text,
    from_account_id integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    to_account_id   integer NOT NULL REFERENCES account(account_id) ON DELETE RESTRICT,
    frequency character varying(20) NOT NULL,
    anchor_day smallint,
    second_day smallint,
    next_due date NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE
);