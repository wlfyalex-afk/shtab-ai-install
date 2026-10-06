-- Штаб.AI 0.19.0 portable schema for PostgreSQL 15 / Ubuntu.
-- Generated from schema-astra.sql; data, owners and privileges are absent.

--
-- PostgreSQL database dump
--


-- Dumped from database version 15.17 (Debian 15.17-astra.se2.3r2)
-- Dumped by pg_dump version 15.17 (Debian 15.17-astra.se2.3r2)

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
--



--
--



--
--



--
--



SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_events (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    actor_type text NOT NULL,
    actor_id uuid,
    event_type text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid,
    correlation_id uuid,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT audit_events_actor_type_check CHECK ((actor_type = ANY (ARRAY['USER'::text, 'SYSTEM'::text, 'ASTERISK'::text, 'WORKER'::text]))),
    CONSTRAINT audit_events_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
--



--
--



--
-- Name: call_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_attempts (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    call_job_id uuid NOT NULL,
    attempt_no smallint NOT NULL,
    provider_call_id text,
    status text NOT NULL,
    started_at timestamp with time zone,
    answered_at timestamp with time zone,
    ended_at timestamp with time zone,
    hangup_cause integer,
    error_code text,
    error_detail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    dialed_kind text,
    dialed_value text,
    prompt_text text,
    prompt_storage_path text,
    recording_storage_path text,
    CONSTRAINT call_attempts_attempt_no_check CHECK ((attempt_no > 0)),
    CONSTRAINT call_attempts_check CHECK ((updated_at >= created_at)),
    CONSTRAINT call_attempts_check1 CHECK (((answered_at IS NULL) OR (started_at IS NULL) OR (answered_at >= started_at))),
    CONSTRAINT call_attempts_check2 CHECK (((ended_at IS NULL) OR (started_at IS NULL) OR (ended_at >= started_at))),
    CONSTRAINT call_attempts_status_check CHECK ((status = ANY (ARRAY['CREATED'::text, 'ORIGINATING'::text, 'RINGING'::text, 'ANSWERED'::text, 'RECORDING'::text, 'PROCESSING'::text, 'COMPLETED'::text, 'BUSY'::text, 'NO_ANSWER'::text, 'UNAVAILABLE'::text, 'FAILED'::text, 'CANCELLED'::text])))
);


--
-- Name: COLUMN call_attempts.dialed_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.call_attempts.dialed_kind IS 'Immutable snapshot of contact kind used for this attempt';


--
-- Name: COLUMN call_attempts.dialed_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.call_attempts.dialed_value IS 'Immutable snapshot of the number or address actually dialed';


--
-- Name: COLUMN call_attempts.prompt_text; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.call_attempts.prompt_text IS 'Exact text synthesized and played to the assignee';


--
-- Name: COLUMN call_attempts.prompt_storage_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.call_attempts.prompt_storage_path IS 'Local immutable prompt WAV path on the dispatcher host';


--
-- Name: COLUMN call_attempts.recording_storage_path; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.call_attempts.recording_storage_path IS 'Expected response WAV path on the Asterisk host';


--
--



--
--



--
-- Name: call_job_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_job_tasks (
    call_job_id uuid NOT NULL,
    task_id uuid NOT NULL,
    sequence_no smallint NOT NULL,
    CONSTRAINT call_job_tasks_sequence_no_check CHECK ((sequence_no > 0))
);


--
--



--
--



--
-- Name: call_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.call_jobs (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    person_id uuid NOT NULL,
    contact_point_id uuid NOT NULL,
    provider text DEFAULT 'asterisk'::text NOT NULL,
    prompt_profile text DEFAULT 'ru_default'::text NOT NULL,
    scheduled_at timestamp with time zone NOT NULL,
    status text DEFAULT 'QUEUED'::text NOT NULL,
    attempt_count smallint DEFAULT 0 NOT NULL,
    max_attempts smallint DEFAULT 3 NOT NULL,
    idempotency_key text NOT NULL,
    locked_by text,
    locked_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT call_jobs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT call_jobs_check CHECK ((attempt_count <= max_attempts)),
    CONSTRAINT call_jobs_check1 CHECK ((updated_at >= created_at)),
    CONSTRAINT call_jobs_idempotency_key_check CHECK ((length(idempotency_key) >= 8)),
    CONSTRAINT call_jobs_max_attempts_check CHECK (((max_attempts >= 1) AND (max_attempts <= 20))),
    CONSTRAINT call_jobs_status_check CHECK ((status = ANY (ARRAY['QUEUED'::text, 'SCHEDULED'::text, 'RUNNING'::text, 'RETRY_WAIT'::text, 'COMPLETED'::text, 'FAILED'::text, 'CANCELLED'::text])))
);


--
--



--
--



--
-- Name: contact_points; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_points (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    person_id uuid NOT NULL,
    kind text NOT NULL,
    label text NOT NULL,
    value text NOT NULL,
    normalized_value text NOT NULL,
    priority smallint DEFAULT 1 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    allow_automated_calls boolean DEFAULT false NOT NULL,
    call_windows jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT contact_points_call_windows_check CHECK ((jsonb_typeof(call_windows) = 'array'::text)),
    CONSTRAINT contact_points_check CHECK ((updated_at >= created_at)),
    CONSTRAINT contact_points_kind_check CHECK ((kind = ANY (ARRAY['EXTENSION'::text, 'MOBILE'::text, 'LANDLINE'::text, 'SIP_URI'::text]))),
    CONSTRAINT contact_points_label_check CHECK ((length(btrim(label)) > 0)),
    CONSTRAINT contact_points_normalized_value_check CHECK ((length(btrim(normalized_value)) > 0)),
    CONSTRAINT contact_points_priority_check CHECK (((priority >= 1) AND (priority <= 99))),
    CONSTRAINT contact_points_value_check CHECK ((length(btrim(value)) > 0))
);


--
--



--
--



--
-- Name: manual_call_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manual_call_sessions (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    task_id uuid NOT NULL,
    person_id uuid NOT NULL,
    contact_point_id uuid NOT NULL,
    secretary_id uuid NOT NULL,
    status text DEFAULT 'PREPARED'::text NOT NULL,
    outcome text,
    reported_status text,
    note text,
    promised_due_text text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT manual_call_sessions_check CHECK ((((status = 'PREPARED'::text) AND (finished_at IS NULL) AND (outcome IS NULL)) OR ((status <> 'PREPARED'::text) AND (finished_at IS NOT NULL) AND (outcome IS NOT NULL)))),
    CONSTRAINT manual_call_sessions_note_check CHECK (((note IS NULL) OR (length(note) <= 2000))),
    CONSTRAINT manual_call_sessions_outcome_check CHECK ((outcome = ANY (ARRAY['CONNECTED'::text, 'NO_ANSWER'::text, 'BUSY'::text, 'CALLBACK'::text, 'REFUSED'::text, 'WRONG_NUMBER'::text, 'CANCELLED'::text]))),
    CONSTRAINT manual_call_sessions_promised_due_text_check CHECK (((promised_due_text IS NULL) OR (length(promised_due_text) <= 200))),
    CONSTRAINT manual_call_sessions_reported_status_check CHECK ((reported_status = ANY (ARRAY['CLAIMED_DONE'::text, 'NOT_DONE'::text, 'BLOCKED'::text, 'UNKNOWN'::text]))),
    CONSTRAINT manual_call_sessions_status_check CHECK ((status = ANY (ARRAY['PREPARED'::text, 'COMPLETED'::text, 'CANCELLED'::text])))
);


--
--



--
--



--
-- Name: meeting_brief_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_brief_attempts (
    id bigint NOT NULL,
    job_id uuid NOT NULL,
    run_no integer DEFAULT 0 NOT NULL,
    stage text NOT NULL,
    chunk_no integer,
    attempt_no integer NOT NULL,
    outcome text NOT NULL,
    done_reason text,
    input_bytes integer DEFAULT 0 NOT NULL,
    output_bytes integer DEFAULT 0 NOT NULL,
    prompt_tokens integer,
    output_tokens integer,
    elapsed_seconds double precision DEFAULT 0 NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT meeting_brief_attempts_attempt_no_check CHECK ((attempt_no >= 0)),
    CONSTRAINT meeting_brief_attempts_elapsed_seconds_check CHECK ((elapsed_seconds >= (0)::double precision)),
    CONSTRAINT meeting_brief_attempts_input_bytes_check CHECK ((input_bytes >= 0)),
    CONSTRAINT meeting_brief_attempts_outcome_check CHECK ((outcome = ANY (ARRAY['SUCCESS'::text, 'INCOMPLETE'::text, 'INVALID'::text, 'ERROR'::text, 'FALLBACK'::text]))),
    CONSTRAINT meeting_brief_attempts_output_bytes_check CHECK ((output_bytes >= 0)),
    CONSTRAINT meeting_brief_attempts_output_tokens_check CHECK (((output_tokens IS NULL) OR (output_tokens >= 0))),
    CONSTRAINT meeting_brief_attempts_prompt_tokens_check CHECK (((prompt_tokens IS NULL) OR (prompt_tokens >= 0))),
    CONSTRAINT meeting_brief_attempts_run_no_check CHECK ((run_no >= 0))
);


--
--



--
--



--
-- Name: meeting_brief_attempts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.meeting_brief_attempts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: meeting_brief_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.meeting_brief_attempts_id_seq OWNED BY public.meeting_brief_attempts.id;


--
--



--
--



--
-- Name: meeting_brief_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_brief_chunks (
    job_id uuid NOT NULL,
    chunk_no integer NOT NULL,
    response jsonb NOT NULL,
    evidence jsonb NOT NULL,
    elapsed_seconds double precision NOT NULL,
    rejected_count integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT meeting_brief_chunks_evidence_check CHECK ((jsonb_typeof(evidence) = 'array'::text))
);


--
--



--
--



--
-- Name: meeting_brief_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_brief_jobs (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    transcript_id uuid NOT NULL,
    method text NOT NULL,
    model text NOT NULL,
    model_digest text,
    source_hash text NOT NULL,
    status text DEFAULT 'QUEUED'::text NOT NULL,
    next_chunk integer DEFAULT 0 NOT NULL,
    total_chunks integer NOT NULL,
    evidence_count integer DEFAULT 0 NOT NULL,
    rejected_count integer DEFAULT 0 NOT NULL,
    error_code text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    final_elapsed_seconds double precision DEFAULT 0 NOT NULL,
    retry_count integer DEFAULT 0 NOT NULL,
    last_stage text,
    last_done_reason text,
    last_prompt_tokens integer,
    last_output_tokens integer,
    last_input_bytes integer,
    last_output_bytes integer,
    last_attempt_at timestamp with time zone,
    CONSTRAINT meeting_brief_jobs_final_elapsed_seconds_check CHECK ((final_elapsed_seconds >= (0)::double precision)),
    CONSTRAINT meeting_brief_jobs_last_input_bytes_check CHECK (((last_input_bytes IS NULL) OR (last_input_bytes >= 0))),
    CONSTRAINT meeting_brief_jobs_last_output_bytes_check CHECK (((last_output_bytes IS NULL) OR (last_output_bytes >= 0))),
    CONSTRAINT meeting_brief_jobs_last_output_tokens_check CHECK (((last_output_tokens IS NULL) OR (last_output_tokens >= 0))),
    CONSTRAINT meeting_brief_jobs_last_prompt_tokens_check CHECK (((last_prompt_tokens IS NULL) OR (last_prompt_tokens >= 0))),
    CONSTRAINT meeting_brief_jobs_next_chunk_check CHECK ((next_chunk >= 0)),
    CONSTRAINT meeting_brief_jobs_retry_count_check CHECK ((retry_count >= 0)),
    CONSTRAINT meeting_brief_jobs_status_check CHECK ((status = ANY (ARRAY['QUEUED'::text, 'RUNNING'::text, 'DONE'::text, 'FAILED'::text, 'CANCELLED'::text]))),
    CONSTRAINT meeting_brief_jobs_total_chunks_check CHECK ((total_chunks > 0))
);


--
--



--
--



--
-- Name: meeting_briefs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_briefs (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    transcript_id uuid NOT NULL,
    job_id uuid NOT NULL,
    method text NOT NULL,
    model text NOT NULL,
    model_digest text NOT NULL,
    content jsonb NOT NULL,
    review_status text DEFAULT 'DRAFT'::text NOT NULL,
    approved_by uuid,
    approved_at timestamp with time zone,
    review_comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT meeting_briefs_check CHECK (((review_status <> 'APPROVED'::text) OR ((approved_by IS NOT NULL) AND (approved_at IS NOT NULL)))),
    CONSTRAINT meeting_briefs_content_check CHECK ((jsonb_typeof(content) = 'object'::text)),
    CONSTRAINT meeting_briefs_review_status_check CHECK ((review_status = ANY (ARRAY['DRAFT'::text, 'APPROVED'::text, 'SUPERSEDED'::text])))
);


--
--



--
--



--
-- Name: meeting_import_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_import_chunks (
    import_id uuid NOT NULL,
    part_no integer NOT NULL,
    size_bytes integer NOT NULL,
    sha256 character(64) NOT NULL,
    CONSTRAINT meeting_import_chunks_part_no_check CHECK ((part_no >= 0)),
    CONSTRAINT meeting_import_chunks_sha256_check CHECK ((sha256 ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT meeting_import_chunks_size_bytes_check CHECK (((size_bytes >= 1) AND (size_bytes <= 2097152)))
);


--
--



--
--



--
-- Name: meeting_import_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_import_groups (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    created_by uuid NOT NULL,
    title text NOT NULL,
    meeting_at timestamp with time zone NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    primary_import_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT meeting_import_groups_status_check CHECK ((status = ANY (ARRAY['DRAFT'::text, 'QUEUED'::text, 'DONE'::text])))
);


--
--



--
--



--
-- Name: meeting_import_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_import_metrics (
    id uuid NOT NULL,
    import_id uuid NOT NULL,
    run_id uuid NOT NULL,
    stage text NOT NULL,
    state text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    elapsed_seconds double precision DEFAULT 0 NOT NULL,
    processed_seconds double precision DEFAULT 0 NOT NULL,
    duration_seconds double precision,
    received_bytes bigint DEFAULT 0 NOT NULL,
    expected_bytes bigint,
    CONSTRAINT meeting_import_metrics_state_check CHECK ((state = ANY (ARRAY['RUNNING'::text, 'DONE'::text, 'FAILED'::text, 'INTERRUPTED'::text])))
);


--
--



--
--



--
-- Name: meeting_imports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_imports (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    created_by uuid NOT NULL,
    title text NOT NULL,
    meeting_at timestamp with time zone NOT NULL,
    original_name text NOT NULL,
    total_bytes bigint NOT NULL,
    uploaded_bytes bigint DEFAULT 0 NOT NULL,
    status text DEFAULT 'UPLOADING'::text NOT NULL,
    meeting_id uuid,
    transcript_id uuid,
    processed_seconds double precision DEFAULT 0 NOT NULL,
    duration_seconds double precision,
    attempts integer DEFAULT 0 NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_kind text DEFAULT 'UPLOAD'::text NOT NULL,
    source_url text,
    downloaded_bytes bigint DEFAULT 0 NOT NULL,
    expected_download_bytes bigint,
    group_id uuid,
    group_position integer,
    upload_started_at timestamp with time zone,
    upload_finished_at timestamp with time zone,
    upload_observed_bytes bigint DEFAULT 0 NOT NULL,
    upload_last_at timestamp with time zone,
    CONSTRAINT meeting_imports_check CHECK (((uploaded_bytes >= 0) AND (uploaded_bytes <= total_bytes))),
    CONSTRAINT meeting_imports_downloaded_bytes_check CHECK (((downloaded_bytes >= 0) AND (downloaded_bytes <= '17179869184'::bigint))),
    CONSTRAINT meeting_imports_expected_download_bytes_check CHECK (((expected_download_bytes >= 1) AND (expected_download_bytes <= '17179869184'::bigint))),
    CONSTRAINT meeting_imports_source_kind_check CHECK ((source_kind = ANY (ARRAY['UPLOAD'::text, 'YANDEX'::text, 'HTTPS'::text]))),
    CONSTRAINT meeting_imports_status_check CHECK ((status = ANY (ARRAY['UPLOADING'::text, 'QUEUED'::text, 'FETCHING'::text, 'ASSEMBLING'::text, 'CONVERTING'::text, 'TRANSCRIBING'::text, 'REVIEW'::text, 'DUPLICATE'::text, 'FAILED'::text, 'CANCELLED'::text]))),
    CONSTRAINT meeting_imports_title_check CHECK (((length(title) >= 1) AND (length(title) <= 500))),
    CONSTRAINT meeting_imports_total_bytes_check CHECK (((total_bytes >= 1) AND (total_bytes <= '17179869184'::bigint)))
);


--
-- Name: COLUMN meeting_imports.source_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.meeting_imports.source_url IS 'User-provided cloud link. Do not log or expose to other organisations.';


--
--



--
--



--
-- Name: meeting_llm_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_llm_chunks (
    job_id uuid NOT NULL,
    chunk_no integer NOT NULL,
    response jsonb NOT NULL,
    elapsed_seconds double precision NOT NULL,
    created_count integer NOT NULL,
    rejected_count integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
--



--
--



--
-- Name: meeting_llm_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_llm_jobs (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    transcript_id uuid NOT NULL,
    method text NOT NULL,
    model text NOT NULL,
    model_digest text,
    source_hash text NOT NULL,
    status text DEFAULT 'QUEUED'::text NOT NULL,
    next_chunk integer DEFAULT 0 NOT NULL,
    total_chunks integer NOT NULL,
    created_count integer DEFAULT 0 NOT NULL,
    rejected_count integer DEFAULT 0 NOT NULL,
    error_code text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT meeting_llm_jobs_next_chunk_check CHECK ((next_chunk >= 0)),
    CONSTRAINT meeting_llm_jobs_status_check CHECK ((status = ANY (ARRAY['QUEUED'::text, 'RUNNING'::text, 'DONE'::text, 'FAILED'::text, 'CANCELLED'::text]))),
    CONSTRAINT meeting_llm_jobs_total_chunks_check CHECK ((total_chunks > 0))
);


--
--



--
--



--
-- Name: meeting_task_drafts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_task_drafts (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    transcript_id uuid NOT NULL,
    segment_index integer NOT NULL,
    instruction text NOT NULL,
    source_quote text NOT NULL,
    start_seconds double precision NOT NULL,
    end_seconds double precision NOT NULL,
    method text NOT NULL,
    status text DEFAULT 'PENDING'::text NOT NULL,
    task_id uuid,
    version integer DEFAULT 0 NOT NULL,
    reviewed_by uuid,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    candidate_key text DEFAULT 'segment'::text NOT NULL,
    model_analysis jsonb DEFAULT '{}'::jsonb NOT NULL,
    rejection_reason text,
    CONSTRAINT meeting_task_drafts_check CHECK ((end_seconds >= start_seconds)),
    CONSTRAINT meeting_task_drafts_rejection_reason_length CHECK (((rejection_reason IS NULL) OR ((length(rejection_reason) >= 3) AND (length(rejection_reason) <= 2000)))),
    CONSTRAINT meeting_task_drafts_segment_index_check CHECK ((segment_index >= 0)),
    CONSTRAINT meeting_task_drafts_start_seconds_check CHECK ((start_seconds >= (0)::double precision)),
    CONSTRAINT meeting_task_drafts_status_check CHECK ((status = ANY (ARRAY['PENDING'::text, 'APPROVED'::text, 'REJECTED'::text])))
);


--
--



--
--



--
-- Name: meetings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meetings (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    title text NOT NULL,
    meeting_at timestamp with time zone,
    source_path text NOT NULL,
    source_sha256 character(64) NOT NULL,
    duration_seconds numeric(12,3),
    processing_status text DEFAULT 'UPLOADED'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT meetings_check CHECK ((updated_at >= created_at)),
    CONSTRAINT meetings_duration_seconds_check CHECK ((duration_seconds > (0)::numeric)),
    CONSTRAINT meetings_processing_status_check CHECK ((processing_status = ANY (ARRAY['UPLOADED'::text, 'CONVERTING'::text, 'TRANSCRIBING'::text, 'ANALYZING'::text, 'REVIEW'::text, 'COMPLETED'::text, 'FAILED'::text]))),
    CONSTRAINT meetings_source_sha256_check CHECK ((source_sha256 ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT meetings_title_check CHECK ((length(btrim(title)) > 0))
);


--
--



--
--



--
-- Name: operation_controls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operation_controls (
    kind text NOT NULL,
    entity_id uuid NOT NULL,
    organization_id uuid NOT NULL,
    desired_state text DEFAULT 'RUNNING'::text NOT NULL,
    actual_state text DEFAULT 'PENDING'::text NOT NULL,
    reason text,
    requested_by uuid,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error text,
    CONSTRAINT operation_controls_actual_state_check CHECK ((actual_state = ANY (ARRAY['PENDING'::text, 'RUNNING'::text, 'PAUSED'::text, 'CANCELLED'::text, 'COMPLETED'::text, 'FAILED'::text]))),
    CONSTRAINT operation_controls_desired_state_check CHECK ((desired_state = ANY (ARRAY['RUNNING'::text, 'PAUSED'::text, 'CANCELLED'::text]))),
    CONSTRAINT operation_controls_kind_check CHECK ((kind = ANY (ARRAY['IMPORT'::text, 'EXTRACTION'::text, 'BRIEF'::text]))),
    CONSTRAINT operation_controls_reason_check CHECK (((reason IS NULL) OR (length(reason) <= 500)))
);


--
--



--
--



--
-- Name: organization_operation_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organization_operation_state (
    organization_id uuid NOT NULL,
    paused boolean DEFAULT false NOT NULL,
    reason text,
    updated_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT organization_operation_state_reason_check CHECK (((reason IS NULL) OR (length(reason) <= 500)))
);


--
--



--
--



--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    id uuid NOT NULL,
    name text NOT NULL,
    timezone text DEFAULT 'Asia/Vladivostok'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT organizations_name_check CHECK ((length(btrim(name)) > 0))
);


--
--



--
--



--
-- Name: people; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.people (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    display_name text NOT NULL,
    role_title text,
    aliases jsonb DEFAULT '[]'::jsonb NOT NULL,
    timezone text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT people_aliases_check CHECK ((jsonb_typeof(aliases) = 'array'::text)),
    CONSTRAINT people_check CHECK ((updated_at >= created_at)),
    CONSTRAINT people_display_name_check CHECK ((length(btrim(display_name)) > 0))
);


--
--



--
--



--
-- Name: response_worker_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.response_worker_runs (
    attempt_id uuid NOT NULL,
    tries integer DEFAULT 0 NOT NULL,
    last_error text,
    next_retry_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT response_worker_runs_tries_check CHECK ((tries >= 0))
);


--
--



--
--



--
-- Name: reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reviews (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    reviewer_id uuid NOT NULL,
    action text NOT NULL,
    before_data jsonb,
    after_data jsonb,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reviews_action_check CHECK ((action = ANY (ARRAY['CONFIRM'::text, 'CORRECT'::text, 'REJECT'::text, 'REOPEN'::text]))),
    CONSTRAINT reviews_entity_type_check CHECK ((entity_type = ANY (ARRAY['MEETING'::text, 'TRANSCRIPT'::text, 'TASK'::text, 'TASK_RESPONSE'::text])))
);


--
--



--
--



--
-- Name: secretary_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.secretary_users (
    id uuid NOT NULL,
    username text NOT NULL,
    person_id uuid NOT NULL,
    password_hash text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    session_version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_admin boolean DEFAULT false NOT NULL,
    app_role text DEFAULT 'secretary'::text NOT NULL,
    CONSTRAINT secretary_users_app_role_check CHECK ((app_role = ANY (ARRAY['secretary'::text, 'head'::text])))
);


--
-- Name: COLUMN secretary_users.is_admin; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.secretary_users.is_admin IS 'Single system administrator across organizations; tenant users are secretary or head';


--
-- Name: COLUMN secretary_users.app_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.secretary_users.app_role IS 'Non-admin access: secretary edits, head reads; is_admin takes precedence';


--
--



--
--



--
-- Name: task_assignees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_assignees (
    task_id uuid NOT NULL,
    person_id uuid NOT NULL,
    role text NOT NULL,
    CONSTRAINT task_assignees_role_check CHECK ((role = ANY (ARRAY['PRIMARY'::text, 'CO_ASSIGNEE'::text, 'OBSERVER'::text])))
);


--
--



--
--



--
-- Name: task_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_comments (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    task_id uuid NOT NULL,
    author_id uuid NOT NULL,
    author_role text NOT NULL,
    body text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT task_comments_author_role_check CHECK ((author_role = ANY (ARRAY['admin'::text, 'head'::text, 'secretary'::text]))),
    CONSTRAINT task_comments_body_check CHECK (((length(body) >= 1) AND (length(body) <= 4000)))
);


--
--



--
--



--
-- Name: task_responses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_responses (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    call_job_id uuid NOT NULL,
    call_attempt_id uuid NOT NULL,
    task_id uuid NOT NULL,
    person_id uuid NOT NULL,
    answer_index smallint NOT NULL,
    audio_path text NOT NULL,
    audio_sha256 character(64) NOT NULL,
    audio_duration_seconds numeric(10,3) NOT NULL,
    audio_sample_rate_hz integer NOT NULL,
    audio_channels smallint NOT NULL,
    asr_engine text NOT NULL,
    asr_model text NOT NULL,
    asr_language text NOT NULL,
    transcript text NOT NULL,
    asr_confidence numeric(5,4),
    classification text NOT NULL,
    classification_confidence numeric(5,4),
    requires_review boolean NOT NULL,
    review_status text DEFAULT 'PENDING'::text NOT NULL,
    reason_transcript text,
    promised_due_text text,
    promised_due_at timestamp with time zone,
    received_at timestamp with time zone NOT NULL,
    reviewed_by uuid,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    response_analysis jsonb,
    reviewed_result jsonb,
    review_version integer DEFAULT 0 NOT NULL,
    CONSTRAINT task_responses_answer_index_check CHECK ((answer_index > 0)),
    CONSTRAINT task_responses_asr_confidence_check CHECK (((asr_confidence >= (0)::numeric) AND (asr_confidence <= (1)::numeric))),
    CONSTRAINT task_responses_audio_channels_check CHECK (((audio_channels >= 1) AND (audio_channels <= 8))),
    CONSTRAINT task_responses_audio_duration_seconds_check CHECK ((audio_duration_seconds > (0)::numeric)),
    CONSTRAINT task_responses_audio_sample_rate_hz_check CHECK ((audio_sample_rate_hz >= 8000)),
    CONSTRAINT task_responses_audio_sha256_check CHECK ((audio_sha256 ~ '^[a-f0-9]{64}$'::text)),
    CONSTRAINT task_responses_check CHECK (((classification <> 'UNCLEAR'::text) OR requires_review)),
    CONSTRAINT task_responses_check1 CHECK (((review_status <> ALL (ARRAY['CONFIRMED'::text, 'CORRECTED'::text])) OR ((reviewed_by IS NOT NULL) AND (reviewed_at IS NOT NULL)))),
    CONSTRAINT task_responses_classification_check CHECK ((classification = ANY (ARRAY['YES'::text, 'NO'::text, 'UNCLEAR'::text]))),
    CONSTRAINT task_responses_classification_confidence_check CHECK (((classification_confidence >= (0)::numeric) AND (classification_confidence <= (1)::numeric))),
    CONSTRAINT task_responses_response_analysis_check CHECK (((response_analysis IS NULL) OR (jsonb_typeof(response_analysis) = 'object'::text))),
    CONSTRAINT task_responses_review_status_check CHECK ((review_status = ANY (ARRAY['PENDING'::text, 'CONFIRMED'::text, 'CORRECTED'::text, 'REJECTED'::text, 'NOT_REQUIRED'::text]))),
    CONSTRAINT task_responses_review_version_check CHECK ((review_version >= 0)),
    CONSTRAINT task_responses_reviewed_result_check CHECK (((reviewed_result IS NULL) OR (jsonb_typeof(reviewed_result) = 'object'::text)))
);


--
-- Name: COLUMN task_responses.response_analysis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_responses.response_analysis IS 'Versioned rule analysis with verbatim evidence; human review required';


--
-- Name: COLUMN task_responses.reviewed_result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.task_responses.reviewed_result IS 'Human correction kept separately from original ASR and classifier evidence';


--
--



--
--



--
-- Name: tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tasks (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    instruction text NOT NULL,
    lifecycle text DEFAULT 'DRAFT'::text NOT NULL,
    execution_status text DEFAULT 'UNKNOWN'::text NOT NULL,
    primary_assignee_id uuid,
    due_text text,
    due_at timestamp with time zone,
    due_resolution text DEFAULT 'NOT_STATED'::text NOT NULL,
    source_spans jsonb NOT NULL,
    machine_confidence numeric(5,4),
    review_status text DEFAULT 'PENDING'::text NOT NULL,
    approved_by uuid,
    approved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tasks_check CHECK (((due_resolution <> 'RESOLVED'::text) OR (due_at IS NOT NULL))),
    CONSTRAINT tasks_check1 CHECK (((due_resolution <> 'UNRESOLVED_RELATIVE'::text) OR (length(btrim(due_text)) > 0))),
    CONSTRAINT tasks_check2 CHECK ((updated_at >= created_at)),
    CONSTRAINT tasks_due_resolution_check CHECK ((due_resolution = ANY (ARRAY['RESOLVED'::text, 'UNRESOLVED_RELATIVE'::text, 'NOT_STATED'::text]))),
    CONSTRAINT tasks_execution_status_check CHECK ((execution_status = ANY (ARRAY['PENDING'::text, 'CLAIMED_DONE'::text, 'CONFIRMED_DONE'::text, 'NOT_DONE'::text, 'BLOCKED'::text, 'UNKNOWN'::text]))),
    CONSTRAINT tasks_instruction_check CHECK ((length(btrim(instruction)) > 0)),
    CONSTRAINT tasks_lifecycle_check CHECK ((lifecycle = ANY (ARRAY['DRAFT'::text, 'APPROVED'::text, 'ACTIVE'::text, 'CLOSED'::text, 'CANCELLED'::text]))),
    CONSTRAINT tasks_machine_confidence_check CHECK (((machine_confidence >= (0)::numeric) AND (machine_confidence <= (1)::numeric))),
    CONSTRAINT tasks_review_status_check CHECK ((review_status = ANY (ARRAY['PENDING'::text, 'CONFIRMED'::text, 'CORRECTED'::text, 'REJECTED'::text, 'NOT_REQUIRED'::text]))),
    CONSTRAINT tasks_source_spans_check CHECK (((jsonb_typeof(source_spans) = 'array'::text) AND (jsonb_array_length(source_spans) > 0)))
);


--
--



--
--



--
-- Name: transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcripts (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    meeting_id uuid NOT NULL,
    engine text NOT NULL,
    model text NOT NULL,
    language text NOT NULL,
    content jsonb NOT NULL,
    immutable boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT transcripts_content_check CHECK ((jsonb_typeof(content) = 'object'::text))
);


--
--



--
--



--
-- Name: meeting_brief_attempts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_attempts ALTER COLUMN id SET DEFAULT nextval('public.meeting_brief_attempts_id_seq'::regclass);


--
-- Name: audit_events audit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_pkey PRIMARY KEY (id);


--
-- Name: call_attempts call_attempts_call_job_id_attempt_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_attempts
    ADD CONSTRAINT call_attempts_call_job_id_attempt_no_key UNIQUE (call_job_id, attempt_no);


--
-- Name: call_attempts call_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_attempts
    ADD CONSTRAINT call_attempts_pkey PRIMARY KEY (id);


--
-- Name: call_attempts call_attempts_provider_call_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_attempts
    ADD CONSTRAINT call_attempts_provider_call_id_key UNIQUE (provider_call_id);


--
-- Name: call_job_tasks call_job_tasks_call_job_id_sequence_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_job_tasks
    ADD CONSTRAINT call_job_tasks_call_job_id_sequence_no_key UNIQUE (call_job_id, sequence_no);


--
-- Name: call_job_tasks call_job_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_job_tasks
    ADD CONSTRAINT call_job_tasks_pkey PRIMARY KEY (call_job_id, task_id);


--
-- Name: call_jobs call_jobs_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_jobs
    ADD CONSTRAINT call_jobs_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: call_jobs call_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_jobs
    ADD CONSTRAINT call_jobs_pkey PRIMARY KEY (id);


--
-- Name: contact_points contact_points_organization_id_normalized_value_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_points
    ADD CONSTRAINT contact_points_organization_id_normalized_value_key UNIQUE (organization_id, normalized_value);


--
-- Name: contact_points contact_points_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_points
    ADD CONSTRAINT contact_points_pkey PRIMARY KEY (id);


--
-- Name: manual_call_sessions manual_call_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_call_sessions
    ADD CONSTRAINT manual_call_sessions_pkey PRIMARY KEY (id);


--
-- Name: meeting_brief_attempts meeting_brief_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_attempts
    ADD CONSTRAINT meeting_brief_attempts_pkey PRIMARY KEY (id);


--
-- Name: meeting_brief_chunks meeting_brief_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_chunks
    ADD CONSTRAINT meeting_brief_chunks_pkey PRIMARY KEY (job_id, chunk_no);


--
-- Name: meeting_brief_jobs meeting_brief_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_jobs
    ADD CONSTRAINT meeting_brief_jobs_pkey PRIMARY KEY (id);


--
-- Name: meeting_briefs meeting_briefs_job_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_briefs
    ADD CONSTRAINT meeting_briefs_job_id_key UNIQUE (job_id);


--
-- Name: meeting_briefs meeting_briefs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_briefs
    ADD CONSTRAINT meeting_briefs_pkey PRIMARY KEY (id);


--
-- Name: meeting_import_chunks meeting_import_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_chunks
    ADD CONSTRAINT meeting_import_chunks_pkey PRIMARY KEY (import_id, part_no);


--
-- Name: meeting_import_groups meeting_import_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_groups
    ADD CONSTRAINT meeting_import_groups_pkey PRIMARY KEY (id);


--
-- Name: meeting_import_metrics meeting_import_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_metrics
    ADD CONSTRAINT meeting_import_metrics_pkey PRIMARY KEY (id);


--
-- Name: meeting_imports meeting_imports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_imports
    ADD CONSTRAINT meeting_imports_pkey PRIMARY KEY (id);


--
-- Name: meeting_llm_chunks meeting_llm_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_llm_chunks
    ADD CONSTRAINT meeting_llm_chunks_pkey PRIMARY KEY (job_id, chunk_no);


--
-- Name: meeting_llm_jobs meeting_llm_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_llm_jobs
    ADD CONSTRAINT meeting_llm_jobs_pkey PRIMARY KEY (id);


--
-- Name: meeting_llm_jobs meeting_llm_jobs_transcript_id_method_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_llm_jobs
    ADD CONSTRAINT meeting_llm_jobs_transcript_id_method_key UNIQUE (transcript_id, method);


--
-- Name: meeting_task_drafts meeting_task_drafts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_task_drafts
    ADD CONSTRAINT meeting_task_drafts_pkey PRIMARY KEY (id);


--
-- Name: meetings meetings_organization_id_source_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_organization_id_source_sha256_key UNIQUE (organization_id, source_sha256);


--
-- Name: meetings meetings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_pkey PRIMARY KEY (id);


--
-- Name: operation_controls operation_controls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_controls
    ADD CONSTRAINT operation_controls_pkey PRIMARY KEY (kind, entity_id);


--
-- Name: organization_operation_state organization_operation_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_operation_state
    ADD CONSTRAINT organization_operation_state_pkey PRIMARY KEY (organization_id);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);


--
-- Name: people people_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_pkey PRIMARY KEY (id);


--
-- Name: response_worker_runs response_worker_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.response_worker_runs
    ADD CONSTRAINT response_worker_runs_pkey PRIMARY KEY (attempt_id);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: secretary_users secretary_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secretary_users
    ADD CONSTRAINT secretary_users_pkey PRIMARY KEY (id);


--
-- Name: secretary_users secretary_users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secretary_users
    ADD CONSTRAINT secretary_users_username_key UNIQUE (username);


--
-- Name: task_assignees task_assignees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_assignees
    ADD CONSTRAINT task_assignees_pkey PRIMARY KEY (task_id, person_id);


--
-- Name: task_comments task_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_comments
    ADD CONSTRAINT task_comments_pkey PRIMARY KEY (id);


--
-- Name: task_responses task_responses_call_attempt_id_task_id_answer_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_call_attempt_id_task_id_answer_index_key UNIQUE (call_attempt_id, task_id, answer_index);


--
-- Name: task_responses task_responses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_pkey PRIMARY KEY (id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: transcripts transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_pkey PRIMARY KEY (id);


--
-- Name: brief_jobs_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX brief_jobs_one_active ON public.meeting_brief_jobs USING btree (transcript_id, method) WHERE (status = ANY (ARRAY['QUEUED'::text, 'RUNNING'::text]));


--
-- Name: brief_jobs_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX brief_jobs_queue ON public.meeting_brief_jobs USING btree (status, created_at);


--
-- Name: ix_audit_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_entity ON public.audit_events USING btree (entity_type, entity_id, created_at);


--
-- Name: ix_call_jobs_dispatch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_call_jobs_dispatch ON public.call_jobs USING btree (status, scheduled_at) WHERE (status = ANY (ARRAY['QUEUED'::text, 'SCHEDULED'::text, 'RETRY_WAIT'::text]));


--
-- Name: ix_import_metrics; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_import_metrics ON public.meeting_import_metrics USING btree (import_id, started_at);


--
-- Name: ix_manual_call_sessions_org_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_manual_call_sessions_org_created ON public.manual_call_sessions USING btree (organization_id, created_at DESC);


--
-- Name: ix_manual_call_sessions_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_manual_call_sessions_task ON public.manual_call_sessions USING btree (organization_id, task_id, created_at DESC);


--
-- Name: ix_meeting_import_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_meeting_import_org ON public.meeting_imports USING btree (organization_id, created_at);


--
-- Name: ix_meeting_import_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_meeting_import_queue ON public.meeting_imports USING btree (status, created_at);


--
-- Name: ix_operation_controls_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operation_controls_org ON public.operation_controls USING btree (organization_id, updated_at DESC);


--
-- Name: ix_task_responses_review; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_responses_review ON public.task_responses USING btree (review_status, received_at) WHERE requires_review;


--
-- Name: ix_tasks_assignee_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_assignee_status ON public.tasks USING btree (primary_assignee_id, lifecycle, execution_status);


--
-- Name: ix_tasks_due_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_due_at ON public.tasks USING btree (due_at) WHERE (lifecycle = 'ACTIVE'::text);


--
-- Name: llm_jobs_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_jobs_queue ON public.meeting_llm_jobs USING btree (status, created_at);


--
-- Name: meeting_brief_attempts_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX meeting_brief_attempts_job ON public.meeting_brief_attempts USING btree (job_id, created_at, id);


--
-- Name: meeting_briefs_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX meeting_briefs_lookup ON public.meeting_briefs USING btree (organization_id, meeting_id, created_at DESC);


--
-- Name: meeting_draft_candidate_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX meeting_draft_candidate_key ON public.meeting_task_drafts USING btree (transcript_id, segment_index, candidate_key);


--
-- Name: meeting_drafts_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX meeting_drafts_org ON public.meeting_task_drafts USING btree (organization_id, meeting_id, status);


--
-- Name: meeting_part_order; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX meeting_part_order ON public.meeting_imports USING btree (group_id, group_position);


--
-- Name: task_comments_timeline; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX task_comments_timeline ON public.task_comments USING btree (organization_id, task_id, created_at, id);


--
-- Name: uq_call_jobs_one_running_per_person; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_call_jobs_one_running_per_person ON public.call_jobs USING btree (person_id) WHERE (status = 'RUNNING'::text);


--
-- Name: ux_single_platform_admin; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_single_platform_admin ON public.secretary_users USING btree (is_admin) WHERE is_admin;


--
-- Name: audit_events audit_events_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: call_attempts call_attempts_call_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_attempts
    ADD CONSTRAINT call_attempts_call_job_id_fkey FOREIGN KEY (call_job_id) REFERENCES public.call_jobs(id) ON DELETE CASCADE;


--
-- Name: call_attempts call_attempts_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_attempts
    ADD CONSTRAINT call_attempts_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: call_job_tasks call_job_tasks_call_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_job_tasks
    ADD CONSTRAINT call_job_tasks_call_job_id_fkey FOREIGN KEY (call_job_id) REFERENCES public.call_jobs(id) ON DELETE CASCADE;


--
-- Name: call_job_tasks call_job_tasks_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_job_tasks
    ADD CONSTRAINT call_job_tasks_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);


--
-- Name: call_jobs call_jobs_contact_point_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_jobs
    ADD CONSTRAINT call_jobs_contact_point_id_fkey FOREIGN KEY (contact_point_id) REFERENCES public.contact_points(id);


--
-- Name: call_jobs call_jobs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_jobs
    ADD CONSTRAINT call_jobs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: call_jobs call_jobs_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.call_jobs
    ADD CONSTRAINT call_jobs_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id);


--
-- Name: contact_points contact_points_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_points
    ADD CONSTRAINT contact_points_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: contact_points contact_points_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_points
    ADD CONSTRAINT contact_points_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id) ON DELETE CASCADE;


--
-- Name: manual_call_sessions manual_call_sessions_contact_point_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_call_sessions
    ADD CONSTRAINT manual_call_sessions_contact_point_id_fkey FOREIGN KEY (contact_point_id) REFERENCES public.contact_points(id);


--
-- Name: manual_call_sessions manual_call_sessions_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_call_sessions
    ADD CONSTRAINT manual_call_sessions_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: manual_call_sessions manual_call_sessions_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_call_sessions
    ADD CONSTRAINT manual_call_sessions_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id);


--
-- Name: manual_call_sessions manual_call_sessions_secretary_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_call_sessions
    ADD CONSTRAINT manual_call_sessions_secretary_id_fkey FOREIGN KEY (secretary_id) REFERENCES public.people(id);


--
-- Name: manual_call_sessions manual_call_sessions_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manual_call_sessions
    ADD CONSTRAINT manual_call_sessions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);


