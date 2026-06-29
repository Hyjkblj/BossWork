"""HR 对话 Agent：监听 HR 消息，以求职者身份生成回复"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from chat_client import BossChatClient
from llm_reply import (
    generate_greet_llm,
    generate_reply_llm,
    is_llm_available,
    load_user_profile,
)

DEFAULT_BROWSER_PROFILE = ".boss_profile"
DEFAULT_JOBS_FILE = "output/boss_jobs_20260628_234041.json"


def load_jobs(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    import pandas as pd
    return pd.read_csv(p).to_dict("records")


def find_job_by_id(jobs: list[dict], job_id: str) -> dict | None:
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
    cities = cities or ["广州", "深圳"]
    skills = [s.lower() for s in (skills or [])]

    scored: list[tuple[float, dict]] = []
    for job in jobs:
        city = job.get("target_city") or job.get("city") or ""
        if cities and city not in cities:
            continue
        job_skills = job.get("extracted_skills") or job.get("skills") or []
        if isinstance(job_skills, str):
            job_skills = [s.strip() for s in job_skills.split(",") if s.strip()]
        overlap = 0
        if skills:
            job_lower = {str(s).lower() for s in job_skills}
            overlap = sum(1 for s in skills if s in job_lower or any(s in x.lower() for x in job_skills))
            if overlap == 0:
                continue
        score = overlap * 10 + (1 if "Spring" in str(job_skills) else 0)
        scored.append((score, job))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [j for _, j in scored[:limit]]


def rule_based_reply(hr_text: str, job: dict | None, user: dict) -> str:
    text = hr_text.strip()
    job_name = (job or {}).get("job_name") or "该岗位"
    intro = user.get("self_intro") or user.get("title") or "有相关经验"

    if re.search(r"你好|您好|在吗|方便", text):
        return f"您好，我对贵司的「{job_name}」很感兴趣，{intro}，方便进一步沟通吗？"
    if re.search(r"简历|附件", text):
        return "好的，我可以发送在线简历，请问还需要补充哪方面信息？"
    if re.search(r"薪资|工资|期望|多少钱", text):
        sal = user.get("salary_expectation", "面议")
        return f"期望薪资在 {sal} 左右，具体可以根据岗位职责和团队情况再沟通。"
    if re.search(r"加班|大小周|996", text):
        return user.get("work_preferences") or "想了解一下团队的工作节奏，方便介绍一下吗？"
    if re.search(r"面试|时间|约", text):
        avail = user.get("availability", "时间相对灵活")
        return f"可以的，{avail}，请问您方便的时间段是？线上或线下都可以。"
    if re.search(r"经验|项目|做过", text):
        skills = user.get("skills") or []
        if isinstance(skills, list):
            skills = ", ".join(skills[:4])
        proj = user.get("projects_summary", "")
        return f"{proj or intro}，熟悉 {skills}，和岗位比较契合，可以结合项目详细介绍。"
    return f"收到，感谢回复。关于「{job_name}」，还想了解一下团队技术栈和面试流程，谢谢！"


def generate_reply(
    hr_text: str,
    user_profile: dict,
    job: dict | None = None,
    chat_history: list[dict] | None = None,
    use_llm: bool = True,
    llm_config: str | None = None,
) -> str:
    if use_llm and is_llm_available(llm_config):
        try:
            return generate_reply_llm(
                hr_text,
                user_profile,
                job=job,
                chat_history=chat_history,
                llm_config_path=llm_config,
            )
        except Exception as e:
            print(f"[LLM 失败，回退规则回复] {e}")
    return rule_based_reply(hr_text, job, user_profile)


def run_chat_session(
    boss_id: str,
    job: dict | None,
    user_profile: dict,
    auto_reply: bool = False,
    greet_first: str | None = None,
    use_llm: bool = True,
    llm_config: str | None = None,
) -> None:
    with BossChatClient(
        headless=False,
        user_data_dir=str(Path(DEFAULT_BROWSER_PROFILE).resolve()),
        browser_channel="chrome",
    ) as client:
        if not client.ensure_logged_in(timeout_sec=180):
            print("请先登录 Boss 直聘账号（wt2），否则无法聊天。")
            return

        client.open_chat_page()
        boss_name = (job or {}).get("boss_name")
        if boss_name:
            opened = client.open_conversation_by_boss_name(boss_name)
            if opened:
                boss_id = opened

        if greet_first:
            print(f"发送: {greet_first}")
            client.send_message_ui(greet_first)
            time.sleep(2)

        mode = "LLM" if use_llm and is_llm_available(llm_config) else "规则"
        print(f"\n开始监听 HR 消息 (bossId={boss_id})，回复模式: {mode}，Ctrl+C 退出\n")

        all_history = client.fetch_all_messages(boss_id)
        last_mid = max((m.get("mid") or 0 for m in all_history), default=0)
        client.print_conversation(boss_id)

        while True:
            msg = client.wait_for_new_hr_message(boss_id, last_mid=last_mid, timeout_sec=300)
            if not msg:
                print("5 分钟内无新消息，退出监听。")
                break
            last_mid = max(last_mid, msg.get("mid") or 0)
            hr_text = msg.get("text") or ""
            print(f"\n[HR 说] {hr_text}")

            recent = client.fetch_all_messages(boss_id)
            reply = generate_reply(
                hr_text,
                user_profile,
                job=job,
                chat_history=recent,
                use_llm=use_llm,
                llm_config=llm_config,
            )
            print(f"[求职者回复建议] {reply}")

            if auto_reply:
                confirm = "y"
            else:
                confirm = input("发送此回复？(y/n/e=自己编辑/r=重新生成): ").strip().lower()

            if confirm == "r":
                reply = generate_reply(
                    hr_text,
                    user_profile,
                    job=job,
                    chat_history=recent,
                    use_llm=use_llm,
                    llm_config=llm_config,
                )
                print(f"[求职者回复建议] {reply}")
                confirm = input("发送此回复？(y/n/e): ").strip().lower()

            if confirm == "e":
                reply = input("输入回复: ").strip()
                confirm = "y" if reply else "n"

            if confirm == "y":
                ok = client.send_message_ui(reply)
                print("已发送。" if ok else "发送失败，请手动在浏览器发送。")
            else:
                print("已跳过发送。")


def run_job_outreach(
    jobs_file: str,
    cities: list[str],
    skills: list[str],
    limit: int,
    user_profile: dict,
    auto_greet: bool = False,
    use_llm: bool = True,
    llm_config: str | None = None,
) -> None:
    jobs = load_jobs(jobs_file)
    picked = filter_jobs(jobs, cities=cities, skills=skills, limit=limit)
    print(f"筛选出 {len(picked)} 个岗位\n")

    with BossChatClient(
        headless=False,
        user_data_dir=str(Path(DEFAULT_BROWSER_PROFILE).resolve()),
        browser_channel="chrome",
    ) as client:
        if not client.ensure_logged_in(timeout_sec=180):
            print("请先登录 Boss 直聘。")
            return

        for i, job in enumerate(picked, 1):
            job_id = job.get("job_id")
            if not job_id:
                continue
            print(f"\n[{i}/{len(picked)}] {job.get('job_name')} @ {job.get('company')} ({job.get('target_city')})")
            try:
                ctx = client.start_chat_from_job(job_id, security_id=job.get("security_id"))
                if use_llm and is_llm_available(llm_config):
                    greet = generate_greet_llm(user_profile, job, llm_config)
                else:
                    sk = (job.get("extracted_skills") or job.get("skills") or [])[:3]
                    if isinstance(sk, list):
                        sk = ", ".join(str(s) for s in sk)
                    greet = (
                        f"您好，我对「{job.get('job_name')}」很感兴趣，"
                        f"熟悉 {sk}，方便聊聊吗？"
                    )
                if auto_greet:
                    client.send_message_ui(greet)
                    print(f"  已打招呼: {greet}")
                else:
                    print(f"  已打开沟通页，建议首句: {greet}")
                    input("  按 Enter 继续下一个岗位...")
                print(f"  bossId: {ctx.get('boss_id')}")
            except Exception as e:
                print(f"  失败: {e}")
            time.sleep(3)


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--user-profile",
        default="user_profile.json",
        help="求职者预设信息 JSON",
    )
    parser.add_argument(
        "--llm-config",
        default="llm_config.json",
        help="LLM 配置 JSON（模型、base_url）",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="禁用 LLM，使用规则回复",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Boss 直聘 HR 实时问答 Agent（LLM + 预设信息）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_filter = sub.add_parser("filter", help="筛选岗位并展示")
    p_filter.add_argument("--jobs", default=DEFAULT_JOBS_FILE)
    p_filter.add_argument("--cities", nargs="+", default=["广州", "深圳"])
    p_filter.add_argument("--skills", nargs="+", default=["Java", "Spring", "Redis"])
    p_filter.add_argument("--limit", type=int, default=10)

    p_greet = sub.add_parser("greet", help="对筛选岗位逐个发起沟通")
    p_greet.add_argument("--jobs", default=DEFAULT_JOBS_FILE)
    p_greet.add_argument("--cities", nargs="+", default=["广州", "深圳"])
    p_greet.add_argument("--skills", nargs="+", default=["Java", "Spring"])
    p_greet.add_argument("--limit", type=int, default=5)
    p_greet.add_argument("--auto", action="store_true", help="自动发送打招呼（慎用）")
    _add_llm_args(p_greet)

    p_chat = sub.add_parser("chat", help="与指定 HR 实时问答")
    p_chat.add_argument("--boss-id", help="HR 的 bossId")
    p_chat.add_argument("--job-id", help="岗位 job_id（自动加载岗位信息）")
    p_chat.add_argument("--jobs", default=DEFAULT_JOBS_FILE, help="岗位数据文件，供 --job-id 匹配")
    p_chat.add_argument("--auto-reply", action="store_true", help="自动发送（慎用）")
    p_chat.add_argument("--greet", help="进入会话后先发一句")
    _add_llm_args(p_chat)

    p_hist = sub.add_parser("history", help="查看历史消息")
    p_hist.add_argument("--boss-id", required=True)

    p_test = sub.add_parser("test-llm", help="测试 LLM 回复（无需登录）")
    p_test.add_argument("--hr-msg", required=True, help="模拟 HR 消息")
    p_test.add_argument("--job-id", help="关联岗位 job_id")
    p_test.add_argument("--jobs", default=DEFAULT_JOBS_FILE)
    _add_llm_args(p_test)

    args = parser.parse_args()

    if args.cmd == "filter":
        jobs = filter_jobs(
            load_jobs(args.jobs),
            cities=args.cities,
            skills=args.skills,
            limit=args.limit,
        )
        for j in jobs:
            skills = j.get("extracted_skills") or j.get("skills") or []
            print(
                f"- {j.get('job_name')} | {j.get('company')} | {j.get('target_city')} | "
                f"{', '.join(str(s) for s in skills[:5])} | {j.get('job_id')}"
            )

    elif args.cmd == "test-llm":
        user = load_user_profile(getattr(args, "user_profile", "user_profile.json"))
        job = None
        if args.job_id:
            job = find_job_by_id(load_jobs(args.jobs), args.job_id)
        reply = generate_reply(
            args.hr_msg,
            user,
            job=job,
            use_llm=not args.no_llm,
            llm_config=args.llm_config,
        )
        print(f"HR 说: {args.hr_msg}\n\n求职者回复: {reply}")

    elif args.cmd == "greet":
        user = load_user_profile(args.user_profile)
        run_job_outreach(
            args.jobs,
            args.cities,
            args.skills,
            args.limit,
            user,
            auto_greet=args.auto,
            use_llm=not args.no_llm,
            llm_config=args.llm_config,
        )

    elif args.cmd == "chat":
        user = load_user_profile(args.user_profile)
        boss_id = args.boss_id
        job = None
        if args.job_id:
            job = find_job_by_id(load_jobs(args.jobs), args.job_id)
            with BossChatClient(
                headless=False,
                user_data_dir=str(Path(DEFAULT_BROWSER_PROFILE).resolve()),
                browser_channel="chrome",
            ) as client:
                if not client.ensure_logged_in(180):
                    return
                ctx = client.start_chat_from_job(args.job_id, security_id=(job or {}).get("security_id"))
                boss_id = ctx.get("boss_id")
        if not boss_id:
            print("需要 --boss-id 或 --job-id")
            return
        run_chat_session(
            boss_id,
            job,
            user,
            auto_reply=args.auto_reply,
            greet_first=args.greet,
            use_llm=not args.no_llm,
            llm_config=args.llm_config,
        )

    elif args.cmd == "history":
        with BossChatClient(
            headless=False,
            user_data_dir=str(Path(DEFAULT_BROWSER_PROFILE).resolve()),
            browser_channel="chrome",
        ) as client:
            if not client.ensure_logged_in(120):
                return
            client.print_conversation(args.boss_id)


if __name__ == "__main__":
    main()
