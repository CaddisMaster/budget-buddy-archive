CREATE TABLE public.account (
    account_id SERIAL PRIMARY KEY,
    account_name character varying(50) NOT NULL,
    type character varying(50) NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);