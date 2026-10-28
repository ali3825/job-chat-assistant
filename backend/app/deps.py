from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from .errors import AppError
from .services.job_store import JobStore
from .services.orchestrator import Orchestrator
from .services.sessions import CLIENT_ID_RE


def get_client_id(x_client_id: Annotated[str | None, Header()] = None) -> str:
    if not x_client_id or not CLIENT_ID_RE.match(x_client_id):
        raise AppError(400, "invalid_client_id", "Missing or invalid X-Client-Id header.",
                       "Reload the page so a new session ID is created.")
    return x_client_id


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_store(request: Request) -> JobStore:
    return request.app.state.store


ClientId = Annotated[str, Depends(get_client_id)]
