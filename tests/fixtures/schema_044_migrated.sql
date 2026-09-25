-- Schema of a MIGRATED database at revision 044 (the local dev database, built up
-- through the migration chain like production), dumped with pg_dump --schema-only
-- --no-owner --no-privileges. No rows. CI loads it, runs alembic upgrade head
-- and alembic check, proving migrations converge an old install onto the models.
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: account_level; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.account_level AS ENUM (
    'GROUP',
    'GENERAL',
    'SUB',
    'DETAIL'
);


--
-- Name: fee_application_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.fee_application_status AS ENUM (
    'PENDING',
    'APPLIED',
    'SKIPPED'
);


--
-- Name: fee_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.fee_type AS ENUM (
    'FLAT',
    'PERCENT',
    'HYBRID',
    'FREE'
);


--
-- Name: inventory_movement_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.inventory_movement_type AS ENUM (
    'IN',
    'OUT',
    'ADJUSTMENT'
);


--
-- Name: audit_logs_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.audit_logs_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only (% refused)', TG_OP
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    id uuid NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(512) NOT NULL,
    level public.account_level NOT NULL,
    parent_id uuid,
    detail_type character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: adjustments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adjustments (
    id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    description text,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    amount bigint DEFAULT '0'::bigint NOT NULL,
    residual bigint DEFAULT '0'::bigint NOT NULL,
    periods integer DEFAULT 1 NOT NULL,
    period_months integer DEFAULT 1 NOT NULL,
    start_date date NOT NULL,
    direction character varying(8) DEFAULT 'expense'::character varying NOT NULL,
    auto_reverse boolean DEFAULT false NOT NULL,
    periods_posted integer DEFAULT 0 NOT NULL,
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    transaction_id uuid,
    reversal_transaction_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: ai_chat_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_chat_messages (
    id uuid NOT NULL,
    session_id uuid NOT NULL,
    role character varying(16) NOT NULL,
    content jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: ai_chat_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_chat_sessions (
    id uuid NOT NULL,
    user_id character varying(64) NOT NULL,
    title character varying(256),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL,
    archived boolean DEFAULT false NOT NULL
);


--
-- Name: ai_proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_proposals (
    id uuid NOT NULL,
    confirmation_token uuid NOT NULL,
    user_id character varying(64) NOT NULL,
    session_id character varying(64),
    tool_name character varying(64) NOT NULL,
    tool_input jsonb NOT NULL,
    user_message text,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    executed_at timestamp with time zone,
    executed_audit_id uuid,
    company_id uuid NOT NULL
);


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id uuid NOT NULL,
    company_id uuid NOT NULL,
    label character varying(128) DEFAULT 'integration'::character varying NOT NULL,
    key_hash character varying(64) NOT NULL,
    prefix character varying(16) NOT NULL,
    revoked boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    last_used_at timestamp with time zone,
    scopes text DEFAULT 'time:read,time:write'::text NOT NULL,
    expires_at timestamp with time zone
);


--
-- Name: app_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_settings (
    key character varying(128) NOT NULL,
    value text NOT NULL,
    company_id uuid,
    id uuid NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    action character varying(32) NOT NULL,
    entity_type character varying(64) NOT NULL,
    entity_id character varying(64),
    user_id character varying(64),
    username character varying(128),
    detail text,
    ip_address character varying(64),
    actor_source character varying(32) DEFAULT 'manual'::character varying NOT NULL,
    session_id character varying(64),
    tool_name character varying(64),
    confirmation_token uuid,
    user_message text,
    company_id uuid,
    actor_role character varying(16)
);


--
-- Name: bank_statement_rows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bank_statement_rows (
    id uuid NOT NULL,
    statement_id uuid NOT NULL,
    row_index integer NOT NULL,
    tx_date date NOT NULL,
    description text,
    reference character varying(256),
    debit bigint NOT NULL,
    credit bigint NOT NULL,
    balance bigint,
    counterparty character varying(256),
    raw_text text,
    confidence double precision NOT NULL,
    category character varying(128),
    suggested_account_code character varying(64),
    recon_status character varying(32) NOT NULL,
    matched_transaction_id uuid,
    created_transaction_id uuid,
    user_approved boolean NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: bank_statements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bank_statements (
    id uuid NOT NULL,
    bank_name character varying(256) NOT NULL,
    account_number character varying(128),
    source_type character varying(32) NOT NULL,
    source_filename character varying(512) NOT NULL,
    currency character varying(8) NOT NULL,
    from_date date,
    to_date date,
    status character varying(32) NOT NULL,
    total_rows integer NOT NULL,
    matched_rows integer NOT NULL,
    new_rows integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    content_hash character varying(64),
    company_id uuid NOT NULL
);


--
-- Name: billing_rate_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.billing_rate_overrides (
    id uuid NOT NULL,
    employee_id uuid NOT NULL,
    client_id uuid,
    project_id uuid,
    rate numeric(14,2) DEFAULT '0'::numeric NOT NULL,
    currency character varying(8),
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: books_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.books_versions (
    scope character varying(64) NOT NULL,
    version bigint NOT NULL
);


--
-- Name: budget_limits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.budget_limits (
    id uuid NOT NULL,
    month character varying(7) NOT NULL,
    category character varying(256) NOT NULL,
    limit_amount bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: commitments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commitments (
    id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    direction character varying(8) NOT NULL,
    title character varying(256) NOT NULL,
    amount bigint NOT NULL,
    due_date date NOT NULL,
    status character varying(16) NOT NULL,
    plan_id uuid,
    sequence integer,
    plan_total integer,
    reference character varying(64),
    bank_name character varying(128),
    counterparty character varying(256),
    entity_id uuid,
    counter_account_code character varying(64),
    settled_on date,
    settled_transaction_id uuid,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: companies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.companies (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    slug character varying(128) NOT NULL,
    locale character varying(16) DEFAULT 'default'::character varying NOT NULL,
    base_currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    token_version integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    registered_capital bigint DEFAULT '0'::bigint NOT NULL,
    kind character varying(16) DEFAULT 'business'::character varying NOT NULL
);


--
-- Name: company_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_profiles (
    id uuid NOT NULL,
    company_id uuid NOT NULL,
    legal_name character varying(256),
    brand_color character varying(16) DEFAULT '#0f766e'::character varying NOT NULL,
    logo_path character varying(512),
    signature_path character varying(512),
    address text,
    tax_id character varying(128),
    registration_number character varying(128),
    email character varying(256),
    phone character varying(64),
    website character varying(256),
    bank_details text,
    default_payment_terms character varying(128),
    invoice_footer text,
    invoice_number_prefix character varying(32),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    economic_code character varying(32),
    national_id character varying(32),
    province character varying(128),
    city character varying(128),
    postal_code character varying(16),
    bank_account_no character varying(64),
    iban character varying(34)
);


--
-- Name: credit_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.credit_notes (
    id uuid NOT NULL,
    invoice_id uuid,
    entity_id uuid,
    kind character varying(16) DEFAULT 'sales'::character varying NOT NULL,
    date date NOT NULL,
    amount bigint NOT NULL,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    reason text,
    note_type character varying(16) DEFAULT 'reduction'::character varying NOT NULL,
    transaction_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: digest_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.digest_settings (
    company_id uuid NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    cash_threshold bigint DEFAULT '0'::bigint NOT NULL,
    runway_months numeric(6,2) DEFAULT '3'::numeric NOT NULL,
    channel character varying(16) DEFAULT 'all'::character varying NOT NULL
);


--
-- Name: employee_pay_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_pay_profiles (
    id uuid NOT NULL,
    entity_id uuid NOT NULL,
    pay_type character varying(16) DEFAULT 'salaried'::character varying NOT NULL,
    base_salary bigint DEFAULT '0'::bigint NOT NULL,
    hourly_rate bigint DEFAULT '0'::bigint NOT NULL,
    standard_hours numeric(8,2) DEFAULT '0'::numeric NOT NULL,
    overtime_multiplier numeric(5,2) DEFAULT 1.5 NOT NULL,
    income_tax_rate numeric(6,4) DEFAULT '0'::numeric NOT NULL,
    social_security_rate numeric(6,4) DEFAULT '0'::numeric NOT NULL,
    pension_rate numeric(6,4) DEFAULT '0'::numeric NOT NULL,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    billable_rate numeric(14,2),
    company_id uuid NOT NULL,
    monthly_standard_hours numeric(8,2),
    tax_mode character varying(16) DEFAULT 'flat'::character varying NOT NULL,
    children integer DEFAULT 0 NOT NULL,
    seniority_eligible boolean DEFAULT false NOT NULL
);


--
-- Name: entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.entities (
    id uuid NOT NULL,
    type character varying(32) NOT NULL,
    name character varying(256) NOT NULL,
    code character varying(64),
    company_id uuid NOT NULL,
    legal_name character varying(256),
    address text,
    email character varying(256),
    phone character varying(64),
    website character varying(256),
    tax_id character varying(128),
    contact_person character varying(256),
    payment_terms character varying(128),
    currency character varying(8),
    notes text,
    economic_code character varying(32),
    national_id character varying(32),
    province character varying(128),
    city character varying(128),
    postal_code character varying(16),
    bank_name character varying(128),
    account_holder character varying(256),
    account_number character varying(64),
    iban character varying(34),
    sort_code character varying(16)
);


--
-- Name: equity_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.equity_events (
    id uuid NOT NULL,
    event_type character varying(32) NOT NULL,
    date date NOT NULL,
    amount bigint NOT NULL,
    entity_id uuid,
    transaction_id uuid,
    funded_from character varying(24),
    group_ref character varying(64),
    description character varying(512),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: exchange_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_rates (
    id uuid NOT NULL,
    from_currency character varying(8) NOT NULL,
    to_currency character varying(8) NOT NULL,
    rate double precision NOT NULL,
    effective_date date NOT NULL,
    note character varying(256),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: goods_receipt_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.goods_receipt_lines (
    id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    po_line_id uuid NOT NULL,
    quantity numeric(18,4) DEFAULT '0'::numeric NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: goods_receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.goods_receipts (
    id uuid NOT NULL,
    order_id uuid NOT NULL,
    receipt_date date NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: integrity_checks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integrity_checks (
    id uuid NOT NULL,
    check_type character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    score integer NOT NULL,
    detail text,
    checked_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: inventory_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventory_items (
    id uuid NOT NULL,
    sku character varying(64),
    name character varying(256) NOT NULL,
    unit character varying(32) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    list_price bigint DEFAULT '0'::bigint NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: inventory_movements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventory_movements (
    id uuid NOT NULL,
    item_id uuid NOT NULL,
    movement_date date NOT NULL,
    movement_type public.inventory_movement_type NOT NULL,
    quantity numeric(18,4) NOT NULL,
    unit_cost bigint NOT NULL,
    reference character varying(128),
    description text,
    invoice_id uuid,
    transaction_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: invoice_emails; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_emails (
    id uuid NOT NULL,
    invoice_id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    stage integer,
    to_address character varying(512) NOT NULL,
    subject character varying(256) NOT NULL,
    status character varying(16) NOT NULL,
    error text,
    actor character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: invoice_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_items (
    id uuid NOT NULL,
    invoice_id uuid NOT NULL,
    product_name character varying(256) NOT NULL,
    quantity numeric(18,4) NOT NULL,
    unit_price bigint NOT NULL,
    unit_cost bigint,
    line_total bigint NOT NULL,
    description text,
    inventory_item_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    tax_rate numeric(7,4) DEFAULT '0'::numeric NOT NULL,
    taxable boolean DEFAULT true NOT NULL,
    tax_code character varying(64),
    tax_treatment character varying(24) DEFAULT 'standard'::character varying NOT NULL,
    company_id uuid NOT NULL,
    sstid character varying(13),
    mu character varying(8)
);


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id uuid NOT NULL,
    number character varying(128) NOT NULL,
    kind character varying(16) NOT NULL,
    status character varying(16) NOT NULL,
    issue_date date NOT NULL,
    due_date date NOT NULL,
    amount bigint NOT NULL,
    currency character varying(8) NOT NULL,
    description text,
    entity_id uuid,
    transaction_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    scheduled_payment_date date,
    company_id uuid NOT NULL,
    recurring_invoice_id uuid,
    moadian_serial bigint,
    moadian_taxid character varying(22),
    moadian_status character varying(16),
    moadian_exported_at timestamp with time zone,
    moadian_reference character varying(64),
    moadian_error text
);


--
-- Name: migration_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_batches (
    id uuid NOT NULL,
    token character varying(80) NOT NULL,
    status character varying(16) NOT NULL,
    source_files jsonb NOT NULL,
    payload jsonb NOT NULL,
    summary jsonb NOT NULL,
    result jsonb,
    opening_date date,
    opening_transaction_id uuid,
    applied_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: migration_pending_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_pending_records (
    id uuid NOT NULL,
    batch_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    entity_type character varying(32) NOT NULL,
    source_code character varying(64),
    missing_fields jsonb NOT NULL,
    review_flags jsonb NOT NULL,
    status character varying(16) NOT NULL,
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: mileage_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mileage_claims (
    id uuid NOT NULL,
    entity_id uuid,
    employee_name character varying(256) NOT NULL,
    claim_date date NOT NULL,
    distance numeric(12,2) DEFAULT '0'::numeric NOT NULL,
    unit character varying(8) DEFAULT 'mile'::character varying NOT NULL,
    rate numeric(12,4) DEFAULT '0'::numeric NOT NULL,
    amount bigint DEFAULT '0'::bigint NOT NULL,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    purpose text,
    status character varying(24) DEFAULT 'approved'::character varying NOT NULL,
    transaction_id uuid,
    reimbursement_transaction_id uuid,
    decided_by character varying(128),
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id uuid NOT NULL,
    user_id character varying(64),
    kind character varying(32) NOT NULL,
    level character varying(16) NOT NULL,
    title character varying(256) NOT NULL,
    message text NOT NULL,
    link_page character varying(64),
    dedupe_key character varying(160) NOT NULL,
    due_date date,
    read_at timestamp with time zone,
    dismissed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: pay_run_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pay_run_lines (
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    employee_name character varying(256) NOT NULL,
    hours numeric(8,2) DEFAULT '0'::numeric NOT NULL,
    overtime_hours numeric(8,2) DEFAULT '0'::numeric NOT NULL,
    proration numeric(6,4) DEFAULT '1'::numeric NOT NULL,
    gross bigint DEFAULT '0'::bigint NOT NULL,
    pre_tax_deductions bigint DEFAULT '0'::bigint NOT NULL,
    taxable_base bigint DEFAULT '0'::bigint NOT NULL,
    income_tax bigint DEFAULT '0'::bigint NOT NULL,
    social_security bigint DEFAULT '0'::bigint NOT NULL,
    net_pay bigint DEFAULT '0'::bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL,
    leave_hours numeric(8,2) DEFAULT '0'::numeric NOT NULL,
    paid_to character varying(256),
    allowances bigint DEFAULT '0'::bigint NOT NULL,
    insurable_wage bigint DEFAULT '0'::bigint NOT NULL,
    employer_social bigint DEFAULT '0'::bigint NOT NULL
);


--
-- Name: pay_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pay_runs (
    id uuid NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    pay_date date NOT NULL,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    status character varying(16) DEFAULT 'draft'::character varying NOT NULL,
    total_gross bigint DEFAULT '0'::bigint NOT NULL,
    total_tax bigint DEFAULT '0'::bigint NOT NULL,
    total_social bigint DEFAULT '0'::bigint NOT NULL,
    total_deductions bigint DEFAULT '0'::bigint NOT NULL,
    total_net bigint DEFAULT '0'::bigint NOT NULL,
    post_transaction_id uuid,
    pay_transaction_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL,
    total_employer_social bigint DEFAULT '0'::bigint NOT NULL
);


--
-- Name: payment_methods; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_methods (
    id uuid NOT NULL,
    key character varying(64) NOT NULL,
    name character varying(128) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payments (
    id uuid NOT NULL,
    invoice_id uuid NOT NULL,
    date date NOT NULL,
    amount bigint NOT NULL,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    method character varying(16) DEFAULT 'bank'::character varying NOT NULL,
    direction character varying(8) NOT NULL,
    transaction_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: payroll_rule_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payroll_rule_sets (
    id uuid NOT NULL,
    locale character varying(16) NOT NULL,
    year character varying(16) NOT NULL,
    name character varying(128) NOT NULL,
    effective_from date NOT NULL,
    effective_to date,
    params text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pending_time_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pending_time_entries (
    id uuid NOT NULL,
    company_id uuid,
    source character varying(32) NOT NULL,
    external_id character varying(128) NOT NULL,
    worker_ref character varying(256) NOT NULL,
    client_ref character varying(256),
    project_ref character varying(256),
    work_date date NOT NULL,
    hours numeric(8,2) DEFAULT '0'::numeric NOT NULL,
    description text,
    entry_type character varying(16) DEFAULT 'work'::character varying NOT NULL,
    billable boolean DEFAULT false NOT NULL,
    status character varying(24) DEFAULT 'pending'::character varying NOT NULL,
    reason character varying(256),
    resolved_entry_id uuid,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: personal_holdings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_holdings (
    id uuid NOT NULL,
    account_code character varying(64) NOT NULL,
    unit character varying(16) NOT NULL,
    quantity double precision NOT NULL,
    label character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: petty_cash_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.petty_cash_accounts (
    id uuid NOT NULL,
    user_id character varying(64) NOT NULL,
    holder_name character varying(256) NOT NULL,
    entity_id uuid,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: petty_cash_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.petty_cash_transactions (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    amount bigint NOT NULL,
    signed_amount bigint NOT NULL,
    description text NOT NULL,
    counter_account_code character varying(16),
    attachment_id uuid,
    status character varying(16) NOT NULL,
    transaction_id uuid,
    created_by character varying(64),
    decided_by character varying(64),
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id uuid NOT NULL,
    client_id uuid NOT NULL,
    name character varying(256) NOT NULL,
    code character varying(64),
    status character varying(16) DEFAULT 'active'::character varying NOT NULL,
    default_currency character varying(8),
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: purchase_order_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_order_lines (
    id uuid NOT NULL,
    order_id uuid NOT NULL,
    inventory_item_id uuid,
    description character varying(256) NOT NULL,
    ordered_qty numeric(18,4) DEFAULT '0'::numeric NOT NULL,
    received_qty numeric(18,4) DEFAULT '0'::numeric NOT NULL,
    unit_price bigint DEFAULT '0'::bigint NOT NULL,
    line_total bigint DEFAULT '0'::bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: purchase_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_orders (
    id uuid NOT NULL,
    number character varying(128) NOT NULL,
    entity_id uuid,
    order_date date NOT NULL,
    expected_date date,
    status character varying(24) DEFAULT 'draft'::character varying NOT NULL,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    description text,
    matched_invoice_id uuid,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: quote_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quote_items (
    id uuid NOT NULL,
    quote_id uuid NOT NULL,
    "position" integer NOT NULL,
    product_name character varying(256) NOT NULL,
    quantity numeric(18,4) NOT NULL,
    unit_price bigint NOT NULL,
    unit_cost bigint,
    line_total bigint NOT NULL,
    tax_rate numeric(7,4) DEFAULT '0'::numeric NOT NULL,
    taxable boolean DEFAULT true NOT NULL,
    tax_code character varying(64),
    tax_treatment character varying(24) DEFAULT 'standard'::character varying NOT NULL,
    description text,
    inventory_item_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid,
    sstid character varying(13),
    mu character varying(8)
);


--
-- Name: quotes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quotes (
    id uuid NOT NULL,
    number character varying(128) NOT NULL,
    status character varying(16) NOT NULL,
    issue_date date NOT NULL,
    valid_until date NOT NULL,
    amount bigint NOT NULL,
    currency character varying(8) NOT NULL,
    description text,
    entity_id uuid,
    converted_invoice_id uuid,
    sent_at timestamp with time zone,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: rate_limit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rate_limit_events (
    id uuid NOT NULL,
    bucket character varying(32) NOT NULL,
    identity character varying(256) NOT NULL,
    at timestamp without time zone NOT NULL
);


--
-- Name: recurring_invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recurring_invoices (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    entity_id uuid NOT NULL,
    currency character varying(8) NOT NULL,
    description text,
    amount bigint NOT NULL,
    items text NOT NULL,
    frequency character varying(16) NOT NULL,
    calendar character varying(16) NOT NULL,
    start_date date NOT NULL,
    end_date date,
    max_occurrences integer,
    next_run_date date NOT NULL,
    occurrences integer NOT NULL,
    terms_days integer NOT NULL,
    issue_status character varying(16) NOT NULL,
    auto_send boolean NOT NULL,
    status character varying(16) NOT NULL,
    last_invoice_id uuid,
    last_run_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: recurring_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recurring_rules (
    id uuid NOT NULL,
    name character varying(256) NOT NULL,
    direction character varying(16) NOT NULL,
    frequency character varying(16) NOT NULL,
    amount bigint,
    start_date date NOT NULL,
    next_run_date date NOT NULL,
    entity_id uuid,
    bank_name character varying(128),
    reference_prefix character varying(128),
    note text,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL,
    end_date date,
    bank_account_code character varying(16),
    counter_account_code character varying(16),
    auto_post boolean DEFAULT true NOT NULL,
    last_run_date date
);


--
-- Name: reminders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reminders (
    id uuid NOT NULL,
    user_id character varying(64) NOT NULL,
    title character varying(256) NOT NULL,
    note text,
    due_date date NOT NULL,
    repeat character varying(16) NOT NULL,
    days_before integer NOT NULL,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: shareholdings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shareholdings (
    id uuid NOT NULL,
    entity_id uuid NOT NULL,
    shares bigint,
    percent numeric(7,4),
    par_value bigint,
    since date,
    share_class character varying(16) DEFAULT 'ordinary'::character varying NOT NULL,
    notes character varying(512),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid
);


--
-- Name: tax_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tax_rates (
    id uuid NOT NULL,
    code character varying(64) NOT NULL,
    jurisdiction character varying(32) NOT NULL,
    description character varying(128),
    rate numeric(7,4) DEFAULT '0'::numeric NOT NULL,
    effective_from date NOT NULL,
    effective_to date,
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL
);


--
-- Name: time_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.time_entries (
    id uuid NOT NULL,
    employee_id uuid NOT NULL,
    client_id uuid,
    project_id uuid,
    work_date date NOT NULL,
    hours numeric(8,2) DEFAULT '0'::numeric NOT NULL,
    description text,
    billable boolean DEFAULT true NOT NULL,
    status character varying(16) DEFAULT 'unbilled'::character varying NOT NULL,
    invoice_id uuid,
    rate_snapshot numeric(14,2),
    currency character varying(8),
    created_by character varying(128),
    created_at timestamp with time zone DEFAULT now(),
    company_id uuid NOT NULL,
    entry_type character varying(16) DEFAULT 'work'::character varying NOT NULL,
    payable boolean DEFAULT true NOT NULL,
    payroll_status character varying(16) DEFAULT 'unpaid'::character varying NOT NULL,
    source character varying(32),
    external_id character varying(128),
    payroll_run_id uuid
);


--
-- Name: transaction_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transaction_attachments (
    id uuid NOT NULL,
    transaction_id uuid,
    file_name character varying(256) NOT NULL,
    file_path character varying(512) NOT NULL,
    content_type character varying(128) NOT NULL,
    size_bytes bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: transaction_entities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transaction_entities (
    id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    role character varying(32) NOT NULL,
    company_id uuid NOT NULL,
    amount bigint
);


--
-- Name: transaction_fee_applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transaction_fee_applications (
    id uuid NOT NULL,
    transaction_id uuid,
    method_id uuid,
    bank_id uuid,
    fee_rule_id uuid,
    status public.fee_application_status NOT NULL,
    direction character varying(16),
    amount_mode character varying(16),
    base_amount bigint,
    fee_amount bigint,
    gross_amount bigint,
    net_amount bigint,
    note character varying(256),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: transaction_fees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transaction_fees (
    id uuid NOT NULL,
    method_id uuid NOT NULL,
    bank_id uuid NOT NULL,
    fee_type public.fee_type NOT NULL,
    fee_value bigint NOT NULL,
    flat_fee bigint NOT NULL,
    percent_bps integer NOT NULL,
    max_fee bigint,
    effective_from date NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: transaction_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transaction_lines (
    id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    account_id uuid NOT NULL,
    debit bigint NOT NULL,
    credit bigint NOT NULL,
    line_description character varying(512),
    company_id uuid NOT NULL
);


--
-- Name: transaction_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transaction_versions (
    id uuid NOT NULL,
    transaction_id character varying(64) NOT NULL,
    version integer NOT NULL,
    snapshot text NOT NULL,
    action character varying(32) NOT NULL,
    user_id character varying(64),
    username character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transactions (
    id uuid NOT NULL,
    date date NOT NULL,
    reference character varying(128),
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    currency character varying(8) DEFAULT 'IRR'::character varying NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: trial_balance_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trial_balance_lines (
    id uuid NOT NULL,
    trial_balance_id uuid NOT NULL,
    account_id uuid NOT NULL,
    account_code character varying(64) NOT NULL,
    account_name character varying(512) NOT NULL,
    detail_type character varying(128),
    debit_turnover bigint NOT NULL,
    credit_turnover bigint NOT NULL,
    debit_balance bigint NOT NULL,
    credit_balance bigint NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: trial_balances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trial_balances (
    id uuid NOT NULL,
    name character varying(256),
    source_filename character varying(512),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    company_id uuid NOT NULL
);


--
-- Name: upload_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upload_tokens (
    id uuid NOT NULL,
    kind character varying(32) NOT NULL,
    token character varying(300) NOT NULL,
    file_path character varying(1024) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    company_id uuid
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    username character varying(64) NOT NULL,
    password_hash character varying(256) NOT NULL,
    password_salt character varying(128) NOT NULL,
    is_admin boolean NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    preferred_language character varying(8) DEFAULT 'en'::character varying,
    company_id uuid,
    is_superadmin boolean DEFAULT false NOT NULL,
    token_version integer DEFAULT 0 NOT NULL,
    role character varying(16) DEFAULT 'owner'::character varying NOT NULL,
    entity_id uuid,
    email character varying(254),
    email_verified_at timestamp with time zone,
    verification_token character varying(64),
    verification_sent_at timestamp with time zone,
    last_seen_release character varying(32),
    totp_secret text,
    totp_pending_secret text,
    totp_enabled_at timestamp with time zone,
    totp_last_step bigint,
    totp_recovery text
);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);


--
-- Name: adjustments adjustments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adjustments
    ADD CONSTRAINT adjustments_pkey PRIMARY KEY (id);


--
-- Name: ai_chat_messages ai_chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_chat_messages
    ADD CONSTRAINT ai_chat_messages_pkey PRIMARY KEY (id);


--
-- Name: ai_chat_sessions ai_chat_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_chat_sessions
    ADD CONSTRAINT ai_chat_sessions_pkey PRIMARY KEY (id);


--
-- Name: ai_proposals ai_proposals_confirmation_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_proposals
    ADD CONSTRAINT ai_proposals_confirmation_token_key UNIQUE (confirmation_token);


--
-- Name: ai_proposals ai_proposals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_proposals
    ADD CONSTRAINT ai_proposals_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: app_settings app_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: bank_statement_rows bank_statement_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_statement_rows
    ADD CONSTRAINT bank_statement_rows_pkey PRIMARY KEY (id);


--
-- Name: bank_statements bank_statements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_statements
    ADD CONSTRAINT bank_statements_pkey PRIMARY KEY (id);


--
-- Name: billing_rate_overrides billing_rate_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_rate_overrides
    ADD CONSTRAINT billing_rate_overrides_pkey PRIMARY KEY (id);


--
-- Name: books_versions books_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.books_versions
    ADD CONSTRAINT books_versions_pkey PRIMARY KEY (scope);


--
-- Name: budget_limits budget_limits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.budget_limits
    ADD CONSTRAINT budget_limits_pkey PRIMARY KEY (id);


--
-- Name: commitments commitments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_pkey PRIMARY KEY (id);


--
-- Name: companies companies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT companies_pkey PRIMARY KEY (id);


--
-- Name: companies companies_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT companies_slug_key UNIQUE (slug);


--
-- Name: company_profiles company_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_profiles
    ADD CONSTRAINT company_profiles_pkey PRIMARY KEY (id);


--
-- Name: credit_notes credit_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_notes
    ADD CONSTRAINT credit_notes_pkey PRIMARY KEY (id);


--
-- Name: digest_settings digest_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.digest_settings
    ADD CONSTRAINT digest_settings_pkey PRIMARY KEY (company_id);


--
-- Name: employee_pay_profiles employee_pay_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_pay_profiles
    ADD CONSTRAINT employee_pay_profiles_pkey PRIMARY KEY (id);


--
-- Name: entities entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities
    ADD CONSTRAINT entities_pkey PRIMARY KEY (id);


--
-- Name: equity_events equity_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equity_events
    ADD CONSTRAINT equity_events_pkey PRIMARY KEY (id);


--
-- Name: exchange_rates exchange_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_rates
    ADD CONSTRAINT exchange_rates_pkey PRIMARY KEY (id);


--
-- Name: goods_receipt_lines goods_receipt_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.goods_receipt_lines
    ADD CONSTRAINT goods_receipt_lines_pkey PRIMARY KEY (id);


--
-- Name: goods_receipts goods_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.goods_receipts
    ADD CONSTRAINT goods_receipts_pkey PRIMARY KEY (id);


--
-- Name: integrity_checks integrity_checks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integrity_checks
    ADD CONSTRAINT integrity_checks_pkey PRIMARY KEY (id);


--
-- Name: inventory_items inventory_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_items
    ADD CONSTRAINT inventory_items_pkey PRIMARY KEY (id);


--
-- Name: inventory_movements inventory_movements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_movements
    ADD CONSTRAINT inventory_movements_pkey PRIMARY KEY (id);


--
-- Name: invoice_emails invoice_emails_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_emails
    ADD CONSTRAINT invoice_emails_pkey PRIMARY KEY (id);


--
-- Name: invoice_items invoice_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: migration_batches migration_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_batches
    ADD CONSTRAINT migration_batches_pkey PRIMARY KEY (id);


--
-- Name: migration_pending_records migration_pending_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_pending_records
    ADD CONSTRAINT migration_pending_records_pkey PRIMARY KEY (id);


--
-- Name: mileage_claims mileage_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mileage_claims
    ADD CONSTRAINT mileage_claims_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: pay_run_lines pay_run_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_run_lines
    ADD CONSTRAINT pay_run_lines_pkey PRIMARY KEY (id);


--
-- Name: pay_runs pay_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_runs
    ADD CONSTRAINT pay_runs_pkey PRIMARY KEY (id);


--
-- Name: payment_methods payment_methods_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_methods
    ADD CONSTRAINT payment_methods_pkey PRIMARY KEY (id);


--
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


--
-- Name: payroll_rule_sets payroll_rule_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_rule_sets
    ADD CONSTRAINT payroll_rule_sets_pkey PRIMARY KEY (id);


--
-- Name: pending_time_entries pending_time_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pending_time_entries
    ADD CONSTRAINT pending_time_entries_pkey PRIMARY KEY (id);


--
-- Name: personal_holdings personal_holdings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_holdings
    ADD CONSTRAINT personal_holdings_pkey PRIMARY KEY (id);


--
-- Name: petty_cash_accounts petty_cash_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.petty_cash_accounts
    ADD CONSTRAINT petty_cash_accounts_pkey PRIMARY KEY (id);


--
-- Name: petty_cash_transactions petty_cash_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.petty_cash_transactions
    ADD CONSTRAINT petty_cash_transactions_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: purchase_order_lines purchase_order_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_lines
    ADD CONSTRAINT purchase_order_lines_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: quote_items quote_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quote_items
    ADD CONSTRAINT quote_items_pkey PRIMARY KEY (id);


--
-- Name: quotes quotes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT quotes_pkey PRIMARY KEY (id);


--
-- Name: rate_limit_events rate_limit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rate_limit_events
    ADD CONSTRAINT rate_limit_events_pkey PRIMARY KEY (id);


--
-- Name: recurring_invoices recurring_invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_invoices
    ADD CONSTRAINT recurring_invoices_pkey PRIMARY KEY (id);


--
-- Name: recurring_rules recurring_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_rules
    ADD CONSTRAINT recurring_rules_pkey PRIMARY KEY (id);


--
-- Name: reminders reminders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reminders
    ADD CONSTRAINT reminders_pkey PRIMARY KEY (id);


--
-- Name: shareholdings shareholdings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shareholdings
    ADD CONSTRAINT shareholdings_pkey PRIMARY KEY (id);


--
-- Name: tax_rates tax_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tax_rates
    ADD CONSTRAINT tax_rates_pkey PRIMARY KEY (id);


--
-- Name: time_entries time_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.time_entries
    ADD CONSTRAINT time_entries_pkey PRIMARY KEY (id);


--
-- Name: transaction_attachments transaction_attachments_file_path_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_attachments
    ADD CONSTRAINT transaction_attachments_file_path_key UNIQUE (file_path);


--
-- Name: transaction_attachments transaction_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_attachments
    ADD CONSTRAINT transaction_attachments_pkey PRIMARY KEY (id);


--
-- Name: transaction_entities transaction_entities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_entities
    ADD CONSTRAINT transaction_entities_pkey PRIMARY KEY (id);


--
-- Name: transaction_fee_applications transaction_fee_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fee_applications
    ADD CONSTRAINT transaction_fee_applications_pkey PRIMARY KEY (id);


--
-- Name: transaction_fees transaction_fees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fees
    ADD CONSTRAINT transaction_fees_pkey PRIMARY KEY (id);


--
-- Name: transaction_lines transaction_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_lines
    ADD CONSTRAINT transaction_lines_pkey PRIMARY KEY (id);


--
-- Name: transaction_versions transaction_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_versions
    ADD CONSTRAINT transaction_versions_pkey PRIMARY KEY (id);


--
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- Name: trial_balance_lines trial_balance_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trial_balance_lines
    ADD CONSTRAINT trial_balance_lines_pkey PRIMARY KEY (id);


--
-- Name: trial_balances trial_balances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trial_balances
    ADD CONSTRAINT trial_balances_pkey PRIMARY KEY (id);


--
-- Name: upload_tokens upload_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upload_tokens
    ADD CONSTRAINT upload_tokens_pkey PRIMARY KEY (id);


--
-- Name: accounts uq_account_company_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT uq_account_company_code UNIQUE (company_id, code);


--
-- Name: company_profiles uq_company_profile_company; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_profiles
    ADD CONSTRAINT uq_company_profile_company UNIQUE (company_id);


--
-- Name: exchange_rates uq_exchange_rates_from_to_date; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_rates
    ADD CONSTRAINT uq_exchange_rates_from_to_date UNIQUE (from_currency, to_currency, effective_date);


--
-- Name: migration_batches uq_migration_batch_token; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_batches
    ADD CONSTRAINT uq_migration_batch_token UNIQUE (company_id, token);


--
-- Name: notifications uq_notifications_dedupe; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT uq_notifications_dedupe UNIQUE (company_id, dedupe_key);


--
-- Name: payroll_rule_sets uq_payroll_rule_sets_locale_year; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_rule_sets
    ADD CONSTRAINT uq_payroll_rule_sets_locale_year UNIQUE (locale, year);


--
-- Name: personal_holdings uq_personal_holding_acct_unit; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_holdings
    ADD CONSTRAINT uq_personal_holding_acct_unit UNIQUE (company_id, account_code, unit);


--
-- Name: quotes uq_quotes_company_number; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT uq_quotes_company_number UNIQUE (company_id, number);


--
-- Name: transaction_fees uq_transaction_fee_method_bank_effective; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fees
    ADD CONSTRAINT uq_transaction_fee_method_bank_effective UNIQUE (method_id, bank_id, effective_from);


--
-- Name: upload_tokens uq_upload_tokens_company_kind_token; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upload_tokens
    ADD CONSTRAINT uq_upload_tokens_company_kind_token UNIQUE (company_id, kind, token);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: ix_accounts_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_accounts_code ON public.accounts USING btree (code);


--
-- Name: ix_accounts_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_accounts_company_id ON public.accounts USING btree (company_id);


--
-- Name: ix_adjustments_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adjustments_company_id ON public.adjustments USING btree (company_id);


--
-- Name: ix_adjustments_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adjustments_kind ON public.adjustments USING btree (kind);


--
-- Name: ix_adjustments_start_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adjustments_start_date ON public.adjustments USING btree (start_date);


--
-- Name: ix_adjustments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adjustments_status ON public.adjustments USING btree (status);


--
-- Name: ix_adjustments_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adjustments_transaction_id ON public.adjustments USING btree (transaction_id);


--
-- Name: ix_ai_chat_messages_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_chat_messages_company_id ON public.ai_chat_messages USING btree (company_id);


--
-- Name: ix_ai_chat_messages_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_chat_messages_session ON public.ai_chat_messages USING btree (session_id, created_at);


--
-- Name: ix_ai_chat_sessions_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_chat_sessions_company_id ON public.ai_chat_sessions USING btree (company_id);


--
-- Name: ix_ai_chat_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_chat_sessions_user ON public.ai_chat_sessions USING btree (user_id);


--
-- Name: ix_ai_chat_sessions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_chat_sessions_user_id ON public.ai_chat_sessions USING btree (user_id);


--
-- Name: ix_ai_proposals_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_proposals_company_id ON public.ai_proposals USING btree (company_id);


--
-- Name: ix_ai_proposals_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_ai_proposals_token ON public.ai_proposals USING btree (confirmation_token);


--
-- Name: ix_ai_proposals_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_proposals_user_status ON public.ai_proposals USING btree (user_id, status);


--
-- Name: ix_api_keys_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_api_keys_company_id ON public.api_keys USING btree (company_id);


--
-- Name: ix_api_keys_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_api_keys_expires_at ON public.api_keys USING btree (expires_at);


--
-- Name: ix_api_keys_key_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_api_keys_key_hash ON public.api_keys USING btree (key_hash);


--
-- Name: ix_api_keys_revoked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_api_keys_revoked ON public.api_keys USING btree (revoked);


--
-- Name: ix_app_settings_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_app_settings_company_id ON public.app_settings USING btree (company_id);


--
-- Name: ix_app_settings_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_app_settings_key ON public.app_settings USING btree (key);


--
-- Name: ix_audit_logs_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_action ON public.audit_logs USING btree (action);


--
-- Name: ix_audit_logs_actor_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_actor_source ON public.audit_logs USING btree (actor_source);


--
-- Name: ix_audit_logs_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_company_id ON public.audit_logs USING btree (company_id);


--
-- Name: ix_audit_logs_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_entity_id ON public.audit_logs USING btree (entity_id);


--
-- Name: ix_audit_logs_entity_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_entity_type ON public.audit_logs USING btree (entity_type);


--
-- Name: ix_audit_logs_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_session_id ON public.audit_logs USING btree (session_id);


--
-- Name: ix_audit_logs_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_timestamp ON public.audit_logs USING btree ("timestamp");


--
-- Name: ix_audit_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_user_id ON public.audit_logs USING btree (user_id);


--
-- Name: ix_bank_statement_rows_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statement_rows_company_id ON public.bank_statement_rows USING btree (company_id);


--
-- Name: ix_bank_statement_rows_recon_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statement_rows_recon_status ON public.bank_statement_rows USING btree (recon_status);


--
-- Name: ix_bank_statement_rows_row_index; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statement_rows_row_index ON public.bank_statement_rows USING btree (row_index);


--
-- Name: ix_bank_statement_rows_statement_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statement_rows_statement_id ON public.bank_statement_rows USING btree (statement_id);


--
-- Name: ix_bank_statement_rows_tx_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statement_rows_tx_date ON public.bank_statement_rows USING btree (tx_date);


--
-- Name: ix_bank_statements_bank_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statements_bank_name ON public.bank_statements USING btree (bank_name);


--
-- Name: ix_bank_statements_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statements_company_id ON public.bank_statements USING btree (company_id);


--
-- Name: ix_bank_statements_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statements_content_hash ON public.bank_statements USING btree (content_hash);


--
-- Name: ix_bank_statements_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statements_source_type ON public.bank_statements USING btree (source_type);


--
-- Name: ix_bank_statements_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bank_statements_status ON public.bank_statements USING btree (status);


--
-- Name: ix_billing_rate_overrides_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_billing_rate_overrides_client_id ON public.billing_rate_overrides USING btree (client_id);


--
-- Name: ix_billing_rate_overrides_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_billing_rate_overrides_company_id ON public.billing_rate_overrides USING btree (company_id);


--
-- Name: ix_billing_rate_overrides_employee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_billing_rate_overrides_employee_id ON public.billing_rate_overrides USING btree (employee_id);


--
-- Name: ix_billing_rate_overrides_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_billing_rate_overrides_project_id ON public.billing_rate_overrides USING btree (project_id);


--
-- Name: ix_budget_limits_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_budget_limits_category ON public.budget_limits USING btree (category);


--
-- Name: ix_budget_limits_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_budget_limits_company_id ON public.budget_limits USING btree (company_id);


--
-- Name: ix_budget_limits_month; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_budget_limits_month ON public.budget_limits USING btree (month);


--
-- Name: ix_commitments_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_company_id ON public.commitments USING btree (company_id);


--
-- Name: ix_commitments_direction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_direction ON public.commitments USING btree (direction);


--
-- Name: ix_commitments_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_due_date ON public.commitments USING btree (due_date);


--
-- Name: ix_commitments_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_entity_id ON public.commitments USING btree (entity_id);


--
-- Name: ix_commitments_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_kind ON public.commitments USING btree (kind);


--
-- Name: ix_commitments_plan_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_plan_id ON public.commitments USING btree (plan_id);


--
-- Name: ix_commitments_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_reference ON public.commitments USING btree (reference);


--
-- Name: ix_commitments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commitments_status ON public.commitments USING btree (status);


--
-- Name: ix_companies_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_companies_slug ON public.companies USING btree (slug);


--
-- Name: ix_companies_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_companies_status ON public.companies USING btree (status);


--
-- Name: ix_company_profiles_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_company_profiles_company_id ON public.company_profiles USING btree (company_id);


--
-- Name: ix_credit_notes_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_notes_company_id ON public.credit_notes USING btree (company_id);


--
-- Name: ix_credit_notes_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_notes_date ON public.credit_notes USING btree (date);


--
-- Name: ix_credit_notes_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_notes_entity_id ON public.credit_notes USING btree (entity_id);


--
-- Name: ix_credit_notes_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_notes_invoice_id ON public.credit_notes USING btree (invoice_id);


--
-- Name: ix_credit_notes_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_notes_kind ON public.credit_notes USING btree (kind);


--
-- Name: ix_credit_notes_note_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_notes_note_type ON public.credit_notes USING btree (note_type);


--
-- Name: ix_credit_notes_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_credit_notes_transaction_id ON public.credit_notes USING btree (transaction_id);


--
-- Name: ix_employee_pay_profiles_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_employee_pay_profiles_active ON public.employee_pay_profiles USING btree (active);


--
-- Name: ix_employee_pay_profiles_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_employee_pay_profiles_company_id ON public.employee_pay_profiles USING btree (company_id);


--
-- Name: ix_employee_pay_profiles_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_employee_pay_profiles_entity_id ON public.employee_pay_profiles USING btree (entity_id);


--
-- Name: ix_employee_pay_profiles_pay_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_employee_pay_profiles_pay_type ON public.employee_pay_profiles USING btree (pay_type);


--
-- Name: ix_entities_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entities_code ON public.entities USING btree (code);


--
-- Name: ix_entities_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entities_company_id ON public.entities USING btree (company_id);


--
-- Name: ix_entities_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entities_name ON public.entities USING btree (name);


--
-- Name: ix_entities_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_entities_type ON public.entities USING btree (type);


--
-- Name: ix_equity_events_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equity_events_company_id ON public.equity_events USING btree (company_id);


--
-- Name: ix_equity_events_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equity_events_date ON public.equity_events USING btree (date);


--
-- Name: ix_equity_events_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equity_events_entity_id ON public.equity_events USING btree (entity_id);


--
-- Name: ix_equity_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equity_events_event_type ON public.equity_events USING btree (event_type);


--
-- Name: ix_equity_events_group_ref; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equity_events_group_ref ON public.equity_events USING btree (group_ref);


--
-- Name: ix_equity_events_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_equity_events_transaction_id ON public.equity_events USING btree (transaction_id);


--
-- Name: ix_exchange_rates_effective_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_exchange_rates_effective_date ON public.exchange_rates USING btree (effective_date);


--
-- Name: ix_exchange_rates_from_currency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_exchange_rates_from_currency ON public.exchange_rates USING btree (from_currency);


--
-- Name: ix_exchange_rates_to_currency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_exchange_rates_to_currency ON public.exchange_rates USING btree (to_currency);


--
-- Name: ix_goods_receipt_lines_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_goods_receipt_lines_company_id ON public.goods_receipt_lines USING btree (company_id);


--
-- Name: ix_goods_receipt_lines_po_line_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_goods_receipt_lines_po_line_id ON public.goods_receipt_lines USING btree (po_line_id);


--
-- Name: ix_goods_receipt_lines_receipt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_goods_receipt_lines_receipt_id ON public.goods_receipt_lines USING btree (receipt_id);


--
-- Name: ix_goods_receipts_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_goods_receipts_company_id ON public.goods_receipts USING btree (company_id);


--
-- Name: ix_goods_receipts_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_goods_receipts_order_id ON public.goods_receipts USING btree (order_id);


--
-- Name: ix_integrity_checks_check_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integrity_checks_check_type ON public.integrity_checks USING btree (check_type);


--
-- Name: ix_integrity_checks_checked_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integrity_checks_checked_at ON public.integrity_checks USING btree (checked_at);


--
-- Name: ix_integrity_checks_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integrity_checks_company_id ON public.integrity_checks USING btree (company_id);


--
-- Name: ix_integrity_checks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integrity_checks_status ON public.integrity_checks USING btree (status);


--
-- Name: ix_inventory_items_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_items_company_id ON public.inventory_items USING btree (company_id);


--
-- Name: ix_inventory_items_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_items_name ON public.inventory_items USING btree (name);


--
-- Name: ix_inventory_items_sku; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_items_sku ON public.inventory_items USING btree (sku);


--
-- Name: ix_inventory_movements_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_movements_company_id ON public.inventory_movements USING btree (company_id);


--
-- Name: ix_inventory_movements_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_movements_invoice_id ON public.inventory_movements USING btree (invoice_id);


--
-- Name: ix_inventory_movements_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_movements_item_id ON public.inventory_movements USING btree (item_id);


--
-- Name: ix_inventory_movements_movement_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_movements_movement_date ON public.inventory_movements USING btree (movement_date);


--
-- Name: ix_inventory_movements_movement_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_movements_movement_type ON public.inventory_movements USING btree (movement_type);


--
-- Name: ix_inventory_movements_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_movements_reference ON public.inventory_movements USING btree (reference);


--
-- Name: ix_inventory_movements_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_inventory_movements_transaction_id ON public.inventory_movements USING btree (transaction_id);


--
-- Name: ix_invoice_emails_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_emails_company_id ON public.invoice_emails USING btree (company_id);


--
-- Name: ix_invoice_emails_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_emails_invoice_id ON public.invoice_emails USING btree (invoice_id);


--
-- Name: ix_invoice_emails_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_emails_kind ON public.invoice_emails USING btree (kind);


--
-- Name: ix_invoice_emails_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_emails_status ON public.invoice_emails USING btree (status);


--
-- Name: ix_invoice_items_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_items_company_id ON public.invoice_items USING btree (company_id);


--
-- Name: ix_invoice_items_inventory_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_items_inventory_item_id ON public.invoice_items USING btree (inventory_item_id);


--
-- Name: ix_invoice_items_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_items_invoice_id ON public.invoice_items USING btree (invoice_id);


--
-- Name: ix_invoice_items_product_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_items_product_name ON public.invoice_items USING btree (product_name);


--
-- Name: ix_invoices_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_company_id ON public.invoices USING btree (company_id);


--
-- Name: ix_invoices_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_due_date ON public.invoices USING btree (due_date);


--
-- Name: ix_invoices_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_entity_id ON public.invoices USING btree (entity_id);


--
-- Name: ix_invoices_issue_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_issue_date ON public.invoices USING btree (issue_date);


--
-- Name: ix_invoices_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_kind ON public.invoices USING btree (kind);


--
-- Name: ix_invoices_moadian_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_moadian_status ON public.invoices USING btree (moadian_status);


--
-- Name: ix_invoices_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_number ON public.invoices USING btree (number);


--
-- Name: ix_invoices_recurring_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_recurring_invoice_id ON public.invoices USING btree (recurring_invoice_id);


--
-- Name: ix_invoices_scheduled_payment_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_scheduled_payment_date ON public.invoices USING btree (scheduled_payment_date);


--
-- Name: ix_invoices_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_status ON public.invoices USING btree (status);


--
-- Name: ix_invoices_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoices_transaction_id ON public.invoices USING btree (transaction_id);


--
-- Name: ix_migration_batches_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_migration_batches_company_id ON public.migration_batches USING btree (company_id);


--
-- Name: ix_migration_batches_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_migration_batches_token ON public.migration_batches USING btree (token);


--
-- Name: ix_migration_pending_records_batch_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_migration_pending_records_batch_id ON public.migration_pending_records USING btree (batch_id);


--
-- Name: ix_migration_pending_records_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_migration_pending_records_company_id ON public.migration_pending_records USING btree (company_id);


--
-- Name: ix_migration_pending_records_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_migration_pending_records_entity_id ON public.migration_pending_records USING btree (entity_id);


--
-- Name: ix_migration_pending_records_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_migration_pending_records_status ON public.migration_pending_records USING btree (status);


--
-- Name: ix_mileage_claims_claim_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mileage_claims_claim_date ON public.mileage_claims USING btree (claim_date);


--
-- Name: ix_mileage_claims_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mileage_claims_company_id ON public.mileage_claims USING btree (company_id);


--
-- Name: ix_mileage_claims_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mileage_claims_entity_id ON public.mileage_claims USING btree (entity_id);


--
-- Name: ix_mileage_claims_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mileage_claims_status ON public.mileage_claims USING btree (status);


--
-- Name: ix_notifications_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_company_id ON public.notifications USING btree (company_id);


--
-- Name: ix_notifications_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_kind ON public.notifications USING btree (kind);


--
-- Name: ix_notifications_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_user_id ON public.notifications USING btree (user_id);


--
-- Name: ix_pay_run_lines_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pay_run_lines_company_id ON public.pay_run_lines USING btree (company_id);


--
-- Name: ix_pay_run_lines_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pay_run_lines_entity_id ON public.pay_run_lines USING btree (entity_id);


--
-- Name: ix_pay_run_lines_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pay_run_lines_run_id ON public.pay_run_lines USING btree (run_id);


--
-- Name: ix_pay_runs_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pay_runs_company_id ON public.pay_runs USING btree (company_id);


--
-- Name: ix_pay_runs_period_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pay_runs_period_start ON public.pay_runs USING btree (period_start);


--
-- Name: ix_pay_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pay_runs_status ON public.pay_runs USING btree (status);


--
-- Name: ix_payment_methods_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_payment_methods_key ON public.payment_methods USING btree (key);


--
-- Name: ix_payment_methods_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_payment_methods_name ON public.payment_methods USING btree (name);


--
-- Name: ix_payments_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_company_id ON public.payments USING btree (company_id);


--
-- Name: ix_payments_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_date ON public.payments USING btree (date);


--
-- Name: ix_payments_direction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_direction ON public.payments USING btree (direction);


--
-- Name: ix_payments_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_invoice_id ON public.payments USING btree (invoice_id);


--
-- Name: ix_payments_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_transaction_id ON public.payments USING btree (transaction_id);


--
-- Name: ix_payroll_rule_sets_effective_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payroll_rule_sets_effective_from ON public.payroll_rule_sets USING btree (effective_from);


--
-- Name: ix_payroll_rule_sets_locale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payroll_rule_sets_locale ON public.payroll_rule_sets USING btree (locale);


--
-- Name: ix_pending_time_company; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pending_time_company ON public.pending_time_entries USING btree (company_id);


--
-- Name: ix_pending_time_source_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pending_time_source_external ON public.pending_time_entries USING btree (company_id, source, external_id);


--
-- Name: ix_pending_time_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pending_time_status ON public.pending_time_entries USING btree (status);


--
-- Name: ix_personal_holdings_account_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_holdings_account_code ON public.personal_holdings USING btree (account_code);


--
-- Name: ix_personal_holdings_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_holdings_company_id ON public.personal_holdings USING btree (company_id);


--
-- Name: ix_personal_holdings_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_personal_holdings_unit ON public.personal_holdings USING btree (unit);


--
-- Name: ix_petty_cash_accounts_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_petty_cash_accounts_company_id ON public.petty_cash_accounts USING btree (company_id);


--
-- Name: ix_petty_cash_accounts_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_petty_cash_accounts_user_id ON public.petty_cash_accounts USING btree (user_id);


--
-- Name: ix_petty_cash_transactions_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_petty_cash_transactions_account_id ON public.petty_cash_transactions USING btree (account_id);


--
-- Name: ix_petty_cash_transactions_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_petty_cash_transactions_company_id ON public.petty_cash_transactions USING btree (company_id);


--
-- Name: ix_petty_cash_transactions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_petty_cash_transactions_status ON public.petty_cash_transactions USING btree (status);


--
-- Name: ix_projects_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_projects_client_id ON public.projects USING btree (client_id);


--
-- Name: ix_projects_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_projects_company_id ON public.projects USING btree (company_id);


--
-- Name: ix_projects_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_projects_name ON public.projects USING btree (name);


--
-- Name: ix_projects_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_projects_status ON public.projects USING btree (status);


--
-- Name: ix_purchase_order_lines_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_order_lines_company_id ON public.purchase_order_lines USING btree (company_id);


--
-- Name: ix_purchase_order_lines_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_order_lines_order_id ON public.purchase_order_lines USING btree (order_id);


--
-- Name: ix_purchase_orders_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_orders_company_id ON public.purchase_orders USING btree (company_id);


--
-- Name: ix_purchase_orders_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_orders_entity_id ON public.purchase_orders USING btree (entity_id);


--
-- Name: ix_purchase_orders_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_orders_number ON public.purchase_orders USING btree (number);


--
-- Name: ix_purchase_orders_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_purchase_orders_status ON public.purchase_orders USING btree (status);


--
-- Name: ix_quote_items_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quote_items_company_id ON public.quote_items USING btree (company_id);


--
-- Name: ix_quote_items_quote_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quote_items_quote_id ON public.quote_items USING btree (quote_id);


--
-- Name: ix_quotes_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quotes_company_id ON public.quotes USING btree (company_id);


--
-- Name: ix_quotes_converted_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quotes_converted_invoice_id ON public.quotes USING btree (converted_invoice_id);


--
-- Name: ix_quotes_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quotes_entity_id ON public.quotes USING btree (entity_id);


--
-- Name: ix_quotes_issue_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quotes_issue_date ON public.quotes USING btree (issue_date);


--
-- Name: ix_quotes_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quotes_number ON public.quotes USING btree (number);


--
-- Name: ix_quotes_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quotes_status ON public.quotes USING btree (status);


--
-- Name: ix_quotes_valid_until; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quotes_valid_until ON public.quotes USING btree (valid_until);


--
-- Name: ix_rate_limit_events_bucket_identity_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_rate_limit_events_bucket_identity_at ON public.rate_limit_events USING btree (bucket, identity, at);


--
-- Name: ix_recurring_invoices_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_invoices_company_id ON public.recurring_invoices USING btree (company_id);


--
-- Name: ix_recurring_invoices_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_invoices_entity_id ON public.recurring_invoices USING btree (entity_id);


--
-- Name: ix_recurring_invoices_next_run_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_invoices_next_run_date ON public.recurring_invoices USING btree (next_run_date);


--
-- Name: ix_recurring_invoices_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_invoices_status ON public.recurring_invoices USING btree (status);


--
-- Name: ix_recurring_rules_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_rules_company_id ON public.recurring_rules USING btree (company_id);


--
-- Name: ix_recurring_rules_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_rules_entity_id ON public.recurring_rules USING btree (entity_id);


--
-- Name: ix_recurring_rules_next_run_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_rules_next_run_date ON public.recurring_rules USING btree (next_run_date);


--
-- Name: ix_recurring_rules_start_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recurring_rules_start_date ON public.recurring_rules USING btree (start_date);


--
-- Name: ix_reminders_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reminders_company_id ON public.reminders USING btree (company_id);


--
-- Name: ix_reminders_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reminders_due_date ON public.reminders USING btree (due_date);


--
-- Name: ix_reminders_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reminders_user_id ON public.reminders USING btree (user_id);


--
-- Name: ix_shareholdings_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_shareholdings_company_id ON public.shareholdings USING btree (company_id);


--
-- Name: ix_shareholdings_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_shareholdings_entity_id ON public.shareholdings USING btree (entity_id);


--
-- Name: ix_tax_rates_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tax_rates_code ON public.tax_rates USING btree (code);


--
-- Name: ix_tax_rates_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tax_rates_company_id ON public.tax_rates USING btree (company_id);


--
-- Name: ix_tax_rates_effective_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tax_rates_effective_from ON public.tax_rates USING btree (effective_from);


--
-- Name: ix_tax_rates_jurisdiction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tax_rates_jurisdiction ON public.tax_rates USING btree (jurisdiction);


--
-- Name: ix_time_entries_client_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_client_id ON public.time_entries USING btree (client_id);


--
-- Name: ix_time_entries_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_company_id ON public.time_entries USING btree (company_id);


--
-- Name: ix_time_entries_employee_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_employee_id ON public.time_entries USING btree (employee_id);


--
-- Name: ix_time_entries_invoice_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_invoice_id ON public.time_entries USING btree (invoice_id);


--
-- Name: ix_time_entries_payroll_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_payroll_run_id ON public.time_entries USING btree (payroll_run_id);


--
-- Name: ix_time_entries_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_project_id ON public.time_entries USING btree (project_id);


--
-- Name: ix_time_entries_source_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_source_external ON public.time_entries USING btree (source, external_id);


--
-- Name: ix_time_entries_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_status ON public.time_entries USING btree (status);


--
-- Name: ix_time_entries_work_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_time_entries_work_date ON public.time_entries USING btree (work_date);


--
-- Name: ix_transaction_attachments_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_attachments_company_id ON public.transaction_attachments USING btree (company_id);


--
-- Name: ix_transaction_attachments_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_attachments_transaction_id ON public.transaction_attachments USING btree (transaction_id);


--
-- Name: ix_transaction_entities_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_entities_company_id ON public.transaction_entities USING btree (company_id);


--
-- Name: ix_transaction_entities_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_entities_entity_id ON public.transaction_entities USING btree (entity_id);


--
-- Name: ix_transaction_entities_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_entities_role ON public.transaction_entities USING btree (role);


--
-- Name: ix_transaction_entities_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_entities_transaction_id ON public.transaction_entities USING btree (transaction_id);


--
-- Name: ix_transaction_fee_applications_bank_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fee_applications_bank_id ON public.transaction_fee_applications USING btree (bank_id);


--
-- Name: ix_transaction_fee_applications_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fee_applications_company_id ON public.transaction_fee_applications USING btree (company_id);


--
-- Name: ix_transaction_fee_applications_fee_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fee_applications_fee_rule_id ON public.transaction_fee_applications USING btree (fee_rule_id);


--
-- Name: ix_transaction_fee_applications_method_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fee_applications_method_id ON public.transaction_fee_applications USING btree (method_id);


--
-- Name: ix_transaction_fee_applications_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fee_applications_status ON public.transaction_fee_applications USING btree (status);


--
-- Name: ix_transaction_fee_applications_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_transaction_fee_applications_transaction_id ON public.transaction_fee_applications USING btree (transaction_id);


--
-- Name: ix_transaction_fees_bank_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fees_bank_id ON public.transaction_fees USING btree (bank_id);


--
-- Name: ix_transaction_fees_effective_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fees_effective_from ON public.transaction_fees USING btree (effective_from);


--
-- Name: ix_transaction_fees_fee_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fees_fee_type ON public.transaction_fees USING btree (fee_type);


--
-- Name: ix_transaction_fees_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fees_is_active ON public.transaction_fees USING btree (is_active);


--
-- Name: ix_transaction_fees_method_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_fees_method_id ON public.transaction_fees USING btree (method_id);


--
-- Name: ix_transaction_lines_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_lines_account_id ON public.transaction_lines USING btree (account_id);


--
-- Name: ix_transaction_lines_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_lines_company_id ON public.transaction_lines USING btree (company_id);


--
-- Name: ix_transaction_lines_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_lines_transaction_id ON public.transaction_lines USING btree (transaction_id);


--
-- Name: ix_transaction_versions_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_versions_company_id ON public.transaction_versions USING btree (company_id);


--
-- Name: ix_transaction_versions_transaction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_versions_transaction_id ON public.transaction_versions USING btree (transaction_id);


--
-- Name: ix_transaction_versions_version; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transaction_versions_version ON public.transaction_versions USING btree (version);


--
-- Name: ix_transactions_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_company_id ON public.transactions USING btree (company_id);


--
-- Name: ix_transactions_currency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_currency ON public.transactions USING btree (currency);


--
-- Name: ix_transactions_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_date ON public.transactions USING btree (date);


--
-- Name: ix_transactions_deleted_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_deleted_at ON public.transactions USING btree (deleted_at);


--
-- Name: ix_transactions_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_transactions_reference ON public.transactions USING btree (reference);


--
-- Name: ix_trial_balance_lines_account_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_trial_balance_lines_account_code ON public.trial_balance_lines USING btree (account_code);


--
-- Name: ix_trial_balance_lines_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_trial_balance_lines_account_id ON public.trial_balance_lines USING btree (account_id);


--
-- Name: ix_trial_balance_lines_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_trial_balance_lines_company_id ON public.trial_balance_lines USING btree (company_id);


--
-- Name: ix_trial_balance_lines_trial_balance_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_trial_balance_lines_trial_balance_id ON public.trial_balance_lines USING btree (trial_balance_id);


--
-- Name: ix_trial_balances_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_trial_balances_company_id ON public.trial_balances USING btree (company_id);


--
-- Name: ix_upload_tokens_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_upload_tokens_company_id ON public.upload_tokens USING btree (company_id);


--
-- Name: ix_upload_tokens_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_upload_tokens_expires_at ON public.upload_tokens USING btree (expires_at);


--
-- Name: ix_users_company_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_company_id ON public.users USING btree (company_id);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: ix_users_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_entity_id ON public.users USING btree (entity_id);


--
-- Name: ix_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_username ON public.users USING btree (username);


--
-- Name: ix_users_verification_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_verification_token ON public.users USING btree (verification_token);


--
-- Name: uq_app_settings_company_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_app_settings_company_key ON public.app_settings USING btree (company_id, key) WHERE (company_id IS NOT NULL);


--
-- Name: uq_app_settings_global_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_app_settings_global_key ON public.app_settings USING btree (key) WHERE (company_id IS NULL);


--
-- Name: audit_logs trg_audit_logs_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_logs_append_only BEFORE DELETE OR UPDATE ON public.audit_logs FOR EACH ROW EXECUTE FUNCTION public.audit_logs_append_only();


--
-- Name: accounts accounts_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.accounts(id);


--
-- Name: ai_chat_messages ai_chat_messages_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_chat_messages
    ADD CONSTRAINT ai_chat_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.ai_chat_sessions(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: bank_statement_rows bank_statement_rows_created_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_statement_rows
    ADD CONSTRAINT bank_statement_rows_created_transaction_id_fkey FOREIGN KEY (created_transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: bank_statement_rows bank_statement_rows_matched_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_statement_rows
    ADD CONSTRAINT bank_statement_rows_matched_transaction_id_fkey FOREIGN KEY (matched_transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: bank_statement_rows bank_statement_rows_statement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_statement_rows
    ADD CONSTRAINT bank_statement_rows_statement_id_fkey FOREIGN KEY (statement_id) REFERENCES public.bank_statements(id) ON DELETE CASCADE;


--
-- Name: billing_rate_overrides billing_rate_overrides_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_rate_overrides
    ADD CONSTRAINT billing_rate_overrides_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: billing_rate_overrides billing_rate_overrides_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_rate_overrides
    ADD CONSTRAINT billing_rate_overrides_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: billing_rate_overrides billing_rate_overrides_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_rate_overrides
    ADD CONSTRAINT billing_rate_overrides_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: commitments commitments_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE SET NULL;


--
-- Name: commitments commitments_settled_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commitments
    ADD CONSTRAINT commitments_settled_transaction_id_fkey FOREIGN KEY (settled_transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: company_profiles company_profiles_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_profiles
    ADD CONSTRAINT company_profiles_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: credit_notes credit_notes_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_notes
    ADD CONSTRAINT credit_notes_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id);


--
-- Name: credit_notes credit_notes_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_notes
    ADD CONSTRAINT credit_notes_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE SET NULL;


--
-- Name: credit_notes credit_notes_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_notes
    ADD CONSTRAINT credit_notes_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id);


--
-- Name: digest_settings digest_settings_company_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.digest_settings
    ADD CONSTRAINT digest_settings_company_id_fkey FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: employee_pay_profiles employee_pay_profiles_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_pay_profiles
    ADD CONSTRAINT employee_pay_profiles_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: equity_events equity_events_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equity_events
    ADD CONSTRAINT equity_events_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE SET NULL;


--
-- Name: equity_events equity_events_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equity_events
    ADD CONSTRAINT equity_events_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: accounts fk_accounts_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT fk_accounts_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: adjustments fk_adjustments_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adjustments
    ADD CONSTRAINT fk_adjustments_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: ai_chat_messages fk_ai_chat_messages_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_chat_messages
    ADD CONSTRAINT fk_ai_chat_messages_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: ai_chat_sessions fk_ai_chat_sessions_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_chat_sessions
    ADD CONSTRAINT fk_ai_chat_sessions_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: ai_proposals fk_ai_proposals_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_proposals
    ADD CONSTRAINT fk_ai_proposals_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: app_settings fk_app_settings_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT fk_app_settings_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: audit_logs fk_audit_logs_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT fk_audit_logs_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE RESTRICT;


--
-- Name: bank_statement_rows fk_bank_statement_rows_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_statement_rows
    ADD CONSTRAINT fk_bank_statement_rows_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: bank_statements fk_bank_statements_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_statements
    ADD CONSTRAINT fk_bank_statements_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: billing_rate_overrides fk_billing_rate_overrides_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.billing_rate_overrides
    ADD CONSTRAINT fk_billing_rate_overrides_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: budget_limits fk_budget_limits_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.budget_limits
    ADD CONSTRAINT fk_budget_limits_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: credit_notes fk_credit_notes_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.credit_notes
    ADD CONSTRAINT fk_credit_notes_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: employee_pay_profiles fk_employee_pay_profiles_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_pay_profiles
    ADD CONSTRAINT fk_employee_pay_profiles_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: entities fk_entities_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.entities
    ADD CONSTRAINT fk_entities_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: goods_receipt_lines fk_goods_receipt_lines_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.goods_receipt_lines
    ADD CONSTRAINT fk_goods_receipt_lines_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: goods_receipts fk_goods_receipts_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.goods_receipts
    ADD CONSTRAINT fk_goods_receipts_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: integrity_checks fk_integrity_checks_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integrity_checks
    ADD CONSTRAINT fk_integrity_checks_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: inventory_items fk_inventory_items_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_items
    ADD CONSTRAINT fk_inventory_items_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: inventory_movements fk_inventory_movements_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_movements
    ADD CONSTRAINT fk_inventory_movements_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: invoice_items fk_invoice_items_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT fk_invoice_items_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: invoices fk_invoices_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT fk_invoices_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: invoices fk_invoices_recurring_invoice; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT fk_invoices_recurring_invoice FOREIGN KEY (recurring_invoice_id) REFERENCES public.recurring_invoices(id) ON DELETE SET NULL;


--
-- Name: mileage_claims fk_mileage_claims_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mileage_claims
    ADD CONSTRAINT fk_mileage_claims_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: pay_run_lines fk_pay_run_lines_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_run_lines
    ADD CONSTRAINT fk_pay_run_lines_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: pay_runs fk_pay_runs_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_runs
    ADD CONSTRAINT fk_pay_runs_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: payments fk_payments_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT fk_payments_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: projects fk_projects_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: purchase_order_lines fk_purchase_order_lines_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_lines
    ADD CONSTRAINT fk_purchase_order_lines_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: purchase_orders fk_purchase_orders_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT fk_purchase_orders_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: recurring_rules fk_recurring_rules_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_rules
    ADD CONSTRAINT fk_recurring_rules_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: tax_rates fk_tax_rates_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tax_rates
    ADD CONSTRAINT fk_tax_rates_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: time_entries fk_time_entries_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.time_entries
    ADD CONSTRAINT fk_time_entries_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: time_entries fk_time_entries_payroll_run; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.time_entries
    ADD CONSTRAINT fk_time_entries_payroll_run FOREIGN KEY (payroll_run_id) REFERENCES public.pay_runs(id) ON DELETE SET NULL;


--
-- Name: transaction_attachments fk_transaction_attachments_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_attachments
    ADD CONSTRAINT fk_transaction_attachments_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: transaction_entities fk_transaction_entities_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_entities
    ADD CONSTRAINT fk_transaction_entities_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: transaction_fee_applications fk_transaction_fee_applications_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fee_applications
    ADD CONSTRAINT fk_transaction_fee_applications_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: transaction_lines fk_transaction_lines_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_lines
    ADD CONSTRAINT fk_transaction_lines_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: transaction_versions fk_transaction_versions_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_versions
    ADD CONSTRAINT fk_transaction_versions_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: transactions fk_transactions_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT fk_transactions_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: trial_balance_lines fk_trial_balance_lines_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trial_balance_lines
    ADD CONSTRAINT fk_trial_balance_lines_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: trial_balances fk_trial_balances_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trial_balances
    ADD CONSTRAINT fk_trial_balances_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE CASCADE;


--
-- Name: users fk_users_company; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_company FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE SET NULL;


--
-- Name: users fk_users_entity; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_entity FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE SET NULL;


--
-- Name: goods_receipt_lines goods_receipt_lines_po_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.goods_receipt_lines
    ADD CONSTRAINT goods_receipt_lines_po_line_id_fkey FOREIGN KEY (po_line_id) REFERENCES public.purchase_order_lines(id) ON DELETE CASCADE;


--
-- Name: goods_receipt_lines goods_receipt_lines_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.goods_receipt_lines
    ADD CONSTRAINT goods_receipt_lines_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.goods_receipts(id) ON DELETE CASCADE;


--
-- Name: goods_receipts goods_receipts_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.goods_receipts
    ADD CONSTRAINT goods_receipts_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: inventory_movements inventory_movements_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_movements
    ADD CONSTRAINT inventory_movements_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id);


--
-- Name: inventory_movements inventory_movements_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_movements
    ADD CONSTRAINT inventory_movements_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.inventory_items(id) ON DELETE CASCADE;


--
-- Name: inventory_movements inventory_movements_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_movements
    ADD CONSTRAINT inventory_movements_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id);


--
-- Name: invoice_emails invoice_emails_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_emails
    ADD CONSTRAINT invoice_emails_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;


--
-- Name: invoice_items invoice_items_inventory_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_inventory_item_id_fkey FOREIGN KEY (inventory_item_id) REFERENCES public.inventory_items(id);


--
-- Name: invoice_items invoice_items_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;


--
-- Name: invoices invoices_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id);


--
-- Name: invoices invoices_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id);


--
-- Name: migration_batches migration_batches_opening_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_batches
    ADD CONSTRAINT migration_batches_opening_transaction_id_fkey FOREIGN KEY (opening_transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: migration_pending_records migration_pending_records_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_pending_records
    ADD CONSTRAINT migration_pending_records_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.migration_batches(id) ON DELETE CASCADE;


--
-- Name: migration_pending_records migration_pending_records_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_pending_records
    ADD CONSTRAINT migration_pending_records_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: mileage_claims mileage_claims_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mileage_claims
    ADD CONSTRAINT mileage_claims_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id);


--
-- Name: mileage_claims mileage_claims_reimbursement_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mileage_claims
    ADD CONSTRAINT mileage_claims_reimbursement_transaction_id_fkey FOREIGN KEY (reimbursement_transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: mileage_claims mileage_claims_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mileage_claims
    ADD CONSTRAINT mileage_claims_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: pay_run_lines pay_run_lines_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_run_lines
    ADD CONSTRAINT pay_run_lines_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: pay_run_lines pay_run_lines_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_run_lines
    ADD CONSTRAINT pay_run_lines_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.pay_runs(id) ON DELETE CASCADE;


--
-- Name: pay_runs pay_runs_pay_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_runs
    ADD CONSTRAINT pay_runs_pay_transaction_id_fkey FOREIGN KEY (pay_transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: pay_runs pay_runs_post_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pay_runs
    ADD CONSTRAINT pay_runs_post_transaction_id_fkey FOREIGN KEY (post_transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: payments payments_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;


--
-- Name: payments payments_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id);


--
-- Name: petty_cash_accounts petty_cash_accounts_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.petty_cash_accounts
    ADD CONSTRAINT petty_cash_accounts_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE SET NULL;


--
-- Name: petty_cash_transactions petty_cash_transactions_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.petty_cash_transactions
    ADD CONSTRAINT petty_cash_transactions_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.petty_cash_accounts(id) ON DELETE CASCADE;


--
-- Name: petty_cash_transactions petty_cash_transactions_attachment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.petty_cash_transactions
    ADD CONSTRAINT petty_cash_transactions_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES public.transaction_attachments(id) ON DELETE SET NULL;


--
-- Name: petty_cash_transactions petty_cash_transactions_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.petty_cash_transactions
    ADD CONSTRAINT petty_cash_transactions_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: projects projects_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: purchase_order_lines purchase_order_lines_inventory_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_lines
    ADD CONSTRAINT purchase_order_lines_inventory_item_id_fkey FOREIGN KEY (inventory_item_id) REFERENCES public.inventory_items(id);


--
-- Name: purchase_order_lines purchase_order_lines_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_lines
    ADD CONSTRAINT purchase_order_lines_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: purchase_orders purchase_orders_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id);


--
-- Name: purchase_orders purchase_orders_matched_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_matched_invoice_id_fkey FOREIGN KEY (matched_invoice_id) REFERENCES public.invoices(id) ON DELETE SET NULL;


--
-- Name: quote_items quote_items_inventory_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quote_items
    ADD CONSTRAINT quote_items_inventory_item_id_fkey FOREIGN KEY (inventory_item_id) REFERENCES public.inventory_items(id);


--
-- Name: quote_items quote_items_quote_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quote_items
    ADD CONSTRAINT quote_items_quote_id_fkey FOREIGN KEY (quote_id) REFERENCES public.quotes(id) ON DELETE CASCADE;


--
-- Name: quotes quotes_converted_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT quotes_converted_invoice_id_fkey FOREIGN KEY (converted_invoice_id) REFERENCES public.invoices(id) ON DELETE SET NULL;


--
-- Name: quotes quotes_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotes
    ADD CONSTRAINT quotes_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id);


--
-- Name: recurring_invoices recurring_invoices_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_invoices
    ADD CONSTRAINT recurring_invoices_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id);


--
-- Name: recurring_invoices recurring_invoices_last_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_invoices
    ADD CONSTRAINT recurring_invoices_last_invoice_id_fkey FOREIGN KEY (last_invoice_id) REFERENCES public.invoices(id) ON DELETE SET NULL;


--
-- Name: recurring_rules recurring_rules_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recurring_rules
    ADD CONSTRAINT recurring_rules_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id);


--
-- Name: shareholdings shareholdings_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shareholdings
    ADD CONSTRAINT shareholdings_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: time_entries time_entries_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.time_entries
    ADD CONSTRAINT time_entries_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: time_entries time_entries_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.time_entries
    ADD CONSTRAINT time_entries_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: time_entries time_entries_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.time_entries
    ADD CONSTRAINT time_entries_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE SET NULL;


--
-- Name: time_entries time_entries_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.time_entries
    ADD CONSTRAINT time_entries_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE SET NULL;


--
-- Name: transaction_attachments transaction_attachments_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_attachments
    ADD CONSTRAINT transaction_attachments_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE SET NULL;


--
-- Name: transaction_entities transaction_entities_entity_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_entities
    ADD CONSTRAINT transaction_entities_entity_id_fkey FOREIGN KEY (entity_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: transaction_entities transaction_entities_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_entities
    ADD CONSTRAINT transaction_entities_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE CASCADE;


--
-- Name: transaction_fee_applications transaction_fee_applications_bank_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fee_applications
    ADD CONSTRAINT transaction_fee_applications_bank_id_fkey FOREIGN KEY (bank_id) REFERENCES public.entities(id) ON DELETE SET NULL;


--
-- Name: transaction_fee_applications transaction_fee_applications_fee_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fee_applications
    ADD CONSTRAINT transaction_fee_applications_fee_rule_id_fkey FOREIGN KEY (fee_rule_id) REFERENCES public.transaction_fees(id) ON DELETE SET NULL;


--
-- Name: transaction_fee_applications transaction_fee_applications_method_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fee_applications
    ADD CONSTRAINT transaction_fee_applications_method_id_fkey FOREIGN KEY (method_id) REFERENCES public.payment_methods(id) ON DELETE SET NULL;


--
-- Name: transaction_fee_applications transaction_fee_applications_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fee_applications
    ADD CONSTRAINT transaction_fee_applications_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE CASCADE;


--
-- Name: transaction_fees transaction_fees_bank_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fees
    ADD CONSTRAINT transaction_fees_bank_id_fkey FOREIGN KEY (bank_id) REFERENCES public.entities(id) ON DELETE CASCADE;


--
-- Name: transaction_fees transaction_fees_method_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_fees
    ADD CONSTRAINT transaction_fees_method_id_fkey FOREIGN KEY (method_id) REFERENCES public.payment_methods(id) ON DELETE CASCADE;


--
-- Name: transaction_lines transaction_lines_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_lines
    ADD CONSTRAINT transaction_lines_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- Name: transaction_lines transaction_lines_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transaction_lines
    ADD CONSTRAINT transaction_lines_transaction_id_fkey FOREIGN KEY (transaction_id) REFERENCES public.transactions(id) ON DELETE CASCADE;


--
-- Name: trial_balance_lines trial_balance_lines_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trial_balance_lines
    ADD CONSTRAINT trial_balance_lines_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- Name: trial_balance_lines trial_balance_lines_trial_balance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trial_balance_lines
    ADD CONSTRAINT trial_balance_lines_trial_balance_id_fkey FOREIGN KEY (trial_balance_id) REFERENCES public.trial_balances(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


INSERT INTO public.alembic_version (version_num) VALUES ('044');
