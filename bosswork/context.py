"""运行时上下文（OpenAI Agents SDK 依赖注入）"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BossWorkContext:
    """贯穿 Agent 运行链路的共享上下文。"""

    user_profile: dict[str, Any]
    jobs_path: Path
    llm_config_path: Path = Path("llm_config.json")
    browser_profile: Path = Path(".boss_profile")

    jobs_cache: list[dict[str, Any]] | None = field(default=None, repr=False)
    selected_job_id: str | None = None
    selected_boss_id: str | None = None
    last_hr_info: dict[str, Any] | None = None
    last_conversations: list[dict[str, Any]] = field(default_factory=list)
    last_filtered_jobs: list[dict[str, Any]] = field(default_factory=list)

    # 浏览器操作队列（Human-in-the-loop）
    pending_actions: list[dict[str, Any]] = field(default_factory=list)

    def profile_summary(self) -> str:
        p = self.user_profile
        skills = p.get("skills") or []
        if isinstance(skills, list):
            skills = ", ".join(skills)
        return (
            f"姓名:{p.get('name')} | 方向:{p.get('title')} | "
            f"年限:{p.get('experience_years')} | 技能:{skills} | "
            f"期望薪资:{p.get('salary_expectation')} | 城市:{', '.join(p.get('preferred_cities') or [])}"
        )
