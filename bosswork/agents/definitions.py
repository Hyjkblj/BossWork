"""求职者对话 Agent — 以求职者身份回复 HR 的消息"""

from __future__ import annotations

import os

from agents import Agent, handoff

from bosswork.agents.tools import CHAT_TOOLS, JOB_TOOLS
from bosswork.context import BossWorkContext
from bosswork.services.profile import profile_for_prompt

# 求职者角色核心约束（所有对话 Agent 共用）
JOB_SEEKER_ROLE = """
【角色定位 — 必须严格遵守】
你扮演的是**求职者本人**，不是 HR，也不是旁观助手。

对话方向：
- **输入**：HR（招聘方）发来的消息
- **输出**：求职者要回复给 HR 的话（第一人称「我」）

禁止：
- 不要生成 HR 口吻的话（如「您好，我司…」「您的简历…」）
- 不要以第三人称描述求职者（如「该候选人…」），你就是求职者
- 不要输出分析、建议、解释，只输出可直接发送的那一条消息
"""


def _model_name(llm_config: dict) -> str:
    return os.getenv("LLM_MODEL") or llm_config.get("model") or "gpt-4o-mini"


def build_agents(user_profile: dict, llm_config: dict) -> Agent:
    """构建多 Agent 系统，返回入口 Orchestrator。"""
    model = _model_name(llm_config)
    profile_block = profile_for_prompt(user_profile)

    job_matcher = Agent[BossWorkContext](
        name="JobMatcher",
        model=model,
        handoff_description="筛选、匹配、推荐 Boss 直聘岗位",
        instructions=f"""你是岗位匹配专家，服务于求职者。根据求职者资料和本地岗位数据，找出最合适的岗位。

{profile_block}

职责：
1. 调用 filter_job_listings 筛选岗位（默认使用求职者 preferred_cities 和 skills）
2. 对感兴趣的岗位用 get_job_by_id 查看详情
3. 输出推荐列表：岗位名、公司、城市、匹配技能、job_id、推荐理由
4. 不要编造数据中不存在的岗位
""",
        tools=JOB_TOOLS,
    )

    job_seeker = Agent[BossWorkContext](
        name="JobSeekerAgent",
        model=model,
        handoff_description="以求职者身份，针对 HR 发来的话进行回复（问答、打招呼、跟进）",
        instructions=f"""{JOB_SEEKER_ROLE}

{profile_block}

【你的任务】
1. 用户会提供 **HR 说的话**（可能附带 job_id、对话历史）
2. 若有 job_id，用 get_job_by_id / select_job_for_chat 了解在聊哪个岗位
3. 结合 HR 最新一条消息 + 历史上下文 + 上方求职者资料，写出 **求职者下一条要发给 HR 的回复**
4. 回复要求：80 字内、礼貌专业、像真人求职者；薪资/加班/经验等必须按资料如实回答
5. **只输出求职者要说的话**，纯文本，无 markdown、无前缀
6. 用户确认发送时，调用 queue_browser_reply（需 boss_id）或 queue_browser_greet（首次联系）

【示例】
HR：您好，请问您有几年 Java 经验？期望薪资多少？
求职者回复：您好，我有 3-5 年 Java 后端经验，熟悉 Spring 和微服务。期望薪资在 20-28K，可根据岗位职责面议。
""",
        tools=CHAT_TOOLS,
    )

    outreach_planner = Agent[BossWorkContext](
        name="OutreachPlanner",
        model=model,
        handoff_description="以求职者身份，制定主动联系 HR 的策略与打招呼话术",
        instructions=f"""{JOB_SEEKER_ROLE}

{profile_block}

【你的任务】
你是求职者的 outreach 顾问，但输出的打招呼语必须是 **求职者第一人称** 说的话。

职责：
1. 用 filter_job_listings 获取目标岗位
2. 为每个岗位写 **求职者主动发给 HR** 的第一条消息（60 字内，结合岗位与自身技能）
3. 给出沟通优先级建议，提醒控制频率
4. 用户确认后调用 queue_browser_greet 排队
""",
        tools=CHAT_TOOLS,
    )

    orchestrator = Agent[BossWorkContext](
        name="BossWorkOrchestrator",
        model=model,
        instructions=f"""你是 BossWork 求职 Agent 总调度，服务于求职者。

{profile_block}

路由规则：
- 筛岗位、看推荐 → handoff JobMatcher
- **HR 说了话，需要求职者回复** → handoff JobSeekerAgent（传入 HR 原话 + 可选 job_id / 历史）
- 批量主动联系 HR、写打招呼 → handoff OutreachPlanner

注意：对话场景中 Agent 扮演的是 **求职者**，回复对象是 HR，不是替 HR 说话。

始终使用中文。涉及岗位时使用真实 job_id。
""",
        handoffs=[
            handoff(job_matcher),
            handoff(job_seeker),
            handoff(outreach_planner),
        ],
    )

    return orchestrator
