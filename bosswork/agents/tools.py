"""OpenAI Agents SDK 工具定义（function_tool + RunContextWrapper）"""

from __future__ import annotations

import json
from typing import Annotated

from agents import RunContextWrapper, function_tool

from bosswork.context import BossWorkContext
from bosswork.services.hr import format_messages_for_agent, hr_from_job
from bosswork.services.jobs import filter_jobs, find_job, job_to_summary, load_jobs
from bosswork.services.profile import profile_for_prompt


def _get_jobs(ctx: RunContextWrapper[BossWorkContext]) -> list[dict]:
    if ctx.context.jobs_cache is None:
        ctx.context.jobs_cache = load_jobs(ctx.context.jobs_path)
    return ctx.context.jobs_cache


@function_tool
def get_user_profile(ctx: RunContextWrapper[BossWorkContext]) -> str:
    """获取求职者完整预设资料（技能、项目、薪资期望、回复规则等）。"""
    return profile_for_prompt(ctx.context.user_profile)


@function_tool
def filter_job_listings(
    ctx: RunContextWrapper[BossWorkContext],
    cities: Annotated[list[str], "目标城市，如 ['广州','深圳']"],
    skills: Annotated[list[str], "期望匹配的技能关键词"],
    limit: Annotated[int, "返回岗位数量上限"] = 10,
) -> str:
    """根据城市与技能从本地 Boss 岗位数据中筛选并排序。"""
    jobs = _get_jobs(ctx)
    user_skills = skills or ctx.context.user_profile.get("skills") or []
    user_cities = cities or ctx.context.user_profile.get("preferred_cities") or []
    picked = filter_jobs(jobs, cities=list(user_cities), skills=list(user_skills), limit=limit)
    ctx.context.last_filtered_jobs = picked
    if not picked:
        return "未找到匹配岗位，请放宽城市或技能条件。"
    lines = [f"共 {len(picked)} 个匹配岗位："]
    for i, j in enumerate(picked, 1):
        lines.append(f"{i}. {job_to_summary(j)}")
    return "\n".join(lines)


@function_tool
def get_job_by_id(
    ctx: RunContextWrapper[BossWorkContext],
    job_id: Annotated[str, "岗位 job_id"],
) -> str:
    """获取单个岗位的详细信息。"""
    jobs = _get_jobs(ctx)
    job = find_job(jobs, job_id)
    if not job:
        return f"未找到 job_id={job_id}"
    ctx.context.selected_job_id = job_id
    return json.dumps(job, ensure_ascii=False, indent=2)


@function_tool
def get_hr_by_job_id(
    ctx: RunContextWrapper[BossWorkContext],
    job_id: Annotated[str, "岗位 job_id"],
) -> str:
    """从本地岗位数据获取 HR 信息（姓名、职位、boss_id、security_id）。"""
    jobs = _get_jobs(ctx)
    job = find_job(jobs, job_id)
    if not job:
        return f"未找到 job_id={job_id}"
    hr = hr_from_job(job)
    ctx.context.selected_job_id = job_id
    if hr.boss_id:
        ctx.context.selected_boss_id = hr.boss_id
    ctx.context.last_hr_info = hr.to_dict()
    lines = [hr.summary()]
    if not hr.boss_id:
        lines.append("提示：boss_id 为空，可登录后执行 queue_fetch_hr_info 补全。")
    return "\n".join(lines)


@function_tool
def list_jobs_missing_hr_id(
    ctx: RunContextWrapper[BossWorkContext],
    limit: Annotated[int, "返回条数上限"] = 15,
) -> str:
    """列出缺少 boss_id 的岗位（便于后续登录态补采 HR 信息）。"""
    jobs = _get_jobs(ctx)
    missing = [j for j in jobs if not j.get("boss_id")]
    if not missing:
        return "所有岗位均已包含 boss_id。"
    lines = [f"共 {len(missing)} 个岗位缺少 boss_id，前 {limit} 条："]
    for i, j in enumerate(missing[:limit], 1):
        lines.append(f"{i}. {job_to_summary(j)} | HR:{j.get('boss_name') or '-'}")
    return "\n".join(lines)


@function_tool
def queue_fetch_hr_info(
    ctx: RunContextWrapper[BossWorkContext],
    job_id: Annotated[str, "要补全 HR 信息的岗位 job_id"],
) -> str:
    """登录态浏览器：从详情 API 补全岗位的 boss_id 等 HR 信息。"""
    ctx.context.pending_actions.append({"action": "fetch_hr_info", "job_id": job_id})
    return f"已排队补全 HR 信息: {job_id}（需 execute 浏览器操作）"


