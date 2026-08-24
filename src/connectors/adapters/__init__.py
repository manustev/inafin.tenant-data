"""Live, HTTP-backed `SourceConnectorPort` adapters — one module per
`platform_ref.document_type.source_system` value.

None of these can fetch anything yet: no deployment in this workspace has
GSP/ASP, ICEGATE, DGFT, IRP or EWB-portal credentials configured, so every
adapter's `fetch()` raises `ConnectorNotConfiguredError` until `Settings`
carries a base URL and credential reference for its `source_system`
(`factory.py` is what reads those settings and decides whether an adapter is
configured at all). They exist now, fully typed against
`SourceConnectorPort`, so that wiring real credentials later is an additive
change to one module plus one `Settings` entry — never a change to
`factory.py`'s dispatch table or to any caller.
"""

from __future__ import annotations
