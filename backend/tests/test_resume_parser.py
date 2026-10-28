import io
import zipfile

import pytest

from app.services.resume_parser import ResumeError, extract_text, parse_resume_text, validate_upload
from tests.conftest import make_docx, make_pdf


def test_pdf_resume_parses(resume_pdf):
    text, _ = extract_text(resume_pdf, "pdf")
    p = parse_resume_text(text)
    assert p.name == "Priya Sharma"
    assert p.email == "priya.sharma@example.com"
    assert {"python", "django", "postgresql"} <= set(p.skills)
    assert p.years_experience == 3.0 and p.provenance["years_experience"] == "stated"
    assert p.education and p.education[0].year == 2020
    assert p.projects[0].name == "Job Tracker"


def test_docx_resume_parses(resume_docx):
    text, _ = extract_text(resume_docx, "docx")
    p = parse_resume_text(text)
    assert p.name == "Priya Sharma" and p.designation == "Backend Developer"
    assert "aws" in p.skills and p.certifications


def test_missing_fields_stay_empty_not_invented():
    text = "Some Person\nSKILLS\nPython and SQL are what I know well, and I enjoy building small tools.\nHobbies: chess, hiking, reading novels."
    p = parse_resume_text(text)
    assert p.email is None and p.phone is None
    assert p.years_experience is None and p.education == []
    assert {"python", "sql"} <= set(p.skills)
    assert "years of experience" in p.missing_fields() and "education" in p.missing_fields()


def test_experience_inferred_from_date_ranges():
    from datetime import date
    text = "Jane Doe\nEXPERIENCE\nDeveloper at X\nJan 2020 - Jan 2022\nEngineer at Y\nJan 2022 - Jan 2023\n"
    p = parse_resume_text(text, today=date(2026, 1, 1))
    assert p.years_experience == 3.0 and p.provenance["years_experience"] == "inferred"


def test_corrupted_pdf_rejected():
    with pytest.raises(ResumeError) as e:
        extract_text(b"%PDF-1.4 this is not really a pdf at all", "pdf")
    assert e.value.code in {"corrupt_file", "unreadable"}


def test_corrupted_docx_rejected():
    with pytest.raises(ResumeError) as e:
        extract_text(b"PK\x03\x04garbage-not-a-zip", "docx")
    assert e.value.code == "corrupt_file"


def test_empty_text_pdf_is_unreadable():
    with pytest.raises(ResumeError) as e:
        extract_text(make_pdf(["x"]), "pdf")
    assert e.value.code == "unreadable"


def test_validate_upload_rules():
    with pytest.raises(ResumeError) as e:
        validate_upload("cv.txt", b"hello", 1000)
    assert e.value.code == "unsupported_type"
    with pytest.raises(ResumeError) as e:
        validate_upload("cv.pdf", b"", 1000)
    assert e.value.code == "empty_file"
    with pytest.raises(ResumeError) as e:
        validate_upload("cv.pdf", b"%PDF-" + b"0" * 2000, 1000)
    assert e.value.code == "file_too_large"
    with pytest.raises(ResumeError) as e:
        validate_upload("cv.pdf", b"MZ not a pdf", 1000)  # wrong magic bytes
    assert e.value.code == "corrupt_file"


def test_zip_bomb_like_docx_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (60 * 1024 * 1024))
    with pytest.raises(ResumeError) as e:
        extract_text(buf.getvalue(), "docx")
    assert e.value.code == "corrupt_file"
