"""SYNTHETIC test fixtures. These are NOT real job listings and must never be used as the
submission dataset (data/jobs.json). source_name is "TEST_FIXTURE" so the app can flag it."""
from __future__ import annotations

from app.schemas import Job


def _job(i, title, company, location, remote, skills, min_y, level=None, desc="", emp="full_time"):
    return Job(
        job_id=f"j_test{i:04d}", title=title, company=company, location=location,
        experience_required=(f"{min_y:g}+ years" if min_y is not None else "Not specified"),
        min_experience_years=min_y, experience_level=level, skills=skills,
        description=desc or f"{title} at {company}. Skills: {', '.join(skills)}.",
        source_url=f"https://example.test/jobs/{i}", application_url=f"https://example.test/apply/{i}",
        employment_type=emp, remote_status=remote, source_name="TEST_FIXTURE", posted_date="2026-10-01",
        scraped_at="2026-10-20T00:00:00Z", last_verified_at="2026-10-20T00:00:00Z",
    )


FIXTURE_JOBS = [
    _job(1, "Python Backend Developer", "Acme Test", "Bangalore, India", "onsite", ["python", "django", "postgresql", "docker"], 2),
    _job(2, "Senior Python Engineer", "Beta Test", "Remote", "remote", ["python", "fastapi", "aws", "kubernetes"], 5),
    _job(3, "React Frontend Developer", "Gamma Test", "Remote", "remote", ["react", "javascript", "css", "html"], 1, "entry"),
    _job(4, "Junior Full Stack Developer", "Delta Test", "Pune, India", "hybrid", ["react", "node.js", "mongodb", "javascript"], 0, "entry"),
    _job(5, "Data Analyst", "Epsilon Test", "Mumbai, India", "onsite", ["sql", "excel", "tableau", "python"], 2),
    _job(6, "Machine Learning Engineer", "Zeta Test", "Remote", "remote", ["python", "pytorch", "machine learning", "docker"], 3),
    _job(7, "DevOps Engineer", "Eta Test", "Hyderabad, India", "onsite", ["aws", "terraform", "docker", "kubernetes", "jenkins"], 4),
    _job(8, "Java Developer", "Theta Test", "Chennai, India", "onsite", ["java", "spring", "mysql"], 3,
         desc="Java Developer. A bachelor's degree is required. Spring Boot and MySQL experience."),
    _job(9, "Software Engineer Intern", "Iota Test", "Remote", "remote", ["python", "javascript", "git"], None, "entry", emp="internship"),
    _job(10, "Technical Writer", "Kappa Test", "Delhi, India", "onsite", [], None),
]