--
-- Name: meeting_brief_attempts meeting_brief_attempts_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_attempts
    ADD CONSTRAINT meeting_brief_attempts_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.meeting_brief_jobs(id) ON DELETE CASCADE;


--
-- Name: meeting_brief_chunks meeting_brief_chunks_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_chunks
    ADD CONSTRAINT meeting_brief_chunks_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.meeting_brief_jobs(id) ON DELETE CASCADE;


--
-- Name: meeting_brief_jobs meeting_brief_jobs_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_jobs
    ADD CONSTRAINT meeting_brief_jobs_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.people(id);


--
-- Name: meeting_brief_jobs meeting_brief_jobs_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_jobs
    ADD CONSTRAINT meeting_brief_jobs_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id);


--
-- Name: meeting_brief_jobs meeting_brief_jobs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_jobs
    ADD CONSTRAINT meeting_brief_jobs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: meeting_brief_jobs meeting_brief_jobs_transcript_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_brief_jobs
    ADD CONSTRAINT meeting_brief_jobs_transcript_id_fkey FOREIGN KEY (transcript_id) REFERENCES public.transcripts(id);


--
-- Name: meeting_briefs meeting_briefs_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_briefs
    ADD CONSTRAINT meeting_briefs_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.people(id);


--
-- Name: meeting_briefs meeting_briefs_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_briefs
    ADD CONSTRAINT meeting_briefs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.meeting_brief_jobs(id);


