ALTER TABLE transactions
ADD COLUMN transaction_type VARCHAR(10)
NOT NULL DEFAULT 'expense'
CONSTRAINT valid_transaction_type
check (transaction_type IN ('expense', 'income'));

