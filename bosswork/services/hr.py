"""HR 信息服务 — 离线岗位关联 + 登录态实时获取"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class HRInfo:
    """HR / 招聘方信息。"""

    boss_id: str | None = None
    boss_name: str | None = None
    boss_title: str | None = None
    boss_avatar: str | None = None
    job_id: str | None = None
    job_name: str | None = None
    company: str | None = None
    security_id: str | None = None
    job_url: str | None = None
    source: str = "offline"  # offline | detail_api | chat_dom

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def summary(self) -> str:
        parts = [
            f"HR: {self.boss_name or '未知'}",
            f"职位: {self.boss_title or '-'}",
        ]
        if self.boss_id:
            parts.append(f"bossId: {self.boss_id}")
        if self.job_name:
            parts.append(f"岗位: {self.job_name} @ {self.company or ''}")
        if self.job_id:
            parts.append(f"job_id: {self.job_id}")
        return " | ".join(parts)


def hr_from_job(job: dict) -> HRInfo:
    """从离线岗位 JSON 提取 HR 信息（列表 API 字段 + 可选详情 enrichment）。"""
    job_id = job.get("job_id")
    return HRInfo(
        boss_id=job.get("boss_id"),
        boss_name=job.get("boss_name"),
        boss_title=job.get("boss_title"),
        boss_avatar=job.get("boss_avatar"),
        job_id=job_id,
        job_name=job.get("job_name"),
        company=job.get("company"),
        security_id=job.get("security_id"),
        job_url=job.get("job_url") or (
            f"https://www.zhipin.com/job_detail/{job_id}.html" if job_id else None
        ),
        source="offline",
    )


def hr_from_detail_api(zp_data: dict, job_id: str | None = None) -> HRInfo:
    """从 /wapi/zpgeek/job/detail.json 响应解析 HR 信息。"""
    job = zp_data.get("jobInfo") or {}
    boss = zp_data.get("bossInfo") or {}
    brand = zp_data.get("brandComInfo") or {}
    jid = job.get("encryptId") or job_id
    return HRInfo(
        boss_id=boss.get("encryptBossId") or boss.get("bossId"),
        boss_name=boss.get("name"),
        boss_title=boss.get("title"),
        boss_avatar=boss.get("large"),
        job_id=jid,
        job_name=job.get("jobName"),
        company=brand.get("brandName"),
        security_id=None,
        job_url=f"https://www.zhipin.com/job_detail/{jid}.html" if jid else None,
        source="detail_api",
    )


def parse_conversation_preview(preview: str) -> dict[str, str]:
    """从聊天列表 DOM 预览文本中尽量解析 HR 名、公司、最后消息。"""
    text = preview.strip()
    result: dict[str, str] = {"raw_preview": text[:200]}
    # 常见格式：日期 + 姓名 + 公司 + 职位 + 消息预览
    m = re.search(r"([\u4e00-\u9fa5]{2,4})\s+([\u4e00-\u9fa5\w\s]+?)(?:招聘|HR|人事|专员|经理|主管)", text)
    if m:
        result["boss_name_guess"] = m.group(1)
    return result


def format_messages_for_agent(messages: list[dict]) -> str:
    """将 chat_client 消息列表格式化为 Agent 可读对话。"""
    lines: list[str] = []
    for m in messages:
        if m.get("body_type") != 1 or not m.get("text"):
            continue
        role = "我" if m.get("from_me") else (m.get("from_name") or "HR")
        lines.append(f"{role}：{m['text']}")
    return "\n".join(lines) if lines else "（暂无文字消息）"


def enrich_job_with_hr(job: dict, hr: HRInfo) -> dict:
    """将 HR 信息写回岗位 dict。"""
    out = dict(job)
    for k, v in hr.to_dict().items():
        if v and k != "source":
            out[k] = v
    return out
