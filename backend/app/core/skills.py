"""Skill taxonomy shared by the resume parser, job normaliser and matcher.

Each canonical skill maps to a list of aliases. Matching is case-insensitive,
boundary-aware (so "java" does not match inside "javascript") and masks matched
spans so "react native" does not also count as "react".
"""
from __future__ import annotations

import re
from functools import lru_cache

SKILLS: dict[str, list[str]] = {
    # languages
    "python": ["python", "python3"],
    "javascript": ["javascript", "js", "ecmascript", "es6"],
    "typescript": ["typescript", "ts"],
    "java": ["java", "core java", "j2ee"],
    "c++": ["c++", "cpp"],
    "c#": ["c#", "csharp"],
    "c": ["c programming", "embedded c"],
    "go": ["golang", "go lang"],
    "rust": ["rust"],
    "php": ["php"],
    "ruby": ["ruby"],
    "kotlin": ["kotlin"],
    "swift": ["swift"],
    "scala": ["scala"],
    "r": ["r programming", "rstudio"],
    "sql": ["sql", "t-sql", "pl/sql"],
    "bash": ["bash", "shell scripting", "shell script"],
    # frontend
    "react": ["react", "reactjs", "react.js"],
    "react native": ["react native"],
    "next.js": ["next.js", "nextjs", "next js"],
    "angular": ["angular", "angularjs"],
    "vue": ["vue", "vue.js", "vuejs"],
    "svelte": ["svelte", "sveltekit"],
    "html": ["html", "html5"],
    "css": ["css", "css3"],
    "sass": ["sass", "scss"],
    "tailwind": ["tailwind", "tailwindcss", "tailwind css"],
    "redux": ["redux"],
    "webpack": ["webpack"],
    "jquery": ["jquery"],
    # backend
    "node.js": ["node.js", "nodejs", "node js"],
    "express": ["express", "express.js", "expressjs"],
    "nestjs": ["nestjs", "nest.js"],
    "django": ["django"],
    "flask": ["flask"],
    "fastapi": ["fastapi"],
    "spring": ["spring", "spring boot", "springboot"],
    ".net": [".net", "dotnet", "asp.net", ".net core"],
    "laravel": ["laravel"],
    "rails": ["ruby on rails", "rails"],
    "graphql": ["graphql"],
    "rest api": ["rest api", "rest apis", "restful", "restful api", "restful apis"],
    "grpc": ["grpc"],
    "microservices": ["microservices", "microservice"],
    # data stores
    "postgresql": ["postgresql", "postgres"],
    "mysql": ["mysql"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "sqlite": ["sqlite"],
    "elasticsearch": ["elasticsearch", "elastic search", "opensearch"],
    "kafka": ["kafka", "apache kafka"],
    "rabbitmq": ["rabbitmq"],
    "dynamodb": ["dynamodb"],
    "snowflake": ["snowflake"],
    "bigquery": ["bigquery"],
    # cloud / devops
    "aws": ["aws", "amazon web services"],
    "azure": ["azure"],
    "gcp": ["gcp", "google cloud"],
    "docker": ["docker"],
    "kubernetes": ["kubernetes", "k8s"],
    "terraform": ["terraform"],
    "ansible": ["ansible"],
    "jenkins": ["jenkins"],
    "ci/cd": ["ci/cd", "cicd", "ci cd", "continuous integration"],
    "github actions": ["github actions"],
    "linux": ["linux", "unix"],
    "git": ["git", "github", "gitlab"],
    "nginx": ["nginx"],
    "prometheus": ["prometheus"],
    "grafana": ["grafana"],
    # data / ml / ai
    "machine learning": ["machine learning", "ml"],
    "deep learning": ["deep learning"],
    "nlp": ["nlp", "natural language processing"],
    "computer vision": ["computer vision"],
    "llm": ["llm", "llms", "large language models", "large language model"],
    "generative ai": ["generative ai", "genai", "gen ai"],
    "langchain": ["langchain"],
    "rag": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
    "pytorch": ["pytorch"],
    "tensorflow": ["tensorflow"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    "spark": ["spark", "pyspark", "apache spark"],
    "hadoop": ["hadoop"],
    "airflow": ["airflow", "apache airflow"],
    "dbt": ["dbt"],
    "tableau": ["tableau"],
    "power bi": ["power bi", "powerbi"],
    "excel": ["excel", "microsoft excel"],
    "data analysis": ["data analysis", "data analytics"],
    "etl": ["etl", "elt", "data pipelines", "data pipeline"],
    "statistics": ["statistics", "statistical analysis"],
    # mobile / other
    "android": ["android"],
    "ios": ["ios"],
    "flutter": ["flutter"],
    "firebase": ["firebase"],
    "selenium": ["selenium"],
    "cypress": ["cypress"],
    "jest": ["jest"],
    "pytest": ["pytest"],
    "junit": ["junit"],
    "testing": ["unit testing", "test automation", "qa automation", "manual testing"],
    "agile": ["agile", "scrum", "kanban"],
    "jira": ["jira"],
    "figma": ["figma"],
    "ui/ux": ["ui/ux", "ux design", "ui design", "user experience"],
    "system design": ["system design", "distributed systems"],
    "data structures": ["data structures", "algorithms", "dsa"],
    "oop": ["oop", "object oriented", "object-oriented"],
    "web3": ["solidity", "blockchain", "web3"],
    "cybersecurity": ["cybersecurity", "cyber security", "penetration testing", "infosec"],
    "salesforce": ["salesforce"],
    "sap": ["sap"],
    "wordpress": ["wordpress"],
    "shopify": ["shopify"],
    "seo": ["seo"],
}

# Skills treated as roughly interchangeable families: a candidate who knows one
# gets partial (0.5) credit for a missing one from the same family.
RELATED_GROUPS: list[set[str]] = [
    {"javascript", "typescript"},
    {"react", "next.js", "vue", "angular", "svelte", "redux"},
    {"node.js", "express", "nestjs"},
    {"django", "flask", "fastapi"},
    {"postgresql", "mysql", "sqlite", "sql"},
    {"mongodb", "dynamodb", "firebase"},
    {"aws", "azure", "gcp"},
    {"docker", "kubernetes"},
    {"machine learning", "deep learning", "pytorch", "tensorflow", "scikit-learn", "nlp"},
    {"llm", "generative ai", "langchain", "rag"},
    {"java", "spring", "kotlin"},
    {"c#", ".net"},
    {"tableau", "power bi"},
    {"android", "kotlin", "flutter", "react native"},
    {"rest api", "graphql", "grpc"},
    {"jenkins", "github actions", "ci/cd"},
]

_BOUNDARY_L = r"(?<![A-Za-z0-9+#])"
_BOUNDARY_R = r"(?![A-Za-z0-9+#])"


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, re.Pattern[str]]]:
    pairs: list[tuple[str, str]] = []
    for canonical, aliases in SKILLS.items():
        for alias in aliases:
            pairs.append((canonical, alias))
    # longest alias first so multi-word skills win over their substrings
    pairs.sort(key=lambda p: -len(p[1]))
    return [
        (canonical, re.compile(_BOUNDARY_L + re.escape(alias) + _BOUNDARY_R, re.IGNORECASE))
        for canonical, alias in pairs
    ]


def extract_skills(text: str) -> list[str]:
    """Return canonical skills found in ``text`` in order of first appearance."""
    if not text:
        return []
    working = text
    found: dict[str, int] = {}
    for canonical, pattern in _compiled():
        for match in pattern.finditer(working):
            found.setdefault(canonical, match.start())
        if canonical in found:
            working = pattern.sub(lambda m: " " * len(m.group(0)), working)
    return [s for s, _ in sorted(found.items(), key=lambda kv: kv[1])]


def normalize_skill(raw: str) -> str:
    """Map a user-typed skill to its canonical name when known, else lowercase."""
    cleaned = raw.strip().lower()
    for canonical, aliases in SKILLS.items():
        if cleaned == canonical or cleaned in aliases:
            return canonical
    return cleaned


def related_skills(skill: str) -> set[str]:
    out: set[str] = set()
    for group in RELATED_GROUPS:
        if skill in group:
            out |= group
    out.discard(skill)
    return out
