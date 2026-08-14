-- =============================================================================
-- Shared migration 027 — onboarding profile detail and the documents/data
-- requirements a customer must satisfy to complete onboarding.
--
-- Rebuilt from inafin-api's withdrawn proposal (migrations/platform/0003_
-- onboarding_profile_and_artifacts.sql) — see CLAUDE.md's platform migration
-- chain entry for the withdrawal history.
--
-- customer_document and data_requirement both key off platform_ref.document_
-- type (003_document_registry.sql) — the same Document Type Registry the
-- ingestion pipeline already uses, not a second onboarding-specific document
-- taxonomy. This is the "list to pick from during customer onboarding" the
-- registry was asked to also serve: one registry, two consumers.
-- =============================================================================

CREATE TABLE IF NOT EXISTS platform_ref.customer_profile (
    customer_id           uuid PRIMARY KEY
                              REFERENCES platform_ref.onboarding_customer (customer_id),
    primary_contact_name  text NOT NULL,
    primary_contact_email text NOT NULL,
    primary_contact_phone text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    created_by            text NOT NULL,
    modified_at           timestamptz NOT NULL DEFAULT now(),
    modified_by           text NOT NULL
);

-- A customer may hold more than one GSTIN (multi-state registration), so this
-- is not 1:1 with onboarding_customer the way customer_profile is.
CREATE TABLE IF NOT EXISTS platform_ref.gst_registration (
    gst_registration_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id          uuid NOT NULL
                             REFERENCES platform_ref.onboarding_customer (customer_id),
    gstin                text NOT NULL,
    state_code           text NOT NULL,
    registration_date    date,
    status               text NOT NULL DEFAULT 'ACTIVE'
                             CHECK (status IN ('ACTIVE', 'CANCELLED', 'SUSPENDED')),
    created_at           timestamptz NOT NULL DEFAULT now(),
    created_by           text NOT NULL,
    modified_at          timestamptz NOT NULL DEFAULT now(),
    modified_by          text NOT NULL,
    UNIQUE (customer_id, gstin)
);

CREATE TABLE IF NOT EXISTS platform_ref.customer_document (
    document_id   uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id   uuid NOT NULL
                      REFERENCES platform_ref.onboarding_customer (customer_id),
    doc_type_code text NOT NULL REFERENCES platform_ref.document_type (doc_type_code),
    object_uri    text,
    status        text NOT NULL DEFAULT 'RECEIVED'
                      CHECK (status IN ('RECEIVED', 'STORED', 'REJECTED', 'VERIFIED')),
    uploaded_at   timestamptz NOT NULL DEFAULT now(),
    uploaded_by   text NOT NULL,
    failure_reason text
);

CREATE INDEX IF NOT EXISTS customer_document_customer_idx
    ON platform_ref.customer_document (customer_id);

-- What a customer still owes before onboarding can complete. Distinct from
-- customer_document (an uploaded artefact) because a requirement can exist
-- and remain unsatisfied — the row is the ask, not the answer.
CREATE TABLE IF NOT EXISTS platform_ref.data_requirement (
    data_requirement_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
    customer_id          uuid NOT NULL
                             REFERENCES platform_ref.onboarding_customer (customer_id),
    doc_type_code        text NOT NULL REFERENCES platform_ref.document_type (doc_type_code),
    is_required          boolean NOT NULL DEFAULT true,
    satisfied_by_document_id uuid REFERENCES platform_ref.customer_document (document_id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (customer_id, doc_type_code)
);

REVOKE ALL ON platform_ref.customer_profile FROM PUBLIC;
REVOKE ALL ON platform_ref.gst_registration FROM PUBLIC;
REVOKE ALL ON platform_ref.customer_document FROM PUBLIC;
REVOKE ALL ON platform_ref.data_requirement FROM PUBLIC;

GRANT SELECT ON platform_ref.customer_profile, platform_ref.gst_registration,
    platform_ref.customer_document, platform_ref.data_requirement
    TO platform_ref_reader;
