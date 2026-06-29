"""基于 LLM 的 HR 问答回复生成（结合用户预设信息与对话上下文）"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_PROFILE_PATH = Path("user_profile.json")
DEFAULT_LLM_CONFIG_PATH = Path("llm_config.json")


def load_json_file(path: Path | str, default: dict | None = None) -> dict:
    p = Path(path)
    if not p.exists():
        return default or {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_user_profile(path: Path | str | None = None) -> dict:
    p = Path(path) if path else DEFAULT_PROFILE_PATH
    if not p.exists():
        p = Path("user_profile.example.json")
    data = load_json_file(p, {})
    if not data:
        raise FileNotFoundError(
            f"未找到用户配置，请复制 user_profile.example.json 为 user_profile.json 并填写"
        )
    return data


def load_llm_config(path: Path | str | None = None) -> dict:
    p = Path(path) if path else DEFAULT_LLM_CONFIG_PATH
    if not p.exists():
        p = Path("llm_config.example.json")
    cfg = load_json_file(p, {})
    return {
        "api_key_env": cfg.get("api_key_env", "OPENAI_API_KEY"),
        "base_url": cfg.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model": cfg.get("model") or os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "temperature": float(cfg.get("temperature", 0.7)),
        "max_tokens": int(cfg.get("max_tokens", 300)),
    }


def get_api_key(config: dict) -> str | None:
    key = os.getenv(config["api_key_env"]) or os.getenv("OPENAI_API_KEY")
    return key.strip() if key else None


def _skills_str(job: dict | None) -> str:
    if not job:
        return ""
    skills = job.get("extracted_skills") or job.get("skills") or []
    if isinstance(skills, str):
        return skills
    return ", ".join(str(s) for s in skills[:8])


def build_system_prompt(user: dict, job: dict | None) -> str:
    job_block = ""
    if job:
        job_block = f"""
【当前沟通岗位】
- 岗位：{job.get('job_name', '未知')}
- 公司：{job.get('company', '未知')}
- 城市：{job.get('target_city') or job.get('city', '')}
- 要求技能：{_skills_str(job)}
- 经验/学历：{job.get('experience', '')} / {job.get('degree', '')}
"""

    rules = user.get("custom_rules") or []
    avoid = user.get("avoid_topics") or []
    skills = user.get("skills") or []
    if isinstance(skills, list):
        skills = ", ".join(skills)

    return f"""你扮演【求职者本人】，在 Boss 直聘上回复 HR 发来的消息。

【角色 — 必须遵守】
- 输入是 HR（招聘方）说的话
- 你的输出是求职者要说的话，用第一人称「我」
- 不要生成 HR 口吻，不要第三人称描述候选人

【求职者资料】
- 姓名：{user.get('name', '求职者')}
- 职位方向：{user.get('title', '')}
- 工作年限：{user.get('experience_years', '')}
- 学历：{user.get('education', '')}
- 核心技能：{skills}
- 项目经历：{user.get('projects_summary', '')}
- 自我介绍：{user.get('self_intro', '')}
- 期望城市：{', '.join(user.get('preferred_cities') or [])}
- 期望薪资：{user.get('salary_expectation', '面议')}
- 到岗时间：{user.get('availability', '')}
- 工作偏好：{user.get('work_preferences', '')}
{job_block}
【回复要求】
- 风格：{user.get('reply_style', '礼貌、简洁、像真人聊天')}
- 必须基于求职者资料如实回答，不要编造资料里没有的经历或数字
- 只输出一条可直接发送给 HR 的纯文本，不要引号、不要 markdown、不要「回复：」等前缀
- 针对 HR 最新一条消息，以求职者身份作答
- 自定义规则：
{chr(10).join(f'  - {r}' for r in rules) or '  - 无'}
- 避免：{', '.join(avoid) if avoid else '无'}
"""


def format_chat_history(messages: list[dict], limit: int = 12) -> str:
    """将聊天记录格式化为 LLM 可读文本。"""
    lines: list[str] = []
    text_msgs = [m for m in messages if m.get("body_type") == 1 and m.get("text")]
    for m in text_msgs[-limit:]:
        role = "我" if m.get("from_me") else (m.get("from_name") or "HR")
        lines.append(f"{role}：{m['text']}")
    return "\n".join(lines) if lines else "（暂无历史消息）"


def _clean_reply(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^["\'「『]|["\'」』]$', "", text)
    text = re.sub(r"^(回复|答)[:：]\s*", "", text)
    return text.strip()


def generate_reply_llm(
    hr_text: str,
    user_profile: dict,
    job: dict | None = None,
    chat_history: list[dict] | None = None,
    llm_config_path: str | Path | None = None,
) -> str:
    config = load_llm_config(llm_config_path)
    api_key = get_api_key(config)
    if not api_key:
        raise RuntimeError(
            f"未设置 LLM API Key，请配置环境变量 {config['api_key_env']} 或 OPENAI_API_KEY"
        )

    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError("请安装 openai: pip install openai") from e

    client = OpenAI(api_key=api_key, base_url=config["base_url"])
    history_text = format_chat_history(chat_history or [])

    user_content = f"""【历史对话】
{history_text}

【HR 最新一条消息】
{hr_text}

请生成求职者的下一条回复。"""

    response = client.chat.completions.create(
        model=config["model"],
        temperature=config["temperature"],
        max_tokens=config["max_tokens"],
        messages=[
            {"role": "system", "content": build_system_prompt(user_profile, job)},
            {"role": "user", "content": user_content},
        ],
    )
    content = response.choices[0].message.content or ""
    return _clean_reply(content)


def generate_greet_llm(
    user_profile: dict,
    job: dict,
    llm_config_path: str | Path | None = None,
) -> str:
    """生成首次打招呼消息。"""
    config = load_llm_config(llm_config_path)
    api_key = get_api_key(config)
    if not api_key:
        skills = (job.get("extracted_skills") or job.get("skills") or [])[:3]
        if isinstance(skills, list):
            skills = ", ".join(str(s) for s in skills)
        return (
            f"您好，我对「{job.get('job_name')}」很感兴趣，"
            f"熟悉 {skills}，方便进一步沟通吗？"
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=config["base_url"])
    prompt = f"""根据求职者资料，为以下岗位写一条 Boss 直聘首次打招呼（60字内，礼貌专业）：

岗位：{job.get('job_name')} @ {job.get('company')}（{job.get('target_city') or job.get('city')}）
岗位技能：{_skills_str(job)}

求职者：{user_profile.get('self_intro', '')}

只输出打招呼正文，不要引号和前缀。"""

    response = client.chat.completions.create(
        model=config["model"],
        temperature=config["temperature"],
        max_tokens=150,
        messages=[
            {"role": "system", "content": build_system_prompt(user_profile, job)},
            {"role": "user", "content": prompt},
        ],
    )
    return _clean_reply(response.choices[0].message.content or "")


def is_llm_available(llm_config_path: str | Path | None = None) -> bool:
    try:
        config = load_llm_config(llm_config_path)
        return bool(get_api_key(config))
    except Exception:
        return False
