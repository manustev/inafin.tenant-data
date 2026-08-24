"""`build_source_connector` — the one place a `source_system` string becomes
a concrete `SourceConnectorPort`.

Mirrors `src/dispatch/router.py`'s idiom exactly: which adapter answers a
given `source_system` is a lookup, not an if/elif chain scattered across
callers, and adding the eighth source system is a new module in `adapters/`
plus one dict entry here — never a change to anything that calls this
factory. `Settings.source_data_mode` is the other axis: it does not change
which `source_system` is resolved for a `doc_type_code` (that is
`registry_lookup.resolve_document_source`'s job), only whether the call
actually reaches a live adapter or the shared fixture stand-in.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.connectors.adapters.base_http import HttpSourceConnector, SourceCredential
from src.connectors.adapters.court_registry import CourtRegistryConnector
from src.connectors.adapters.dgft_portal import DgftPortalConnector
from src.connectors.adapters.ewb_portal_api import EwbPortalApiConnector
from src.connectors.adapters.gstn_api import GstnApiConnector
from src.connectors.adapters.gstn_portal import GstnPortalConnector
from src.connectors.adapters.icegate_api import IcegateApiConnector
from src.connectors.adapters.irp_api import IrpApiConnector
from src.connectors.base import SourceConnectorPort
from src.connectors.local_fixture import LocalFixtureConnector

if TYPE_CHECKING:
    from src.core.config import Settings

#: source_system -> live adapter class. Every value
#: `registry/document_types.csv` uses for a Category B row except
#: `INAFIN_CORPUS` (that source_system has no connector at all — those two
#: rows, B4.03/B4.04, are `inafin-gst-corpus`'s data, not pulled by this
#: repo; see `registry/README.md` §2 and `adapters/ewb_portal_api.py`'s
#: docstring).
_LIVE_CONNECTORS: dict[str, type[HttpSourceConnector]] = {
    GstnApiConnector.source_system: GstnApiConnector,
    GstnPortalConnector.source_system: GstnPortalConnector,
    IcegateApiConnector.source_system: IcegateApiConnector,
    DgftPortalConnector.source_system: DgftPortalConnector,
    IrpApiConnector.source_system: IrpApiConnector,
    EwbPortalApiConnector.source_system: EwbPortalApiConnector,
    CourtRegistryConnector.source_system: CourtRegistryConnector,
}


class NoConnectorForSourceSystemError(ValueError):
    """`source_system` is not `INAFIN_CORPUS` (deliberately unconnected, see
    `_LIVE_CONNECTORS`'s docstring) and matches no adapter in
    `_LIVE_CONNECTORS` either — almost certainly a new
    `source_system` value landed in the registry CSV without a matching
    adapter module being added here, the connector-layer equivalent of
    `dispatch/router.py`'s `NoDispatchMechanismError`."""


def build_source_connector(
    source_system: str, settings: Settings,
) -> SourceConnectorPort:
    """Local-fixture mode (`settings.source_data_mode == "local_fixture"`,
    the default — no live credentials exist in this workspace yet) always
    returns the one shared `LocalFixtureConnector` regardless of
    `source_system`, since fixture resolution only depends on `ref`/tenant,
    not on which live system a ref would otherwise be pulled from. Live mode
    looks up the adapter class and constructs it from
    `Settings.source_connector_base_urls`/`source_connector_credential_refs`,
    both keyed by `source_system` — an unconfigured entry naturally makes
    `HttpSourceConnector._configured()` false, which is what turns into
    `ConnectorNotConfiguredError` on the first `fetch()` call rather than
    here at construction time (constructing an unconfigured adapter is not
    itself an error — only trying to use it is).
    """
    if settings.source_data_mode == "local_fixture":
        return LocalFixtureConnector(fixture_root=Path(settings.source_fixture_root))

    try:
        adapter_cls = _LIVE_CONNECTORS[source_system]
    except KeyError:
        raise NoConnectorForSourceSystemError(
            f"no live connector registered for source_system={source_system!r}"
        ) from None

    base_url = settings.source_connector_base_urls.get(source_system, "")
    credential_ref = settings.source_connector_credential_refs.get(source_system)
    credential = SourceCredential(secret_ref=credential_ref) if credential_ref else None
    return adapter_cls(base_url=base_url, credential=credential)
