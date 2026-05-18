alter table transactions
ADD CONSTRAINT fk_transactions_account
FOREIGN KEY (account_id)
REFERENCES account (account_id)
ON DELETE RESTRICT;