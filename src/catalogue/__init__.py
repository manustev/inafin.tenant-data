"""The published schema catalogue — what a tenant downloads before uploading.

`document_schema.py` derives, from the sources of truth this repo already
owns, the per-document-type field list the portal shows and a tenant exports
their ERP data to match. Nothing here invents a field: every row traces to a
`RegisterSpec`, a hand-written loader's field tuple, a `field_contract`, or an
`extraction_spec`.
"""

from src.catalogue.document_schema import (
    DocumentSchema,
    Provenance,
    RegistryFacts,
    SchemaField,
    SchemaKind,
    Scope,
    derive_document_schema,
    derive_document_schemas,
)

__all__ = [
    "DocumentSchema",
    "Provenance",
    "RegistryFacts",
    "SchemaField",
    "SchemaKind",
    "Scope",
    "derive_document_schema",
    "derive_document_schemas",
]
