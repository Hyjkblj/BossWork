"""Agent 运行入口"""

from __future__ import annotations

from pathlib import Path

from agents import Runner, trace

from bosswork.agents.definitions import build_agents
from bosswork.context import BossWorkContext
from bosswork.services.profile import load_llm_settings, load_user_profile


def create_context(
    user_profile_path: str = "user_profile.json",
    jobs_path: str = "output/boss_jobs_20260628_234041.json",
    llm_config_path: str = "llm_config.json",
) -> BossWorkContext:
    return BossWorkContext(
        user_profile=load_user_profile(user_profile_path),
        jobs_path=Path(jobs_path),
        llm_config_path=Path(llm_config_path),
    )


def run_agent(
    user_input: str,
    context: BossWorkContext,
    max_turns: int = 15,
) -> str:
    """同步运行 Orchestrator Agent。"""
    llm_config = load_llm_settings(context.llm_config_path)
    orchestrator = build_agents(context.user_profile, llm_config)

    with trace("BossWork Agent Run"):
        result = Runner.run_sync(
            orchestrator,
            user_input,
            context=context,
            max_turns=max_turns,
        )
    return result.final_output or ""


async def run_agent_async(
    user_input: str,
    context: BossWorkContext,
    max_turns: int = 15,
) -> str:
    llm_config = load_llm_settings(context.llm_config_path)
    orchestrator = build_agents(context.user_profile, llm_config)

    with trace("BossWork Agent Run"):
        result = await Runner.run(
            orchestrator,
            user_input,
            context=context,
            max_turns=max_turns,
        )
    return result.final_output or ""
