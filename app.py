"""BossWork 主入口 — OpenAI Agents SDK 架构"""

from __future__ import annotations

import argparse
import sys

from bosswork.agents.runner import create_context, run_agent
from bosswork.runtime.browser import execute_pending_actions


def cmd_run(args: argparse.Namespace) -> None:
    ctx = create_context(
        user_profile_path=args.user_profile,
        jobs_path=args.jobs,
        llm_config_path=args.llm_config,
    )
    output = run_agent(args.prompt, ctx, max_turns=args.max_turns)
    print("\n" + "=" * 50)
    print(output)
    print("=" * 50)

    if ctx.pending_actions and args.execute:
        print(f"\n检测到 {len(ctx.pending_actions)} 个待执行浏览器操作...")
        execute_pending_actions(ctx, auto_send=args.auto_send)


def cmd_interactive(args: argparse.Namespace) -> None:
    ctx = create_context(
        user_profile_path=args.user_profile,
        jobs_path=args.jobs,
        llm_config_path=args.llm_config,
    )
    print("BossWork Agent 交互模式（OpenAI Agents SDK）")
    print("输入 quit 退出 | 示例: 帮我筛选广州深圳 Java 岗位")
    print("-" * 50)

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break

        output = run_agent(user_input, ctx, max_turns=args.max_turns)
        print(f"\nAgent: {output}")

        if ctx.pending_actions:
            print(f"\n[系统] 有 {len(ctx.pending_actions)} 个浏览器操作待执行")
            if input("立即执行？(y/n): ").strip().lower() == "y":
                execute_pending_actions(ctx, auto_send=False)


def cmd_execute(args: argparse.Namespace) -> None:
    """仅执行已排队的浏览器操作（通常由 Agent 产出）。"""
    ctx = create_context(
        user_profile_path=args.user_profile,
        jobs_path=args.jobs,
    )
    if not ctx.pending_actions:
        print("无待执行操作。请先通过 agent run 生成。")
        return
    execute_pending_actions(ctx, auto_send=args.auto_send)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BossWork — OpenAI Agents SDK 求职 Agent 系统"
    )
    parser.add_argument("--user-profile", default="user_profile.json")
    parser.add_argument("--jobs", default="output/boss_jobs_20260628_234041.json")
    parser.add_argument("--llm-config", default="llm_config.json")
    parser.add_argument("--max-turns", type=int, default=15)

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="单次任务")
    p_run.add_argument("prompt", help="任务描述")
    p_run.add_argument("--execute", action="store_true", help="Agent 完成后执行浏览器操作")
    p_run.add_argument("--auto-send", action="store_true", help="浏览器操作自动发送")
    p_run.set_defaults(func=cmd_run)

    p_i = sub.add_parser("interactive", aliases=["i"], help="交互模式")
    p_i.set_defaults(func=cmd_interactive)

    p_ex = sub.add_parser("execute", help="执行 pending 浏览器操作")
    p_ex.add_argument("--auto-send", action="store_true")
    p_ex.set_defaults(func=cmd_execute)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
