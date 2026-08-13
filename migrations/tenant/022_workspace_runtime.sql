-- Tenant migration 022 — application runtime facts for GST workspace, CA/HITL,
-- Data Hub and Command Center. All runtime records remain schema-per-tenant.

CREATE TABLE IF NOT EXISTS {{gold}}.workspace_assessment (
  assessment_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), entity_id text NOT NULL,
  overall_risk text NOT NULL CHECK (overall_risk IN ('Low','Medium','High','Critical')),
  assessment_json jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL DEFAULT current_user, UNIQUE(entity_id)
);
CREATE TABLE IF NOT EXISTS {{gold}}.workspace_rule_result (
  rule_result_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), entity_id text NOT NULL,
  rule_id text NOT NULL, name text NOT NULL, severity text NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  status text NOT NULL CHECK (status IN ('PASSED','FAILED','WARNING')), explanation text NOT NULL,
  inputs jsonb NOT NULL DEFAULT '{}', expected_result text NOT NULL DEFAULT '', actual_result text NOT NULL DEFAULT '',
  legal_basis jsonb, evidence_ids text[] NOT NULL DEFAULT '{}', duplicate_match jsonb,
  evaluated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(entity_id,rule_id)
);
CREATE TABLE IF NOT EXISTS {{gold}}.workspace_evidence (
  evidence_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), entity_id text NOT NULL,
  evidence_type text NOT NULL, source text NOT NULL, file_name text NOT NULL, object_uri text,
  uploaded_by text NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now(), related_fact_key text, related_rule_id text
);
CREATE TABLE IF NOT EXISTS {{gold}}.workspace_comment (
  comment_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), entity_id text NOT NULL, author text NOT NULL,
  body text NOT NULL, mentions text[] NOT NULL DEFAULT '{}', attachment_ids text[] NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.workspace_decision (
  decision_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), entity_id text NOT NULL,
  decision_type text NOT NULL CHECK (decision_type IN ('Accept','Accept with Observation','Override','Reject','Escalate','Request More Evidence','Partially Accept','Mark Not Applicable')),
  system_assessment text, user_value text, reason text, comment text, evidence_ids text[] NOT NULL DEFAULT '{}',
  created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.workspace_activity (
  activity_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), entity_id text NOT NULL, actor text NOT NULL,
  action text NOT NULL, detail text, occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.external_verification (
  check_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), entity_id text NOT NULL, check_type text NOT NULL,
  subject text NOT NULL, status text NOT NULL CHECK (status IN ('Verified','Failed','Pending','Info Available')),
  result_summary text NOT NULL, source text NOT NULL, verified_at timestamptz NOT NULL DEFAULT now(), reference_id text NOT NULL
);
CREATE TABLE IF NOT EXISTS {{gold}}.reconciliation_result (
  reconciliation_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), invoice_id text NOT NULL, source text NOT NULL,
  status text NOT NULL CHECK (status IN ('Fully Matched','Partially Matched','Mismatch','Not Found','Not Applicable')), detail text NOT NULL,
  calculated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(invoice_id,source)
);
CREATE TABLE IF NOT EXISTS {{gold}}.gst_document (
  document_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), name text NOT NULL,
  document_type text NOT NULL CHECK (document_type IN ('SEZ Certificate','GST Registration Certificate','DIN Document','Contract','Notification','Notice')),
  object_uri text, uploaded_by text NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL CHECK (status IN ('Uploaded','Extracted','Validated','Needs Attention','No Issues Found')),
  risk_level text NOT NULL CHECK (risk_level IN ('Low','Medium','High','Critical'))
);
CREATE TABLE IF NOT EXISTS {{gold}}.gst_document_fact (
  document_id uuid NOT NULL REFERENCES {{gold}}.gst_document(document_id) ON DELETE CASCADE, fact_key text NOT NULL,
  fact_value text NOT NULL, source text NOT NULL, extracted_at timestamptz NOT NULL DEFAULT now(), confidence numeric(5,2) NOT NULL CHECK(confidence BETWEEN 0 AND 100), verified_by text, is_static boolean NOT NULL, PRIMARY KEY(document_id,fact_key)
);
CREATE TABLE IF NOT EXISTS {{gold}}.gst_document_validation (
  validation_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), document_id uuid NOT NULL REFERENCES {{gold}}.gst_document(document_id) ON DELETE CASCADE,
  validation_key text NOT NULL, label text NOT NULL, status text NOT NULL CHECK(status IN ('pass','fail','warning')), detail text NOT NULL, UNIQUE(document_id,validation_key)
);
CREATE TABLE IF NOT EXISTS {{gold}}.gst_notice (
  notice_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), notice_number text NOT NULL UNIQUE,
  notice_type text NOT NULL CHECK(notice_type IN ('ADT-01','DRC','SCN','ASMT','Audit Query','Other')), gstin text NOT NULL, legal_entity text NOT NULL,
  audit_period text NOT NULL, issue_category text NOT NULL, amount_involved numeric(18,2) NOT NULL DEFAULT 0,
  received_date date NOT NULL, response_due_date date NOT NULL, risk text NOT NULL CHECK(risk IN ('Low','Medium','High','Critical')),
  status text NOT NULL CHECK(status IN ('New','Analysing','CA Review','Evidence Required','Client Response Pending','Draft Ready','Awaiting Approval','Submitted','Closed')),
  assigned_to text NOT NULL, blocking_reason text, ai_summary text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS {{gold}}.gst_notice_paragraph (
  paragraph_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), notice_id uuid NOT NULL REFERENCES {{gold}}.gst_notice(notice_id) ON DELETE CASCADE,
  paragraph_index integer NOT NULL, department_allegation text NOT NULL, ai_interpretation text NOT NULL, legal_basis jsonb NOT NULL DEFAULT '[]', customer_facts jsonb NOT NULL DEFAULT '{}', dynamic_findings text[] NOT NULL DEFAULT '{}', evidence_ids text[] NOT NULL DEFAULT '{}', external_verification_ids text[] NOT NULL DEFAULT '{}', ai_assessment text NOT NULL CHECK(ai_assessment IN ('Supported','Partially Supported','Not Supported','Insufficient Evidence','Not Applicable','Pending CA Review')), ai_confidence text NOT NULL CHECK(ai_confidence IN ('High','Medium','Low')), ca_decision_id uuid, UNIQUE(notice_id,paragraph_index)
);
CREATE TABLE IF NOT EXISTS {{gold}}.notice_response_draft (
  draft_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(), notice_id uuid NOT NULL REFERENCES {{gold}}.gst_notice(notice_id) ON DELETE CASCADE,
  version integer NOT NULL, status text NOT NULL CHECK(status IN ('AI Generated','AI Revised','CA Edited','Final Approved')), content jsonb NOT NULL DEFAULT '[]', created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(notice_id,version)
);