--
-- Name: meeting_briefs meeting_briefs_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_briefs
    ADD CONSTRAINT meeting_briefs_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id);


--
-- Name: meeting_briefs meeting_briefs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_briefs
    ADD CONSTRAINT meeting_briefs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: meeting_briefs meeting_briefs_transcript_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_briefs
    ADD CONSTRAINT meeting_briefs_transcript_id_fkey FOREIGN KEY (transcript_id) REFERENCES public.transcripts(id);


--
-- Name: meeting_import_chunks meeting_import_chunks_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_chunks
    ADD CONSTRAINT meeting_import_chunks_import_id_fkey FOREIGN KEY (import_id) REFERENCES public.meeting_imports(id) ON DELETE CASCADE;


--
-- Name: meeting_import_groups meeting_import_groups_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_groups
    ADD CONSTRAINT meeting_import_groups_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.people(id);


--
-- Name: meeting_import_groups meeting_import_groups_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_groups
    ADD CONSTRAINT meeting_import_groups_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: meeting_import_groups meeting_import_groups_primary_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_groups
    ADD CONSTRAINT meeting_import_groups_primary_import_id_fkey FOREIGN KEY (primary_import_id) REFERENCES public.meeting_imports(id);


--
-- Name: meeting_import_metrics meeting_import_metrics_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_import_metrics
    ADD CONSTRAINT meeting_import_metrics_import_id_fkey FOREIGN KEY (import_id) REFERENCES public.meeting_imports(id);


