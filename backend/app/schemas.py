"""Pydantic schemas: job records, candidate profile, match results, API payloads."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RemoteStatus = Literal["remote", "hybrid", "onsite", "unknown"]


class Job(BaseModel):
    """One job listing as stored in data/jobs.json."""

    model_config = ConfigDict(extra="ignore")

    job_id: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str = "Not specified"
    experience_required: str = "Not specified"
    min_experience_years: float | None = None
    max_experience_years: float | None = None
    experience_level: str | None = None  # entry | mid | senior | None
    skills: list[str] = Field(default_factory=list)
    description: str = ""
    source_url: str = Field(min_length=8)
    employment_type: str | None = None
    remote_status: RemoteStatus = "unknown"
    posted_date: str | None = None
    application_url: str | None = None
    salary: str | None = None
    source_name: str = "unknown"
    scraped_at: str | None = None
    last_verified_at: str | None = None
    listing_status: str = "listed"  # "listed" = shown by the source at scraped_at; may have closed since


class EducationItem(BaseModel):
    degree: str
    details: str | None = None
    year: int | None = None


class ProjectItem(BaseModel):
    name: str
    description: str | None = None


class Profile(BaseModel):
    """Structured candidate profile. ``provenance`` records, per field, whether the
    value was ``stated`` in the resume, ``inferred`` by a heuristic, or ``user`` (edited)."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    links: list[str] = Field(default_factory=list)
    designation: str | None = None
    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None
    education: list[EducationItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    work_arrangement: Literal["remote", "hybrid", "onsite"] | None = None
    provenance: dict[str, str] = Field(default_factory=dict)
    confirmed: bool = False

    @property
    def education_level(self) -> int | None:
        """0 = none stated, 1 bachelor/diploma, 2 master, 3 doctorate; None if no education parsed."""
        if not self.education:
            return None
        level = 0
        for e in self.education:
            d = e.degree.lower()
            if any(k in d for k in ("ph.d", "phd", "doctor")):
                level = max(level, 3)
            elif any(k in d for k in ("master", "m.tech", "mtech", "m.sc", "msc", "mca", "mba", "m.e", "m.s")):
                level = max(level, 2)
            else:
                level = max(level, 1)
        return level

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.name: missing.append("name")
        if not (self.email or self.phone): missing.append("contact details")
        if not self.designation: missing.append("designation")
        if not self.skills: missing.append("skills")
        if self.years_experience is None: missing.append("years of experience")
        if not self.education: missing.append("education")
        return missing


class ProfileUpdate(BaseModel):
    """Fields the user may correct. Omitted fields are left unchanged."""

    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    designation: str | None = Field(default=None, max_length=120)
    skills: list[str] | None = Field(default=None, max_length=80)
    years_experience: float | None = Field(default=None, ge=0, le=60)
    preferred_locations: list[str] | None = Field(default=None, max_length=10)
    work_arrangement: Literal["remote", "hybrid", "onsite"] | None = None
    confirmed: bool | None = None


class ComponentScore(BaseModel):
    name: str
    weight: int
    score: float | None  # 0..1, None when it could not be evaluated
    note: str


class MatchResult(BaseModel):
    score: int = Field(ge=0, le=100)
    confidence: Literal["high", "medium", "low"]
    matched_skills: list[str]
    partial_skills: list[str]
    missing_skills: list[str]
    uncertainties: list[str]
    explanation: str
    components: list[ComponentScore]


class JobCard(BaseModel):
    """A job plus (optionally) its match for the current candidate, as sent to the UI."""

    rank: int
    job: Job
    match: MatchResult | None = None
    applied: bool = False
    application_status: str | None = None


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: str
    job_title: str
    company: str
    source_url: str
    application_url: str | None
    status: str
    applied_at: datetime
    updated_at: datetime


class ApplicationCreate(BaseModel):
    job_id: str = Field(min_length=3, max_length=64)


class PendingAction(BaseModel):
    type: Literal["confirm_apply"]
    job_id: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    reply: str
    mode: Literal["fallback", "llm"] = "fallback"
    profile: Profile | None = None
    jobs: list[JobCard] | None = None
    applications: list[ApplicationOut] | None = None
    pending_action: PendingAction | None = None
    suggestions: list[str] = Field(default_factory=list)
    filters: dict | None = None
    error: dict | None = None
