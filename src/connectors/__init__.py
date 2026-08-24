"""Source connectors — pull Category B artefacts from wherever they live.

OVERVIEW. Category B documents (GST portal filings, GSTN API returns,
ICEGATE/DGFT records) do not arrive as client uploads the way Category A
does — the platform has to go get them, from one of several external
systems. `SourceConnectorPort` (`base.py`) is the one contract every one of
those systems is accessed through; `factory.py` is the only place that picks
which adapter answers a given call. Nothing downstream of the factory ever
imports a concrete connector class or a vendor SDK directly — the same
"Protocol, not a library import" shape this repo already uses for
`VirusScanPort` (`src/bronze/scan.py`) and `ObjectStorePort`
(`src/provisioning/objectstore.py`), applied to a genuinely new boundary
rather than a new instance of an old one.

WHY THIS IS SEPARATE FROM `src/dispatch/`. `src/dispatch/router.py` decides
how bytes ALREADY IN BRONZE get promoted to Silver. This package decides how
bytes GET INTO Bronze in the first place for a document nothing uploads —
it sits one stage earlier, upstream of `BronzeIngestionService.receive`. A
connector's `fetch()` returns bytes; what happens to those bytes after that
(intake gate, dedup, Silver promotion) is unchanged and out of scope here.

LOCAL FIXTURE MODE. No source system's real credentials exist in this
workspace yet (`Settings.source_data_mode` defaults to `"local_fixture"`).
`LocalFixtureConnector` answers `fetch()` from a folder tree structured
tenant/ref/filename instead of an HTTP call, so the pipeline can be built and
tested end-to-end today. Every live adapter in `adapters/` already
implements the same `SourceConnectorPort` its fixture stand-in does — turning
a source system on for real is a credentials + `_do_fetch` change in that one
adapter, never a change to any caller.
"""

from __future__ import annotations
