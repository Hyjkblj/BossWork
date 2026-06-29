"""登录态 HR 信息获取（封装 chat_client）"""

from __future__ import annotations

from bosswork.services.hr import HRInfo, format_messages_for_agent, hr_from_detail_api, hr_from_job, parse_conversation_preview
from bosswork.services.jobs import find_job, load_jobs
from chat_client import BossChatClient


class HRService:
    """统一 HR 信息获取：离线岗位 + 登录态 API/DOM。"""

    def __init__(self, client: BossChatClient, jobs: list[dict] | None = None):
        self.client = client
        self.jobs = jobs or []

    @classmethod
    def from_jobs_file(cls, jobs_path: str, **client_kw) -> "HRService":
        client = BossChatClient(**client_kw)
        client.__enter__()
        return cls(client, load_jobs(jobs_path))

    def close(self) -> None:
        self.client.close()

    def get_hr_by_job_id(self, job_id: str, fetch_live: bool = True) -> HRInfo:
        """获取岗位对应 HR 信息；优先登录态详情 API，回退离线数据。"""
        job = find_job(self.jobs, job_id)
        offline = hr_from_job(job) if job else HRInfo(job_id=job_id)

        if not fetch_live:
            return offline

        security_id = (job or {}).get("security_id")
        try:
            if security_id:
                detail = self.client.get_job_detail(security_id)
                if detail.get("code") == 0:
                    live = hr_from_detail_api(detail.get("zpData") or {}, job_id)
                    if live.boss_id or live.boss_name:
                        return live
            ctx = self.client.resolve_job_context(job_id, security_id)
            return HRInfo(
                boss_id=ctx.get("boss_id") or offline.boss_id,
                boss_name=ctx.get("boss_name") or offline.boss_name,
                job_id=job_id,
                job_name=ctx.get("job_name") or offline.job_name,
                company=ctx.get("company") or offline.company,
                security_id=ctx.get("security_id") or offline.security_id,
                job_url=ctx.get("job_url") or offline.job_url,
                source="detail_api",
            )
        except Exception:
            return offline

    def list_conversations(self, limit: int = 20) -> list[dict]:
        """登录态：聊天页会话列表（DOM + 解析）。"""
        raw = self.client.list_conversations_dom(limit=limit)
        out: list[dict] = []
        for item in raw:
            parsed = parse_conversation_preview(item.get("preview", ""))
            out.append({"index": item.get("index"), **parsed})
        return out

    def get_messages(self, boss_id: str, limit_pages: int = 5) -> dict:
        """登录态：获取与某 HR 的聊天记录。"""
        msgs = self.client.fetch_all_messages(boss_id, max_pages=limit_pages)
        hr_msgs = [m for m in msgs if not m.get("from_me") and m.get("text")]
        return {
            "boss_id": boss_id,
            "message_count": len(msgs),
            "hr_message_count": len(hr_msgs),
            "latest_hr_message": hr_msgs[-1]["text"] if hr_msgs else None,
            "conversation_text": format_messages_for_agent(msgs),
            "messages": msgs,
        }

    def wait_hr_reply(self, boss_id: str, last_mid: int = 0, timeout: int = 120) -> dict | None:
        """登录态：等待 HR 新消息。"""
        msg = self.client.wait_for_new_hr_message(boss_id, last_mid=last_mid, timeout_sec=timeout)
        if not msg:
            return None
        return {
            "boss_id": boss_id,
            "mid": msg.get("mid"),
            "from_name": msg.get("from_name"),
            "text": msg.get("text"),
        }
