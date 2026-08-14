-- Tenant migration 023 — Data Hub upload requests received by the API.
-- Object bytes are written only by the object-storage ingestion worker; the API
-- records metadata and leaves object_uri NULL until that worker completes.
-- Rebuilt from inafin-api's withdrawn proposal — see CLAUDE.md's platform
-- migration chain entry. Unchanged from the original draft.
CREATE TABLE IF NOT EXISTS {{gold}}.datahub_upload_request (
  upload_request_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
  document_type_id text NOT NULL,
  file_name text NOT NULL,
  file_size bigint NOT NULL CHECK (file_size >= 0),
  object_uri text,
  status text NOT NULL DEFAULT 'RECEIVED' CHECK (status IN ('RECEIVED','STORED','REJECTED','INGESTED')),
  requested_by text NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  failure_reason text
);
CREATE INDEX IF NOT EXISTS datahub_upload_request_requested_idx ON {{gold}}.datahub_upload_request (requested_at DESC);
