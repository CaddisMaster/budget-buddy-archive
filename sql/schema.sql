-- ============================================================
-- Budget Buddy — Complete Database Schema
-- Run this single file on a fresh database to set up everything
-- ============================================================
 
-- ------------------------------------------------------------
-- Categories
-- ------------------------------------------------------------
CREATE TABLE public.categories (
    id SERIAL PRIMARY KEY,
    name character varying(50) NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now()
);
 
-- ------------------------------------------------------------
-- Account
-- ------------------------------------------------------------
CREATE TABLE public.account (
    account_id SERIAL PRIMARY KEY,
    account_name character varying(50) NOT NULL,
    type character varying(50) NOT NULL,
    created_at timestamp without time zone DEFAULT now()
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
    frequency VARCHAR(10) DEFAULT NULL,
    next_due DATE DEFAULT NULL,
    CONSTRAINT valid_transaction_type CHECK (transaction_type IN ('expense', 'income'))
);
 
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
    CONSTRAINT valid_period CHECK (period_end > period_start)
);
 
-- ------------------------------------------------------------
-- Users
-- ------------------------------------------------------------
CREATE TABLE public.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW()
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