ALTER TABLE transactions 
ADD CONSTRAINT fk_transactions_category
fOREIGN KEY (category_id)
REFERENCES categories (id)
on DELETE RESTRICT;


ALTER TABLE budgets
ADD CONSTRAINT fk_budgets_category
FOREIGN KEY (category_id)
REFERENCES categories (id)
ON DELETE RESTRICT;

