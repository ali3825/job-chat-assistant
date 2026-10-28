from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import ClientId, get_store
from ..schemas import ApplicationCreate, ApplicationOut
from ..services import applications as svc
from ..services.job_store import JobStore

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.post("", summary="Record an application (saved locally; not sent to the employer)")
def create_application(body: ApplicationCreate, client_id: ClientId, db: Session = Depends(get_db),
                       store: JobStore = Depends(get_store)):
    from fastapi.responses import JSONResponse
    from fastapi.encoders import jsonable_encoder

    res = svc.apply_to_job(db, store, client_id, body.job_id)
    out = jsonable_encoder(ApplicationOut.model_validate(res.application))
    return JSONResponse(status_code=201 if res.created else 200, content={"created": res.created, "application": out})


@router.get("", response_model=list[ApplicationOut])
def list_applications(client_id: ClientId, db: Session = Depends(get_db)):
    return svc.list_applications(db, client_id)


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(application_id: int, client_id: ClientId, db: Session = Depends(get_db)):
    return svc.get_application(db, client_id, application_id)