--
-- Name: meeting_imports meeting_imports_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_imports
    ADD CONSTRAINT meeting_imports_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.people(id);


--
-- Name: meeting_imports meeting_imports_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_imports
    ADD CONSTRAINT meeting_imports_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.meeting_import_groups(id);


--
-- Name: meeting_imports meeting_imports_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_imports
    ADD CONSTRAINT meeting_imports_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id);


--
-- Name: meeting_imports meeting_imports_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_imports
    ADD CONSTRAINT meeting_imports_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: meeting_imports meeting_imports_transcript_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_imports
    ADD CONSTRAINT meeting_imports_transcript_id_fkey FOREIGN KEY (transcript_id) REFERENCES public.transcripts(id);


--
-- Name: meeting_llm_chunks meeting_llm_chunks_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_llm_chunks
    ADD CONSTRAINT meeting_llm_chunks_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.meeting_llm_jobs(id);


--
-- Name: meeting_llm_jobs meeting_llm_jobs_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_llm_jobs
    ADD CONSTRAINT meeting_llm_jobs_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id);


--
-- Name: meeting_llm_jobs meeting_llm_jobs_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_llm_jobs
    ADD CONSTRAINT meeting_llm_jobs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: meeting_llm_jobs meeting_llm_jobs_transcript_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_llm_jobs
    ADD CONSTRAINT meeting_llm_jobs_transcript_id_fkey FOREIGN KEY (transcript_id) REFERENCES public.transcripts(id);


