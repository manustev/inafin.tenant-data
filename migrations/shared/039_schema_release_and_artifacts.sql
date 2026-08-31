-- =============================================================================
-- Shared migration 039 — versioned schema releases and their published files.
--
-- THE FLOW THESE TWO TABLES SERVE (agreed 2026-08-24):
--
--   customer onboarded -> ERP section -> DOWNLOADS the schema for the document
--   types they must supply -> maps their ERP export to it -> uploads. On that
--   first upload, the release they downloaded is PINNED to their tenant (see
--   tenant migration 028), so a later platform release cannot silently change
--   the contract under a tenant who is mid-integration. inafin-api then serves
--   that tenant their pinned release, and a new release reaches them only when
--   somebody rolls it out.
--
-- WHY THE VERSION IS IN THE OBJECT KEY, NOT ONLY IN S3 VERSIONING. The bucket
-- has S3 versioning enabled, but that is a safety net against overwrite and
-- deletion — not the release mechanism. A staged rollout needs v1 and v2 to be
-- SIMULTANEOUSLY addressable, because two tenants will legitimately be on
-- different releases at the same time; S3 versioning gives one "current"
-- object plus history, which cannot express that. So `schema/v1/...` and
-- `schema/v2/...` are different keys, both live, and this table says which is
-- which. The S3 version id is recorded too, as the immutable pointer to the
-- exact bytes served.
--
-- WHY NOT COMPLIANCE-MODE OBJECT LOCK, WHICH EVERY TENANT BUCKET USES. Object
-- Lock exists here for tenant EVIDENCE — material that may be tendered in a
-- GST proceeding, which nobody may shorten or remove. Published schemas and
-- sample documents are neither evidence nor tenant data; they are platform
-- reference material that is expected to be superseded and occasionally
-- withdrawn. Locking them would make a bad sample permanent.
-- =============================================================================

CREATE TABLE platform_ref.schema_release (
    version text PRIMARY KEY CHECK (version ~ '^v[0-9]+$'),

    --   DRAFT       published to the store, not yet offered to anyone
    --   CURRENT     what a new tenant gets. At most one, enforced below.
    --   SUPERSEDED  still served to tenants pinned to it, never handed out new
    --
    -- SUPERSEDED is not "deleted". A tenant pinned to v1 keeps being served v1
    -- after v2 becomes CURRENT — that is the entire point of pinning, and it
    -- is why nothing here cascades.
    status text NOT NULL CHECK (status IN ('DRAFT', 'CURRENT', 'SUPERSEDED')),

    released_at timestamptz NOT NULL DEFAULT now(),
    notes text NOT NULL DEFAULT ''
);

-- Exactly one CURRENT release, enforced by the database rather than by the
-- publishing script. "Which schema does a brand-new tenant get" must have one
-- answer; two CURRENT rows would make it depend on row order.
CREATE UNIQUE INDEX schema_release_one_current_uq
    ON platform_ref.schema_release ((status)) WHERE status = 'CURRENT';

COMMENT ON TABLE platform_ref.schema_release IS
    'A published set of schema files and samples. Tenants pin to one of these '
    'at first upload (tenant 028) and are migrated forward deliberately.';

CREATE TABLE platform_ref.schema_artifact (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    release_version text NOT NULL
        REFERENCES platform_ref.schema_release (version),

    -- NULL for a release-level artifact that is not about one document type
    -- (a bundle README, an all-types manifest). Not every file is per-type.
    doc_type_code text REFERENCES platform_ref.document_type (doc_type_code),

    --   SCHEMA  the machine-readable field list a tenant maps their ERP to
    --   SAMPLE  an example document of that type
    kind text NOT NULL CHECK (kind IN ('SCHEMA', 'SAMPLE')),

    filename text NOT NULL,
    content_format text NOT NULL,

    object_bucket text NOT NULL,
    object_key text NOT NULL,

    -- The S3 version id of the exact bytes. Recorded so that "what did this
    -- tenant actually download" is answerable later even if the key is
    -- re-uploaded — the same reason artefact_ledger records content_hash
    -- rather than trusting the key.
    object_version_id text,

    sha256 bytea NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),

    CONSTRAINT schema_artifact_key_uq UNIQUE (release_version, object_key)
);

COMMENT ON TABLE platform_ref.schema_artifact IS
    'One published file in a release. The bytes live in the platform bucket; '
    'this is the manifest inafin-api reads to list and address them.';

CREATE INDEX schema_artifact_lookup_idx
    ON platform_ref.schema_artifact (release_version, doc_type_code, kind);

-- Same ambient-read case migration 037 makes for the catalogue itself: the
-- portal offers these downloads during onboarding, before any tenant role can
-- be assumed. SELECT only — publishing is this repo's job, not the API's.
GRANT SELECT ON
    platform_ref.schema_release,
    platform_ref.schema_artifact
    TO app_login;
