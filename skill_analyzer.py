"""从岗位数据中提取与统计技能"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from config import SKILL_KEYWORDS


def _normalize_skill(skill: str) -> str:
    return skill.strip().strip("，,、;；")


def extract_skills_from_job(job: dict) -> list[str]:
    """从岗位列表项或详情中合并技能标签。"""
    skills: set[str] = set()

    for field in ("skills", "showSkills"):
        value = job.get(field)
        if isinstance(value, list):
            skills.update(_normalize_skill(s) for s in value if s)
        elif isinstance(value, str) and value:
            skills.add(_normalize_skill(value))

    labels = job.get("jobLabels") or []
    for label in labels:
        label = _normalize_skill(str(label))
        if _is_likely_skill(label):
            skills.add(label)

    description = job.get("postDescription") or job.get("description") or ""
    if description:
        skills.update(extract_skills_from_text(description))

    return sorted(skills)


def extract_skills_from_text(text: str) -> set[str]:
    """从 JD 文本中匹配常见技能关键词。"""
    found: set[str] = set()
    lower_text = text.lower()

    for skill in SKILL_KEYWORDS:
        pattern = re.escape(skill)
        if re.search(pattern, text, re.IGNORECASE):
            found.add(skill)
        elif skill.lower() != skill and skill.lower() in lower_text:
            found.add(skill)

    return found


def _is_likely_skill(label: str) -> bool:
    """过滤学历、经验等非技能标签。"""
    non_skill_patterns = (
        r"^\d+-\d+年$",
        r"^\d+年以内$",
        r"^经验不限$",
        r"^(本科|大专|硕士|博士|学历不限|高中|中专|不限)$",
        r"^(全职|兼职|实习)$",
        r"^\d+-\d+K",
        r"^面议$",
    )
    for pattern in non_skill_patterns:
        if re.match(pattern, label):
            return False
    return len(label) <= 20


def aggregate_skill_stats(jobs: Iterable[dict]) -> Counter:
    """统计技能出现频次。"""
    counter: Counter = Counter()
    for job in jobs:
        for skill in job.get("extracted_skills") or extract_skills_from_job(job):
            counter[skill] += 1
    return counter