--
-- Name: meeting_task_drafts meeting_task_drafts_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_task_drafts
    ADD CONSTRAINT meeting_task_drafts_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id);


--
-- Name: meeting_task_drafts meeting_task_drafts_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_task_drafts
    ADD CONSTRAINT meeting_task_drafts_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: meeting_task_drafts meeting_task_drafts_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_task_drafts
    ADD CONSTRAINT meeting_task_drafts_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.people(id);


--
-- Name: meeting_task_drafts meeting_task_drafts_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_task_drafts
    ADD CONSTRAINT meeting_task_drafts_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);


--
-- Name: meeting_task_drafts meeting_task_drafts_transcript_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_task_drafts
    ADD CONSTRAINT meeting_task_drafts_transcript_id_fkey FOREIGN KEY (transcript_id) REFERENCES public.transcripts(id);


--
-- Name: meetings meetings_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT meetings_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: operation_controls operation_controls_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_controls
    ADD CONSTRAINT operation_controls_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE CASCADE;


--
-- Name: operation_controls operation_controls_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operation_controls
    ADD CONSTRAINT operation_controls_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.people(id);


--
-- Name: organization_operation_state organization_operation_state_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_operation_state
    ADD CONSTRAINT organization_operation_state_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE CASCADE;


--
-- Name: organization_operation_state organization_operation_state_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_operation_state
    ADD CONSTRAINT organization_operation_state_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.people(id);


