"""用户资料服务"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_user_profile(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else Path("user_profile.json")
    if not p.exists():
        p = Path("user_profile.example.json")
    if not p.exists():
        raise FileNotFoundError("请创建 user_profile.json")
    return json.loads(p.read_text(encoding="utf-8"))


def load_llm_settings(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else Path("llm_config.json")
    if not p.exists():
        p = Path("llm_config.example.json")
    return json.loads(p.read_text(encoding="utf-8"))


def profile_for_prompt(profile: dict[str, Any]) -> str:
    skills = profile.get("skills") or []
    if isinstance(skills, list):
        skills = ", ".join(skills)
    rules = profile.get("custom_rules") or []
    avoid = profile.get("avoid_topics") or []
    return f"""【求职者资料】
姓名：{profile.get('name')}
方向：{profile.get('title')}
年限：{profile.get('experience_years')}
学历：{profile.get('education')}
技能：{skills}
项目：{profile.get('projects_summary')}
自我介绍：{profile.get('self_intro')}
期望城市：{', '.join(profile.get('preferred_cities') or [])}
期望薪资：{profile.get('salary_expectation')}
到岗：{profile.get('availability')}
工作偏好：{profile.get('work_preferences')}
回复风格：{profile.get('reply_style', '礼貌简洁')}
规则：{'; '.join(rules)}
避免：{', '.join(avoid)}"""
