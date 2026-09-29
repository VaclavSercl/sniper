--
-- PostgreSQL database dump
--

\restrict u3cRGbymp0T7DfZjqvsE8WOZltoNwqdGLOLqvfs12Dd5RrMyZJRgzR7F2Ll0foj

-- Dumped from database version 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1)
-- Dumped by pg_dump version 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: fills; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.fills (
    id bigint NOT NULL,
    order_id bigint NOT NULL,
    venue_fill_id text,
    qty numeric(20,8) NOT NULL,
    price numeric(20,8) NOT NULL,
    fee numeric(20,8),
    fee_currency text,
    filled_at timestamp with time zone NOT NULL
);


ALTER TABLE public.fills OWNER TO postgres;

--
-- Name: fills_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.fills_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.fills_id_seq OWNER TO postgres;

--
-- Name: fills_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.fills_id_seq OWNED BY public.fills.id;


--
-- Name: ledger; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.ledger (
    seq bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    kind text NOT NULL,
    currency text NOT NULL,
    amount numeric(20,8) NOT NULL,
    balance_after numeric(20,8),
    ref text,
    prev_hash text NOT NULL,
    entry_hash text NOT NULL,
    CONSTRAINT no_update CHECK (true)
);


ALTER TABLE public.ledger OWNER TO postgres;

--
-- Name: ledger_seq_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.ledger_seq_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ledger_seq_seq OWNER TO postgres;

--
-- Name: ledger_seq_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.ledger_seq_seq OWNED BY public.ledger.seq;


--
-- Name: orders; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.orders (
    id bigint NOT NULL,
    client_order_id text NOT NULL,
    venue text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    type text NOT NULL,
    qty numeric(20,8) NOT NULL,
    price numeric(20,8),
    status text DEFAULT 'INTENT'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT orders_side_check CHECK ((side = ANY (ARRAY['buy'::text, 'sell'::text]))),
    CONSTRAINT orders_type_check CHECK ((type = ANY (ARRAY['market'::text, 'limit'::text])))
);


ALTER TABLE public.orders OWNER TO postgres;

--
-- Name: orders_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.orders_id_seq OWNER TO postgres;

--
-- Name: orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.orders_id_seq OWNED BY public.orders.id;


--
-- Name: reports; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.reports (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    delivered_at timestamp with time zone
);


ALTER TABLE public.reports OWNER TO postgres;

--
-- Name: reports_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.reports_id_seq OWNER TO postgres;

--
-- Name: reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.reports_id_seq OWNED BY public.reports.id;


--
-- Name: research_trials; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.research_trials (
    id bigint NOT NULL,
    registered_at timestamp with time zone DEFAULT now() NOT NULL,
    hypothesis text NOT NULL,
    params jsonb,
    code_hash text,
    data_hash text,
    data_as_of date,
    result jsonb
);


ALTER TABLE public.research_trials OWNER TO postgres;

--
-- Name: research_trials_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.research_trials_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.research_trials_id_seq OWNER TO postgres;

--
-- Name: research_trials_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.research_trials_id_seq OWNED BY public.research_trials.id;


--
-- Name: fills id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.fills ALTER COLUMN id SET DEFAULT nextval('public.fills_id_seq'::regclass);


--
-- Name: ledger seq; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ledger ALTER COLUMN seq SET DEFAULT nextval('public.ledger_seq_seq'::regclass);


--
-- Name: orders id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orders ALTER COLUMN id SET DEFAULT nextval('public.orders_id_seq'::regclass);


--
-- Name: reports id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reports ALTER COLUMN id SET DEFAULT nextval('public.reports_id_seq'::regclass);


--
-- Name: research_trials id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.research_trials ALTER COLUMN id SET DEFAULT nextval('public.research_trials_id_seq'::regclass);


--
-- Name: fills fills_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fills_pkey PRIMARY KEY (id);


--
-- Name: ledger ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.ledger
    ADD CONSTRAINT ledger_pkey PRIMARY KEY (seq);


--
-- Name: orders orders_client_order_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_client_order_id_key UNIQUE (client_order_id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: reports reports_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_pkey PRIMARY KEY (id);


--
-- Name: research_trials research_trials_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.research_trials
    ADD CONSTRAINT research_trials_pkey PRIMARY KEY (id);


--
-- Name: fills fills_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fills_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: TABLE fills; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE public.fills TO beroun;


--
-- Name: SEQUENCE fills_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.fills_id_seq TO beroun;


--
-- Name: TABLE ledger; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE public.ledger TO beroun;


--
-- Name: SEQUENCE ledger_seq_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.ledger_seq_seq TO beroun;


--
-- Name: TABLE orders; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE public.orders TO beroun;


--
-- Name: SEQUENCE orders_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.orders_id_seq TO beroun;


--
-- Name: TABLE reports; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE public.reports TO beroun;


--
-- Name: COLUMN reports.delivered_at; Type: ACL; Schema: public; Owner: postgres
--

GRANT UPDATE(delivered_at) ON TABLE public.reports TO beroun;


--
-- Name: SEQUENCE reports_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.reports_id_seq TO beroun;


--
-- Name: TABLE research_trials; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT ON TABLE public.research_trials TO beroun;


--
-- Name: SEQUENCE research_trials_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.research_trials_id_seq TO beroun;


--
-- PostgreSQL database dump complete
--

\unrestrict u3cRGbymp0T7DfZjqvsE8WOZltoNwqdGLOLqvfs12Dd5RrMyZJRgzR7F2Ll0foj