CREATE OR REPLACE VIEW {{gold}}.v1_command_center_data_quality AS
SELECT count(*) FILTER (WHERE status IN ('READY','ACCEPTED'))::numeric / NULLIF(count(*),0) * 100 AS percent,
       max(ready_at) AS last_ingestion_at FROM {{silver}}.v1_ingest_batch WHERE superseded_by IS NULL;
CREATE OR REPLACE VIEW {{gold}}.v1_command_center_notices AS
SELECT count(*) FILTER (WHERE status NOT IN ('Closed','Submitted')) AS pending_count, min(response_due_date) AS next_sla_at FROM {{gold}}.gst_notice;
CREATE OR REPLACE VIEW {{gold}}.v1_command_center_actions AS
SELECT notice_id::text AS id, notice_number AS title, issue_category AS description,
       CASE WHEN risk='Critical' THEN 'CRITICAL' WHEN status='Awaiting Approval' THEN 'PENDING_APPROVAL' ELSE 'REQUIRED' END AS severity,
       'ALERT'::text AS icon, 'Open notice'::text AS cta_label, 'ca-hitl'::text AS cta_view
FROM {{gold}}.gst_notice WHERE status NOT IN ('Closed','Submitted') AND (risk IN ('High','Critical') OR response_due_date <= current_date + 7);