--
-- Name: people people_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: response_worker_runs response_worker_runs_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.response_worker_runs
    ADD CONSTRAINT response_worker_runs_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.call_attempts(id);


--
-- Name: reviews reviews_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: reviews reviews_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES public.people(id);


--
-- Name: secretary_users secretary_users_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secretary_users
    ADD CONSTRAINT secretary_users_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id);


--
-- Name: task_assignees task_assignees_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_assignees
    ADD CONSTRAINT task_assignees_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id);


--
-- Name: task_assignees task_assignees_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_assignees
    ADD CONSTRAINT task_assignees_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON DELETE CASCADE;


--
-- Name: task_comments task_comments_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_comments
    ADD CONSTRAINT task_comments_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.people(id);


--
-- Name: task_comments task_comments_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_comments
    ADD CONSTRAINT task_comments_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: task_comments task_comments_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_comments
    ADD CONSTRAINT task_comments_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON DELETE CASCADE;


--
-- Name: task_responses task_responses_call_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_call_attempt_id_fkey FOREIGN KEY (call_attempt_id) REFERENCES public.call_attempts(id);


--
-- Name: task_responses task_responses_call_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_call_job_id_fkey FOREIGN KEY (call_job_id) REFERENCES public.call_jobs(id);


--
-- Name: task_responses task_responses_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: task_responses task_responses_person_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_person_id_fkey FOREIGN KEY (person_id) REFERENCES public.people(id);


--
-- Name: task_responses task_responses_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.people(id);


--
-- Name: task_responses task_responses_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_responses
    ADD CONSTRAINT task_responses_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id);


--
-- Name: tasks tasks_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.people(id);


--
-- Name: tasks tasks_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id);


--
-- Name: tasks tasks_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- Name: tasks tasks_primary_assignee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_primary_assignee_id_fkey FOREIGN KEY (primary_assignee_id) REFERENCES public.people(id);


--
-- Name: transcripts transcripts_meeting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_meeting_id_fkey FOREIGN KEY (meeting_id) REFERENCES public.meetings(id) ON DELETE CASCADE;


--
-- Name: transcripts transcripts_organization_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT transcripts_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES public.organizations(id);


--
-- PostgreSQL database dump complete
--


