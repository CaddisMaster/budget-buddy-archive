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
    created_at TIMESTAMP DEFAULT NOW()
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
CREATE TABLE public.budgets (
    id SERIAL PRIMARY KEY,
    category_id integer NOT NULL,
    amount numeric(10,2) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT valid_period CHECK (period_end > period_start)
);

-- ------------------------------------------------------------
-- Goals (account-linked savings goals)
-- ------------------------------------------------------------
CREATE TABLE public.goals (
    id SERIAL PRIMARY KEY,
    name character varying(80) NOT NULL,
    target_amount numeric(10,2) NOT NULL,
    target_date date,
    account_id integer NOT NULL REFERENCES account(account_id) ON DELETE CASCADE,
    baseline_amount numeric(10,2) NOT NULL DEFAULT 0,
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