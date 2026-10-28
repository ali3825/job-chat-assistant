from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import get_store
from ..errors import AppError
from ..schemas import Job
from ..services.job_store import JobFilters, JobStore

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", summary="Search/filter the job dataset")
def list_jobs(
    q: str | None = Query(None, max_length=100, description="Role/keyword query"),
    skill: list[str] = Query(default=[], max_length=10),
    remote: str | None = Query(None, pattern="^(remote|hybrid|onsite)$"),
    location: str | None = Query(None, max_length=80),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: JobStore = Depends(get_store),
):
    f = JobFilters(query=q, skills=[s.lower() for s in skill], remote=remote, locations=[location] if location else [])
    results = store.search(f)
    return {"total": len(results), "offset": offset, "limit": limit,
            "jobs": [j for j, _ in results[offset: offset + limit]]}


@router.get("/{job_id}", response_model=Job, summary="Get one job by stable ID")
def get_job(job_id: str, store: JobStore = Depends(get_store)):
    job = store.get(job_id)
    if job is None:
        raise AppError(404, "job_not_found", f"No job with ID “{job_id}”.", "Check the job ID or search again.")
    return job
