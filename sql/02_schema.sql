-- Step 2 of 2: tables. Run after 01_create_database.sql, in a Query Tool
-- connected to the "bblg_livekit_intake" database as doadmin.
-- Creates every table the agent writes to (services/database.py). Safe to run
-- again: everything is IF NOT EXISTS.
--
-- The agent also creates these on its first call, and adds a column when a
-- field is added to services/analysis_fields.py; this file is for setting up
-- the database ahead of the first call. Regenerate it after adding fields:
--   python -c "from services import database as d; print(d.SCHEMA); [print(d._agent_table_ddl(d.AGENT_TABLES[c], f)) for c, f in d.CASE_FIELDS.items()]"

-- The tables must belong to the agent's login: on startup it runs
-- CREATE ... IF NOT EXISTS, which only the owner may do.
SET ROLE agent_intake_staging;

BEGIN;

CREATE TABLE IF NOT EXISTS user_data (
    id                     BIGSERIAL PRIMARY KEY,
    uuid                   TEXT NOT NULL UNIQUE,
    session_id             TEXT NOT NULL UNIQUE,
    call_id                TEXT,
    first_name             TEXT,
    last_name              TEXT,
    full_name              TEXT,
    email                  TEXT,
    phone_number           TEXT,
    call_number            TEXT,
    current_agent          TEXT,
    call_status            TEXT,
    call_started_at        TIMESTAMPTZ,
    call_ended_at          TIMESTAMPTZ,
    duration_ms            INTEGER,
    total_input_tokens     INTEGER NOT NULL DEFAULT 0,
    total_output_tokens    INTEGER NOT NULL DEFAULT 0,
    -- Not tracked by this build; per-model usage is in intake_calls.record.
    total_cost             NUMERIC(10, 6),
    llm_call_count         INTEGER,
    user_preferred_contact TEXT,
    user_address           TEXT,
    user_dob               TEXT,
    call_summary           TEXT,
    is_test_call           BOOLEAN NOT NULL DEFAULT FALSE,
    language               TEXT NOT NULL DEFAULT 'en',
    priority_level         TEXT,
    matched_keywords       JSONB,
    priority_reasoning     TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_deleted             BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_user_data_call_number ON user_data (call_number);
CREATE INDEX IF NOT EXISTS idx_user_data_call_started_at ON user_data (call_started_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_data_current_agent ON user_data (current_agent);
CREATE INDEX IF NOT EXISTS idx_user_data_priority_level ON user_data (priority_level);
CREATE INDEX IF NOT EXISTS idx_user_data_is_test_call ON user_data (is_test_call);
CREATE INDEX IF NOT EXISTS idx_user_data_phone_number ON user_data (phone_number);

CREATE TABLE IF NOT EXISTS session_tracking (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id       TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    started_at       TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    transfer_reason  TEXT,
    agent_data_table TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_tracking_session ON session_tracking (session_id);
CREATE INDEX IF NOT EXISTS idx_session_tracking_user_session ON session_tracking (user_id, session_id);
CREATE INDEX IF NOT EXISTS idx_session_tracking_agent ON session_tracking (agent_name);

CREATE TABLE IF NOT EXISTS error_events (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT,
    error_type      TEXT,
    error_message   TEXT,
    recoverable     BOOLEAN,
    room_id         TEXT,
    room_name       TEXT,
    session_id      TEXT,
    twilio_call_sid TEXT,
    extra           JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_error_events_source ON error_events (source);
CREATE INDEX IF NOT EXISTS idx_error_events_session ON error_events (session_id);
CREATE INDEX IF NOT EXISTS idx_error_events_created ON error_events (created_at);
CREATE INDEX IF NOT EXISTS idx_error_events_type ON error_events (error_type);

CREATE TABLE IF NOT EXISTS intake_calls (
    call_id            TEXT PRIMARY KEY,
    session_id         TEXT,
    room_name          TEXT NOT NULL,
    started_at         TIMESTAMPTZ NOT NULL,
    ended_at           TIMESTAMPTZ,
    duration_ms        INTEGER,
    case_type          TEXT,
    from_number        TEXT,
    caller_first_name  TEXT,
    caller_last_name   TEXT,
    caller_phone       TEXT,
    caller_email       TEXT,
    priority_level     TEXT,
    is_test_call       BOOLEAN NOT NULL DEFAULT FALSE,
    call_successful    BOOLEAN,
    call_summary       TEXT,
    recording_location TEXT,
    record             JSONB NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intake_calls_started_at_idx ON intake_calls (started_at DESC);
CREATE INDEX IF NOT EXISTS intake_calls_priority_idx ON intake_calls (priority_level, started_at DESC);
CREATE INDEX IF NOT EXISTS intake_calls_session_idx ON intake_calls (session_id);

CREATE TABLE IF NOT EXISTS accident_data (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    other_party_name TEXT,
    best_contact_time TEXT,
    referral_source TEXT,
    client_goal TEXT,
    accident_date TEXT,
    accident_location TEXT,
    incident_city TEXT,
    accident_description TEXT,
    accident_injuries TEXT,
    accident_treatment BOOLEAN,
    life_impact TEXT,
    accident_missed_work BOOLEAN,
    accident_passengers BOOLEAN,
    witnesses BOOLEAN,
    police_report BOOLEAN,
    other_party_insured BOOLEAN,
    insurance_claim_opened BOOLEAN,
    liability_status TEXT,
    has_documents BOOLEAN,
    written_evidence TEXT,
    urgency TEXT,
    has_other_attorney BOOLEAN,
    accident_time TEXT,
    accident_driver BOOLEAN,
    accident_passengers_count NUMERIC,
    police_report_number TEXT,
    accident_emotional_impact TEXT,
    accident_treatment_location TEXT,
    pre_existing_conditions TEXT,
    vehicle_type TEXT,
    vehicle_damage TEXT,
    insurance_claim_number TEXT,
    um_coverage BOOLEAN,
    financial_hardship TEXT,
    existing_client BOOLEAN,
    caller_is_affected_person BOOLEAN,
    affected_person_name_and_relation TEXT,
    considering_changing_attorney BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_accident_data_user_session ON accident_data (user_id, session_id);

CREATE TABLE IF NOT EXISTS employment_data (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    other_party_name TEXT,
    employment_best_contact_time TEXT,
    employment_position_title TEXT,
    employment_current_status TEXT,
    employment_start_date TEXT,
    employment_end_date TEXT,
    employment_supervisor_names TEXT,
    incident_city TEXT,
    employment_issue_description TEXT,
    employment_wrongful_termination BOOLEAN,
    employment_harassment_or_discrimination BOOLEAN,
    employment_wage_hour_violations BOOLEAN,
    employment_retaliation BOOLEAN,
    employment_fmla_issues BOOLEAN,
    employment_reported_internally BOOLEAN,
    employment_documentation_available BOOLEAN,
    employment_effects_financial TEXT,
    employment_effects_emotional TEXT,
    client_goal TEXT,
    urgency TEXT,
    has_other_attorney BOOLEAN,
    referral_source TEXT,
    employer_location TEXT,
    employer_size TEXT,
    employment_timeline TEXT,
    employment_reported_to TEXT,
    employment_effects_professional TEXT,
    employment_disability_or_pregnancy_issue TEXT,
    employment_illegal_act_refusal BOOLEAN,
    employment_safety_violations TEXT,
    existing_client BOOLEAN,
    caller_is_affected_person BOOLEAN,
    affected_person_name_and_relation TEXT,
    considering_changing_attorney BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_employment_data_user_session ON employment_data (user_id, session_id);

CREATE TABLE IF NOT EXISTS premises_liability_data (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    other_party_name TEXT,
    premises_best_contact_time TEXT,
    premises_incident_date TEXT,
    premises_incident_location TEXT,
    incident_city TEXT,
    premises_incident_description TEXT,
    premises_hazard_condition TEXT,
    premises_incident_reported BOOLEAN,
    premises_witnesses TEXT,
    premises_photos_available BOOLEAN,
    premises_physical_injuries TEXT,
    premises_medical_treatment BOOLEAN,
    premises_missed_work BOOLEAN,
    premises_financial_hardship TEXT,
    premises_owner_responsibility BOOLEAN,
    premises_property_owner_insurance BOOLEAN,
    client_goal TEXT,
    has_other_attorney BOOLEAN,
    referral_source TEXT,
    caller_is_injured BOOLEAN,
    premises_incident_time TEXT,
    premises_report_number TEXT,
    premises_footwear TEXT,
    premises_emotional_impact TEXT,
    premises_future_medical TEXT,
    pre_existing_conditions TEXT,
    premises_caller_insurance TEXT,
    existing_client BOOLEAN,
    caller_is_affected_person BOOLEAN,
    affected_person_name_and_relation TEXT,
    considering_changing_attorney BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_premises_liability_data_user_session ON premises_liability_data (user_id, session_id);

CREATE TABLE IF NOT EXISTS medical_malpractice_data (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    other_party_name TEXT,
    mm_caller_is_patient BOOLEAN,
    mm_patient_name TEXT,
    mm_patient_relation TEXT,
    mm_incident_date TEXT,
    mm_incident_location TEXT,
    incident_city TEXT,
    mm_incident_description TEXT,
    mm_injuries TEXT,
    mm_symptom_onset TEXT,
    mm_additional_treatment BOOLEAN,
    mm_additional_hospitalization BOOLEAN,
    mm_complaint_filed BOOLEAN,
    mm_records_available BOOLEAN,
    mm_witnesses TEXT,
    mm_impact_on_life TEXT,
    client_goal TEXT,
    has_other_attorney BOOLEAN,
    mm_contact_consent BOOLEAN,
    referral_source TEXT,
    mm_staff_names TEXT,
    mm_additional_notes TEXT,
    existing_client BOOLEAN,
    caller_is_affected_person BOOLEAN,
    affected_person_name_and_relation TEXT,
    considering_changing_attorney BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_medical_malpractice_data_user_session ON medical_malpractice_data (user_id, session_id);

CREATE TABLE IF NOT EXISTS sexual_harassment_data (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES user_data (id) ON DELETE CASCADE,
    session_id TEXT NOT NULL UNIQUE,
    sh_caller_is_affected BOOLEAN,
    sh_affected_person_name TEXT,
    sh_affected_person_relation TEXT,
    other_party_name TEXT,
    sh_issue_type TEXT,
    sh_nature_of_incidents TEXT,
    sh_incident_dates TEXT,
    sh_incident_location TEXT,
    incident_city TEXT,
    sh_witnesses TEXT,
    sh_reported_to_hr BOOLEAN,
    sh_filed_with_agency BOOLEAN,
    sh_evidence_details TEXT,
    sh_missed_work BOOLEAN,
    sh_retaliation BOOLEAN,
    client_goal TEXT,
    has_other_attorney BOOLEAN,
    sh_additional_notes TEXT,
    sh_people_involved TEXT,
    sh_hr_response TEXT,
    sh_lost_wages TEXT,
    sh_career_impact TEXT,
    existing_client BOOLEAN,
    caller_is_affected_person BOOLEAN,
    affected_person_name_and_relation TEXT,
    considering_changing_attorney BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sexual_harassment_data_user_session ON sexual_harassment_data (user_id, session_id);

COMMIT;

RESET ROLE;
