CREATE TABLE public.transactions (
    id integer NOT NULL,
    amount numeric(10,2) NOT NULL,
    description text,
    category_id integer,
    transaction_date date DEFAULT CURRENT_DATE NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);
