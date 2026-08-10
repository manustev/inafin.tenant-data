"""FastAPI dependency functions — the seam between routes and the core layer.

Both dependencies read off `request.app.state`, set once by `src/api/app.py`'s
lifespan handler at startup. Nothing here constructs a pool or an auth
adapter itself: a static gate in `scripts/check_static.py` forbids building a
connection pool anywhere outside `src/core/pool.py`, and routes must not each
pick their own auth adapter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from src.bronze.service import BronzeIngestionService
from src.core.errors import AuthenticationError
from src.core.pool import TenantScopedPool
from src.core.tenant import TenantContext
from src.reader.entitlement_reader import EntitlementReader
from src.reader.silver_reader import SilverReader


def get_pool(request: Request) -> TenantScopedPool:
    return request.app.state.pool  # type: ignore[no-any-return]


def get_bronze_service(request: Request) -> BronzeIngestionService:
    return request.app.state.bronze_service  # type: ignore[no-any-return]


def get_silver_reader(request: Request) -> SilverReader:
    return request.app.state.silver_reader  # type: ignore[no-any-return]


def get_entitlement_reader(request: Request) -> EntitlementReader:
    return request.app.state.entitlement_reader  # type: ignore[no-any-return]


def get_tenant(request: Request) -> TenantContext:
    """Resolve the caller's tenant via `request.app.state.auth`.

    The ONLY place a `TenantContext` is constructed for an inbound request —
    route handlers depend on this, never on a slug read from the path, query
    string, or body. That is what keeps ARCHITECTURE.md 5.6's rule
    ("never a caller-supplied parameter") true here.
    """
    try:
        return request.app.state.auth.resolve(request)  # type: ignore[no-any-return]
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


PoolDep = Annotated[TenantScopedPool, Depends(get_pool)]
TenantDep = Annotated[TenantContext, Depends(get_tenant)]
BronzeServiceDep = Annotated[BronzeIngestionService, Depends(get_bronze_service)]
SilverReaderDep = Annotated[SilverReader, Depends(get_silver_reader)]
EntitlementReaderDep = Annotated[EntitlementReader, Depends(get_entitlement_reader)]