@function_tool
def queue_list_hr_conversations(ctx: RunContextWrapper[BossWorkContext]) -> str:
    """登录态浏览器：读取聊天页会话列表。"""
    ctx.context.pending_actions.append({"action": "list_conversations"})
    return "已排队：读取 Boss 聊天会话列表（需 execute 浏览器操作）"


@function_tool
def queue_fetch_hr_messages(
    ctx: RunContextWrapper[BossWorkContext],
    boss_id: Annotated[str, "HR 的 bossId"],
    limit_pages: Annotated[int, "历史消息翻页数"] = 5,
) -> str:
    """登录态浏览器：拉取与某 HR 的聊天记录，供 JobSeekerAgent 回复。"""
    ctx.context.pending_actions.append(
        {"action": "fetch_messages", "boss_id": boss_id, "limit_pages": limit_pages}
    )
    ctx.context.selected_boss_id = boss_id
    return f"已排队拉取 boss_id={boss_id} 的聊天记录"


@function_tool
def reply_to_hr_message(
    ctx: RunContextWrapper[BossWorkContext],
    hr_message: Annotated[str, "HR 发来的最新一条消息（招聘方说的话）"],
    job_id: Annotated[str | None, "当前沟通的岗位 job_id，可选"] = None,
    conversation_history: Annotated[str | None, "此前对话记录，格式如 HR：… / 我：…"] = None,
) -> str:
    """记录 HR 消息与上下文，供 JobSeekerAgent 以求职者身份生成回复。返回当前上下文摘要。"""
    if job_id:
        ctx.context.selected_job_id = job_id
    parts = [f"HR 说：{hr_message}"]
    if conversation_history:
        parts.append(f"历史对话：\n{conversation_history}")
    if job_id:
        parts.append(f"岗位 job_id：{job_id}")
    parts.append(f"求职者资料摘要：{ctx.context.profile_summary()}")
    return "\n".join(parts)


@function_tool
def select_job_for_chat(
    ctx: RunContextWrapper[BossWorkContext],
    job_id: Annotated[str, "将要沟通的岗位 job_id"],
) -> str:
    """标记当前要沟通的岗位，供求职者回复 Agent 使用。"""
    jobs = _get_jobs(ctx)
    job = find_job(jobs, job_id)
    if not job:
        return f"岗位不存在: {job_id}"
    ctx.context.selected_job_id = job_id
    return f"已选中岗位: {job_to_summary(job)}"


@function_tool
def queue_browser_greet(
    ctx: RunContextWrapper[BossWorkContext],
    job_id: Annotated[str, "要打招呼的岗位 job_id"],
    message: Annotated[str, "求职者主动发给 HR 的打招呼正文（第一人称）"],
) -> str:
    """将求职者发起的打招呼加入浏览器待执行队列。"""
    ctx.context.pending_actions.append(
        {"action": "greet", "job_id": job_id, "message": message}
    )
    return f"已排队: 对 {job_id} 发送打招呼（共 {len(ctx.context.pending_actions)} 个待执行操作）"


@function_tool
def queue_browser_reply(
    ctx: RunContextWrapper[BossWorkContext],
    boss_id: Annotated[str, "HR 的 bossId"],
    message: Annotated[str, "求职者要发送给 HR 的回复正文（第一人称）"],
) -> str:
    """将求职者的回复加入浏览器待发送队列（Human-in-the-loop）。"""
    ctx.context.pending_actions.append(
        {"action": "reply", "boss_id": boss_id, "message": message}
    )
    return f"已排队回复，待用户在浏览器确认发送。"


@function_tool
def list_pending_browser_actions(ctx: RunContextWrapper[BossWorkContext]) -> str:
    """列出待执行的浏览器操作（打招呼/发消息）。"""
    actions = ctx.context.pending_actions
    if not actions:
        return "无待执行操作。"
    return json.dumps(actions, ensure_ascii=False, indent=2)


# 供 Agent 注册的工具集
HR_TOOLS = [
    get_hr_by_job_id,
    list_jobs_missing_hr_id,
    queue_fetch_hr_info,
    queue_list_hr_conversations,
    queue_fetch_hr_messages,
]

JOB_TOOLS = [
    get_user_profile,
    filter_job_listings,
    get_job_by_id,
    get_hr_by_job_id,
    select_job_for_chat,
]

CHAT_TOOLS = [
    get_user_profile,
    get_job_by_id,
    get_hr_by_job_id,
    select_job_for_chat,
    reply_to_hr_message,
    queue_fetch_hr_info,
    queue_list_hr_conversations,
    queue_fetch_hr_messages,
    queue_browser_greet,
    queue_browser_reply,
    list_pending_browser_actions,
]

ALL_TOOLS = list(
    {t.name: t for t in JOB_TOOLS + CHAT_TOOLS + HR_TOOLS}.values()
)
