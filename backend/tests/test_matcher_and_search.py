from app.schemas import Profile
from app.services.job_store import JobFilters, JobStore
from app.services.matcher import match_job
from tests.fixtures import FIXTURE_JOBS

STORE = JobStore(list(FIXTURE_JOBS))
BY = {j.job_id: j for j in FIXTURE_JOBS}


def profile(**kw):
    base = dict(designation="Backend Developer", skills=["python", "django", "postgresql", "docker"], years_experience=3.0)
    base.update(kw)
    return Profile(**base)


def test_strong_match_scores_high_and_explains():
    m = match_job(profile(), BY["j_test0001"])
    assert m.score >= 85
    assert set(m.matched_skills) == {"python", "django", "postgresql", "docker"}
    assert "Matches your skills" in m.explanation
    assert {c.name for c in m.components} == {"skills", "title", "experience", "location", "education"}


def test_missing_skills_and_experience_gap_lower_score():
    strong = match_job(profile(), BY["j_test0001"]).score
    weak = match_job(profile(), BY["j_test0007"])
    assert weak.score < strong
    assert "kubernetes" in weak.missing_skills or "terraform" in weak.missing_skills
    assert "Not found on your profile" in weak.explanation


def test_related_skills_get_partial_credit():
    m = match_job(profile(skills=["python", "flask"]), BY["j_test0001"])  # job wants django
    assert "django" in m.partial_skills


def test_unknown_info_is_excluded_not_penalised():
    full = match_job(profile(), BY["j_test0001"])
    no_exp = match_job(profile(years_experience=None), BY["j_test0001"])
    exp = next(c for c in no_exp.components if c.name == "experience")
    assert exp.score is None and "Not evaluated" in exp.note
    assert any("experience" in u.lower() for u in no_exp.uncertainties)
    assert no_exp.score >= full.score - 10  # missing info must not crater the score


def test_empty_skill_job_flags_uncertainty():
    m = match_job(profile(), BY["j_test0010"])
    assert any("Skill fit unknown" in u for u in m.uncertainties)
    assert m.confidence in {"low", "medium"}


def test_remote_preference_affects_location_component():
    p = profile(work_arrangement="remote")
    assert match_job(p, BY["j_test0002"]).components[3].score == 1.0
    assert match_job(p, BY["j_test0001"]).components[3].score == 0.0


def test_education_requirement_only_when_stated():
    p = profile(education=[{"degree": "B.Tech in CS"}])
    m = match_job(p, BY["j_test0008"])
    assert next(c for c in m.components if c.name == "education").score == 1.0
    assert next(c for c in match_job(p, BY["j_test0001"]).components if c.name == "education").score is None


def test_score_bounds():
    for j in FIXTURE_JOBS:
        assert 0 <= match_job(profile(), j).score <= 100


def test_search_filters():
    assert {j.job_id for j, _ in STORE.search(JobFilters(remote="remote"))} == {"j_test0002", "j_test0003", "j_test0006", "j_test0009"}
    assert all("react" in j.skills for j, _ in STORE.search(JobFilters(skills=["react"])))
    assert {j.job_id for j, _ in STORE.search(JobFilters(level="entry"))} >= {"j_test0003", "j_test0004", "j_test0009"}
    assert [j.job_id for j, _ in STORE.search(JobFilters(locations=["Pune"]))] == ["j_test0004"]
    top = STORE.search(JobFilters(query="python developer"))
    assert top and "python" in top[0][0].title.lower() + " ".join(top[0][0].skills)
    assert STORE.search(JobFilters(query="astronaut")) == []


def test_empty_store():
    s = JobStore()
    assert len(s) == 0 and s.search(JobFilters()) == [] and s.get("x") is None
    assert s.stats()["count"] == 0


def test_store_skips_invalid_records(tmp_path):
    import json
    f = tmp_path / "jobs.json"
    f.write_text(json.dumps([{"job_id": "j_ok00001", "title": "T", "company": "C", "source_url": "https://x.test/1"},
                             {"title": "missing id"}]))
    s = JobStore.load(f)
    assert len(s) == 1 and s.skipped_invalid == 1
    assert len(JobStore.load(tmp_path / "missing.json")) == 0
    (tmp_path / "bad.json").write_text("{not json")
    assert len(JobStore.load(tmp_path / "bad.json")) == 0
