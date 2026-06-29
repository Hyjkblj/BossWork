"""Boss 直聘聊天客户端（登录态 + 浏览器会话）"""

from __future__ import annotations

import json
import time
from typing import Any

from anti_bot import human_delay, simulate_human_activity
from scraper import BossZhipinScraper


CHAT_URL = "https://www.zhipin.com/web/geek/chat"
JOB_DETAIL_API = "/wapi/zpgeek/job/detail.json"
HISTORY_MSG_API = "/wapi/zpchat/geek/historyMsg"


def _parse_message(raw: dict) -> dict:
    body = raw.get("body") or {}
    return {
        "mid": raw.get("mid"),
        "from_me": not raw.get("received", True),
        "from_name": (raw.get("from") or {}).get("name"),
        "body_type": body.get("type"),
        "text": body.get("text"),
        "job_card": body.get("jobDesc"),
        "time": raw.get("time"),
    }


class BossChatClient(BossZhipinScraper):
    """在已登录浏览器中读取/发送 HR 消息。"""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("defer_bootstrap", True)
        super().__init__(*args, **kwargs)

    def ensure_logged_in(self, timeout_sec: int = 120) -> bool:
        return self.wait_for_login(timeout_sec=timeout_sec)

    def _fetch_in_browser(self, api_path: str, params: dict[str, Any]) -> dict:
        return self.anti_bot.fetch_api(api_path, params)

    def get_job_detail(self, security_id: str) -> dict:
        return self._fetch_in_browser(JOB_DETAIL_API, {"securityId": security_id})

    def resolve_job_context(self, job_id: str, security_id: str | None = None) -> dict:
        """从 job_id 打开详情页，解析 bossId / securityId。"""
        if security_id:
            detail = self.get_job_detail(security_id)
            if detail.get("code") == 0:
                zp = detail.get("zpData") or {}
                job = zp.get("jobInfo") or {}
                boss = zp.get("bossInfo") or {}
                brand = zp.get("brandComInfo") or {}
                return {
                    "job_id": job.get("encryptId") or job_id,
                    "security_id": security_id,
                    "boss_id": boss.get("encryptBossId") or boss.get("bossId"),
                    "boss_name": boss.get("name"),
                    "job_name": job.get("jobName"),
                    "company": brand.get("brandName"),
                    "job_url": f"https://www.zhipin.com/job_detail/{job_id}.html",
                }

        url = f"https://www.zhipin.com/job_detail/{job_id}.html"
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        human_delay(self.page, 2000, 1000)

        boss_id = self.page.evaluate("""
        () => {
            const entries = performance.getEntriesByType('resource');
            for (let i = entries.length - 1; i >= 0; i--) {
                const m = entries[i].name.match(/bossId=([^&]+)/);
                if (m) return decodeURIComponent(m[1]);
            }
            return null;
        }
        """)
        return {
            "job_id": job_id,
            "security_id": security_id,
            "boss_id": boss_id,
            "job_url": url,
        }

    def start_chat_from_job(self, job_id: str, security_id: str | None = None) -> dict:
        """打开岗位详情并点击「立即沟通」，进入与该 HR 的会话。"""
        ctx = self.resolve_job_context(job_id, security_id)
        self.page.goto(ctx["job_url"], wait_until="domcontentloaded", timeout=60000)
        human_delay(self.page, 1500, 800)

        selectors = [
            ".btn-startchat",
            "a.btn-startchat",
            "button.btn-startchat",
            "text=立即沟通",
        ]
        clicked = False
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=3000):
                    loc.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            raise RuntimeError(f"未找到「立即沟通」按钮，可能已沟通过或需登录: {ctx['job_url']}")

        human_delay(self.page, 2500, 1000)
        boss_id = self.get_current_boss_id()
        if boss_id:
            ctx["boss_id"] = boss_id
        return ctx

    def open_chat_page(self) -> None:
        self.page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
        human_delay(self.page, 2000, 1000)

    def open_conversation_by_index(self, index: int = 0) -> str | None:
        """在聊天列表中打开第 index 个会话，返回 bossId。"""
        self.open_chat_page()
        self.page.locator(".friend-content").nth(index).click()
        human_delay(self.page, 2000, 500)
        return self.get_current_boss_id()

    def open_conversation_by_boss_name(self, name: str) -> str | None:
        """按 HR 姓名打开会话。"""
        self.open_chat_page()
        items = self.page.locator(".friend-content")
        count = items.count()
        for i in range(count):
            text = items.nth(i).inner_text(timeout=2000)
            if name in text:
                items.nth(i).click()
                human_delay(self.page, 2000, 500)
                return self.get_current_boss_id()
        return None

    def get_current_boss_id(self) -> str | None:
        return self.page.evaluate("""
        () => {
            const entries = performance.getEntriesByType('resource');
            for (let i = entries.length - 1; i >= 0; i--) {
                const url = entries[i].name;
                if (!url.includes('/wapi/zpchat/geek/historyMsg')) continue;
                const m = url.match(/bossId=([^&]+)/);
                if (m) return decodeURIComponent(m[1]);
            }
            return null;
        }
        """)

    def list_conversations_dom(self, limit: int = 20) -> list[dict]:
        """从聊天页 DOM 读取会话列表（需已登录）。"""
        self.open_chat_page()
        raw = self.page.evaluate(f"""
        () => {{
            const items = document.querySelectorAll('.friend-content');
            const out = [];
            for (let i = 0; i < Math.min(items.length, {limit}); i++) {{
                const el = items[i];
                out.push({{
                    index: i,
                    preview: el.textContent.trim().slice(0, 200),
                }});
            }}
            return JSON.stringify(out);
        }}
        """)
        return json.loads(raw)

    def fetch_messages(
        self,
        boss_id: str,
        max_msg_id: int = 0,
        count: int = 30,
    ) -> dict:
        params = {
            "bossId": boss_id,
            "maxMsgId": str(max_msg_id),
            "c": str(count),
            "page": "1",
            "src": "0",
        }
        data = self._fetch_in_browser(HISTORY_MSG_API, params)
        if data.get("code") != 0:
            return {"messages": [], "has_more": False, "error": data.get("message")}

        zp = data.get("zpData") or {}
        msgs = [_parse_message(m) for m in (zp.get("messages") or [])]
        return {
            "messages": msgs,
            "has_more": zp.get("hasMore", False),
            "raw_count": len(msgs),
        }

    def fetch_all_messages(self, boss_id: str, max_pages: int = 5) -> list[dict]:
        all_msgs: list[dict] = []
        max_msg_id = 0
        for _ in range(max_pages):
            batch = self.fetch_messages(boss_id, max_msg_id=max_msg_id)
            msgs = batch.get("messages") or []
            if not msgs:
                break
            all_msgs.extend(msgs)
            if not batch.get("has_more"):
                break
            mids = [m["mid"] for m in msgs if m.get("mid")]
            max_msg_id = min(mids) if mids else 0
            time.sleep(0.5)
        return all_msgs

    def send_message_ui(self, text: str) -> bool:
        """在当前打开的聊天窗口发送文本（模拟用户输入）。"""
        simulate_human_activity(self.page)
        input_selectors = [
            "div.chat-input textarea",
            "textarea.input-area",
            ".message-input textarea",
            "textarea",
            "div[contenteditable='true']",
        ]
        for sel in input_selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() == 0 or not loc.is_visible(timeout=2000):
                    continue
                loc.click()
                if "contenteditable" in sel:
                    loc.fill(text)
                else:
                    loc.fill("")
                    loc.type(text, delay=30)
                break
            except Exception:
                continue
        else:
            return False

        send_selectors = [
            "button.btn-send",
            ".send-message",
            "text=发送",
            "button:has-text('发送')",
        ]
        for sel in send_selectors:
            try:
                btn = self.page.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click()
                    human_delay(self.page, 800, 400)
                    return True
            except Exception:
                continue

        self.page.keyboard.press("Enter")
        human_delay(self.page, 800, 400)
        return True

    def wait_for_new_hr_message(
        self,
        boss_id: str,
        last_mid: int = 0,
        timeout_sec: int = 120,
        poll_interval: float = 2.5,
    ) -> dict | None:
        """轮询等待 HR 新消息（登录态 REST，接近实时）。"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            batch = self.fetch_messages(boss_id, max_msg_id=0, count=20)
            for msg in batch.get("messages") or []:
                mid = msg.get("mid") or 0
                if mid <= last_mid:
                    continue
                if msg.get("body_type") == 1 and msg.get("text") and not msg.get("from_me"):
                    return msg
            time.sleep(poll_interval)
        return None

    def print_conversation(self, boss_id: str) -> None:
        msgs = self.fetch_all_messages(boss_id)
        for m in reversed(msgs):
            if m.get("body_type") != 1 or not m.get("text"):
                continue
            role = "我" if m.get("from_me") else (m.get("from_name") or "HR")
            print(f"[{role}] {m['text']}")
