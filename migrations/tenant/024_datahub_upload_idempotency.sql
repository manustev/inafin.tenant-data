-- Tenant migration 024 — safe API retries for Data Hub uploads.
ALTER TABLE {{gold}}.datahub_upload_request
  ADD COLUMN IF NOT EXISTS idempotency_key text,
  ADD COLUMN IF NOT EXISTS detected_content_type text;

CREATE UNIQUE INDEX IF NOT EXISTS datahub_upload_request_idempotency_idx
  ON {{gold}}.datahub_upload_request (idempotency_key)
  WHERE idempotency_key IS NOT NULL;
