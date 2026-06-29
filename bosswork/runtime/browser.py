"""浏览器运行时：执行 Agent 排队的沟通操作"""

from __future__ import annotations

import time
from pathlib import Path

from bosswork.context import BossWorkContext
from bosswork.services.hr import HRInfo, enrich_job_with_hr, format_messages_for_agent
from bosswork.services.jobs import find_job, load_jobs
from chat_client import BossChatClient


def execute_pending_actions(
    context: BossWorkContext,
    auto_send: bool = False,
    login_wait: int = 120,
) -> list[dict]:
    """执行 pending_actions 中的打招呼/回复（需登录 Boss）。"""
    if not context.pending_actions:
        return []

    results: list[dict] = []
    actions = list(context.pending_actions)
    context.pending_actions.clear()

    with BossChatClient(
        headless=False,
        user_data_dir=str(context.browser_profile.resolve()),
        browser_channel="chrome",
    ) as client:
        if not client.ensure_logged_in(timeout_sec=login_wait):
            context.pending_actions = actions
            raise RuntimeError("未登录 Boss 直聘，无法执行浏览器操作")

        for action in actions:
            kind = action.get("action")
            try:
                if kind == "greet":
                    job_id = action["job_id"]
                    msg = action["message"]
                    client.start_chat_from_job(job_id)
                    if auto_send:
                        ok = client.send_message_ui(msg)
                        results.append({"action": kind, "job_id": job_id, "sent": ok})
                    else:
                        print(f"\n[待发送] {msg}")
                        confirm = input("发送？(y/n): ").strip().lower()
                        ok = client.send_message_ui(msg) if confirm == "y" else False
                        results.append({"action": kind, "job_id": job_id, "sent": ok})
                elif kind == "reply":
                    boss_id = action["boss_id"]
                    msg = action["message"]
                    client.open_chat_page()
                    if auto_send:
                        ok = client.send_message_ui(msg)
                    else:
                        print(f"\n[待发送] {msg}")
                        confirm = input("发送？(y/n): ").strip().lower()
                        ok = client.send_message_ui(msg) if confirm == "y" else False
                    results.append({"action": kind, "boss_id": boss_id, "sent": ok})
                elif kind == "fetch_hr_info":
                    job_id = action["job_id"]
                    jobs = load_jobs(context.jobs_path)
                    job = find_job(jobs, job_id) or {}
                    ctx = client.resolve_job_context(job_id, job.get("security_id"))
                    hr = HRInfo(
                        boss_id=ctx.get("boss_id"),
                        boss_name=ctx.get("boss_name") or job.get("boss_name"),
                        boss_title=job.get("boss_title"),
                        job_id=job_id,
                        job_name=ctx.get("job_name") or job.get("job_name"),
                        company=ctx.get("company") or job.get("company"),
                        security_id=ctx.get("security_id") or job.get("security_id"),
                        job_url=ctx.get("job_url") or job.get("job_url"),
                        source="detail_api",
                    )
                    enriched = enrich_job_with_hr(job, hr)
                    context.last_hr_info = enriched
                    if enriched.get("boss_id"):
                        context.selected_boss_id = enriched["boss_id"]
                    results.append({"action": kind, "job_id": job_id, "hr": hr.to_dict()})
                elif kind == "list_conversations":
                    convs = client.list_conversations_dom(limit=20)
                    context.last_conversations = convs
                    results.append({"action": kind, "conversations": convs})
                elif kind == "fetch_messages":
                    boss_id = action["boss_id"]
                    limit_pages = action.get("limit_pages", 5)
                    msgs = client.fetch_all_messages(boss_id, max_pages=limit_pages)
                    text = format_messages_for_agent(msgs)
                    hr_msgs = [m for m in msgs if not m.get("from_me") and m.get("text")]
                    results.append({
                        "action": kind,
                        "boss_id": boss_id,
                        "message_count": len(msgs),
                        "latest_hr_message": hr_msgs[-1]["text"] if hr_msgs else None,
                        "conversation_text": text,
                    })
                else:
                    results.append({"action": kind, "error": "unknown action"})
            except Exception as e:
                results.append({"action": kind, "error": str(e)})
            time.sleep(2)

    return results
