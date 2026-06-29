"""岗位数据服务"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_jobs(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"岗位数据不存在: {p}")
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    import pandas as pd
    return pd.read_csv(p).to_dict("records")


def find_job(jobs: list[dict], job_id: str) -> dict | None:
    for j in jobs:
        if j.get("job_id") == job_id:
            return j
    return None


def filter_jobs(
    jobs: list[dict],
    cities: list[str] | None = None,
    skills: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    cities = cities or []
    skills_lower = [s.lower() for s in (skills or [])]

    scored: list[tuple[float, dict]] = []
    for job in jobs:
        city = job.get("target_city") or job.get("city") or ""
        if cities and city not in cities:
            continue

        job_skills = job.get("extracted_skills") or job.get("skills") or []
        if isinstance(job_skills, str):
            job_skills = [s.strip() for s in job_skills.split(",") if s.strip()]

        overlap = 0
        if skills_lower:
            for s in skills_lower:
                if any(s in str(x).lower() for x in job_skills):
                    overlap += 1
            if overlap == 0:
                continue

        score = overlap * 10.0
        user_skills = skills_lower
        for js in job_skills:
            if any(us in str(js).lower() for us in user_skills):
                score += 2
        scored.append((score, job))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [j for _, j in scored[:limit]]


def job_to_summary(job: dict) -> str:
    skills = job.get("extracted_skills") or job.get("skills") or []
    if isinstance(skills, list):
        skills = ", ".join(str(s) for s in skills[:6])
    return (
        f"{job.get('job_name')} @ {job.get('company')} "
        f"({job.get('target_city') or job.get('city')}) | "
        f"HR:{job.get('boss_name') or '-'} | "
        f"经验:{job.get('experience')} | 技能:{skills} | id:{job.get('job_id')}"
    )
