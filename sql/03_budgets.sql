CREATE TABLE public.budgets (
    category_id integer NOT NULL,
    amount numeric(10,2) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT valid_period CHECK ((period_end > period_start))
);