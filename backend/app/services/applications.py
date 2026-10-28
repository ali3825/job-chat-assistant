"""Application tracking. Records an application *in this app*; it never submits to an employer.

Statuses:
  external_application_required - saved here; the user must apply on the employer's page
  applied_by_user               - the user told us they completed the external application
Duplicates are prevented twice: an existence check, and a UNIQUE(client_id, job_id) constraint
that also holds under concurrent requests.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import STATUS_APPLIED_BY_USER, STATUS_EXTERNAL_REQUIRED, Application, utcnow
from ..schemas import Job
from .job_store import JobStore


@dataclass
class ApplyResult:
    application: Application
    created: bool  # False -> an application already existed (duplicate prevented)


def _db_error() -> AppError:
    return AppError(503, "database_error", "I couldn't reach the application database.", "Please try again in a moment.")


def apply_to_job(db: Session, store: JobStore, client_id: str, job_id: str) -> ApplyResult:
    job = store.get(job_id)
    if job is None:
        raise AppError(404, "job_not_found", f"No job with ID “{job_id}” exists in the dataset.",
                       "Ask me to show jobs again and pick one from the list.")
    try:
        existing = db.scalar(select(Application).where(Application.client_id == client_id, Application.job_id == job_id))
        if existing:
            return ApplyResult(existing, created=False)
        app = Application(
            client_id=client_id, job_id=job.job_id, job_title=job.title, company=job.company,
            source_url=job.source_url, application_url=job.application_url, status=STATUS_EXTERNAL_REQUIRED,
        )
        db.add(app)
        try:
            db.commit()
        except IntegrityError:  # lost a race with a concurrent identical request
            db.rollback()
            existing = db.scalar(select(Application).where(Application.client_id == client_id, Application.job_id == job_id))
            if existing:
                return ApplyResult(existing, created=False)
            raise
        return ApplyResult(app, created=True)
    except SQLAlchemyError:
        db.rollback()
        raise _db_error()


def list_applications(db: Session, client_id: str) -> list[Application]:
    try:
        return list(db.scalars(select(Application).where(Application.client_id == client_id).order_by(Application.applied_at.desc(), Application.id.desc())))
    except SQLAlchemyError:
        raise _db_error()


def count_applications(db: Session, client_id: str) -> int:
    try:
        return db.scalar(select(func.count()).select_from(Application).where(Application.client_id == client_id)) or 0
    except SQLAlchemyError:
        raise _db_error()


def get_application(db: Session, client_id: str, application_id: int) -> Application:
    try:
        app = db.scalar(select(Application).where(Application.id == application_id, Application.client_id == client_id))
    except SQLAlchemyError:
        raise _db_error()
    if app is None:
        raise AppError(404, "application_not_found", f"No application #{application_id} found.",
                       "Ask me to show your applied jobs to see valid ones.")
    return app


def get_application_for_job(db: Session, client_id: str, job_id: str) -> Application | None:
    try:
        return db.scalar(select(Application).where(Application.client_id == client_id, Application.job_id == job_id))
    except SQLAlchemyError:
        raise _db_error()


def mark_applied(db: Session, client_id: str, job_id: str) -> Application:
    app = get_application_for_job(db, client_id, job_id)
    if app is None:
        raise AppError(404, "application_not_found", "You haven't saved an application for that job yet.",
                       "Say “apply to job N” first.")
    try:
        app.status, app.updated_at = STATUS_APPLIED_BY_USER, utcnow()
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise _db_error()
    return app


def applied_status_map(db: Session, client_id: str) -> dict[str, str]:
    return {a.job_id: a.status for a in list_applications(db, client_id)}
