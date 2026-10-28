from __future__ import annotations

import io
import uuid

import pytest
from docx import Document
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app import db as dbmod
from app.config import Settings
from app.main import create_app
from app.services.job_store import JobStore
from tests.fixtures import FIXTURE_JOBS

RESUME_LINES = [
    "Priya Sharma",
    "Backend Developer",
    "priya.sharma@example.com | +91 98765 43210 | github.com/priyasharma",
    "SUMMARY",
    "Backend developer with 3 years of experience building APIs.",
    "SKILLS",
    "Python, Django, FastAPI, PostgreSQL, Docker, Git, REST APIs, AWS",
    "EXPERIENCE",
    "Software Engineer at Foo Corp",
    "Jan 2021 - Present",
    "• Built REST APIs with Django and PostgreSQL",
    "EDUCATION",
    "B.Tech in Computer Science, ABC Institute of Technology, 2020",
    "PROJECTS",
    "Job Tracker",
    "• Flask app to track applications using SQLite",
    "CERTIFICATIONS",
    "• AWS Certified Cloud Practitioner",
]


def make_pdf(lines=RESUME_LINES) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(50, y, line.replace("•", "-"))
        y -= 18
    c.save()
    return buf.getvalue()


def make_docx(lines=RESUME_LINES) -> bytes:
    d = Document()
    for line in lines:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def resume_pdf() -> bytes:
    return make_pdf()


@pytest.fixture
def resume_docx() -> bytes:
    return make_docx()


@pytest.fixture
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    dbmod.init_db(eng)
    monkeypatch.setattr(dbmod, "engine", eng)
    monkeypatch.setattr(dbmod, "SessionLocal", dbmod.sessionmaker(bind=eng, autoflush=False, expire_on_commit=False))
    return eng


@pytest.fixture
def make_client(engine):
    def _make(jobs=FIXTURE_JOBS, **settings_kw):
        s = Settings(llm_provider="none", **settings_kw)
        app = create_app(s, JobStore(list(jobs)))

        def override():
            db = dbmod.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[dbmod.get_db] = override
        return TestClient(app, raise_server_exceptions=False)
    return _make


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def cid() -> str:
    return "client-" + uuid.uuid4().hex[:16]


def H(cid: str) -> dict:
    return {"X-Client-Id": cid}
