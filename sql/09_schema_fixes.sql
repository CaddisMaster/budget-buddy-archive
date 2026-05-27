-- Create sequences for auto-incrementing primary keys
CREATE SEQUENCE IF NOT EXISTS transactions_id_seq;
CREATE SEQUENCE IF NOT EXISTS categories_id_seq;
CREATE SEQUENCE IF NOT EXISTS account_id_seq;

-- Link sequences to id columns
ALTER TABLE transactions ALTER COLUMN id SET DEFAULT nextval('transactions_id_seq');
ALTER TABLE categories ALTER COLUMN id SET DEFAULT nextval('categories_id_seq');
ALTER TABLE account ALTER COLUMN account_id SET DEFAULT nextval('account_id_seq');

-- Add primary keys
ALTER TABLE transactions ADD PRIMARY KEY (id);
ALTER TABLE categories ADD PRIMARY KEY (id);

-- Add missing account_id column to transactions
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS account_id INTEGER;

-- Add foreign key constraints
ALTER TABLE transactions ADD CONSTRAINT fk_transactions_category FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE RESTRICT;
ALTER TABLE transactions ADD CONSTRAINT fk_transactions_account FOREIGN KEY (account_id) REFERENCES account (account_id) ON DELETE RESTRICT;
ALTER TABLE budgets ADD CONSTRAINT fk_budgets_category FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE RESTRICT;