"""Boss 直聘反爬识别与应对"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Playwright / Chrome 自动化特征隐藏
STEALTH_INIT_SCRIPT = """
(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

  window.chrome = window.chrome || { runtime: {} };

  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters);

  delete window.__playwright;
  delete window.__pwInitScripts;
})();
"""

# 浏览器内 fetch 模板（携带完整请求头，模拟页面 XHR）
BROWSER_FETCH_SCRIPT = """
async ({ apiPath, params }) => {
  const query = new URLSearchParams(params).toString();
  const origin = window.location.origin.includes('zhipin.com')
    ? window.location.origin
    : 'https://www.zhipin.com';
  const url = origin + apiPath + '?' + query;
  const resp = await fetch(url, {
    method: 'GET',
    credentials: 'include',
    headers: {
      'Accept': 'application/json, text/plain, */*',
      'X-Requested-With': 'XMLHttpRequest',
      'Referer': origin + '/web/geek/jobs',
      'sec-fetch-dest': 'empty',
      'sec-fetch-mode': 'cors',
      'sec-fetch-site': 'same-origin',
    },
  });
  return await resp.json();
}
"""

ANTI_BOT_CODES = {37, 35, 121}


def is_blocked_response(data: dict) -> bool:
    """判断 API 是否被反爬拦截。"""
    if not isinstance(data, dict):
        return True
    code = data.get("code")
    if code in ANTI_BOT_CODES:
        return True
    if code != 0:
        msg = str(data.get("message") or "")
        if any(k in msg for k in ("异常", "频繁", "验证", "安全")):
            return True
    return False


def extract_security_params(data: dict) -> dict | None:
    """从 code=37 响应中提取 security-check 参数。"""
    zp = data.get("zpData") or {}
    seed = zp.get("seed")
    ts = zp.get("ts")
    name = zp.get("name")
    if seed and ts and name:
        return {"seed": seed, "ts": str(ts), "name": name}
    return None


def has_stoken(cookies: list[dict]) -> bool:
    return any(c.get("name") == "__zp_stoken__" and c.get("value") for c in cookies)


def has_user_login(cookies: list[dict]) -> bool:
    """真实用户登录（wt2），有薪资权限。"""
    return any(
        c.get("name") in ("wt2", "geek_zp_token") and c.get("value")
        for c in cookies
    )


def has_login(cookies: list[dict]) -> bool:
    return has_user_login(cookies) or has_stoken(cookies)


def random_delay(base: float = 1.5, jitter: float = 1.0) -> None:
    time.sleep(base + random.uniform(0, jitter))


def human_delay(page: Page, base_ms: int = 800, jitter_ms: int = 1200) -> None:
    page.wait_for_timeout(base_ms + random.randint(0, jitter_ms))


def simulate_human_activity(page: Page) -> None:
    """模拟滚动与鼠标移动，降低行为检测风险。"""
    try:
        width = page.viewport_size["width"] if page.viewport_size else 1440
        height = page.viewport_size["height"] if page.viewport_size else 900
        for _ in range(random.randint(1, 3)):
            page.mouse.move(
                random.randint(80, width - 80),
                random.randint(80, height - 80),
                steps=random.randint(8, 20),
            )
            page.wait_for_timeout(random.randint(200, 600))
        page.evaluate(
            "window.scrollBy(0, arguments[0])",
            random.randint(120, 480),
        )
        page.wait_for_timeout(random.randint(300, 800))
    except Exception:
        pass


class AntiBotHandler:
    """反爬处理器：stealth、security-check、重试。"""

    SECURITY_CHECK_URL = "https://www.zhipin.com/web/common/security-check.html"
    JOBS_URL = "https://www.zhipin.com/web/geek/jobs"

    def __init__(self, page: Page, context):
        self.page = page
        self.context = context
        self._security_pass_count = 0

    def apply_stealth(self) -> None:
        self.context.add_init_script(STEALTH_INIT_SCRIPT)

    def bootstrap_session(self) -> None:
        """首次进入时触发 security-check，确保 __zp_stoken__ 就绪。"""
        print("初始化会话，通过 security-check ...")
        self.page.goto(self.JOBS_URL, wait_until="domcontentloaded", timeout=60000)
        human_delay(self.page, 2000, 1500)
        simulate_human_activity(self.page)

        cookies = self.context.cookies()
        if not has_stoken(cookies):
            self._trigger_security_via_search_probe()
        self._wait_for_stoken(timeout_sec=15)

    def _trigger_security_via_search_probe(self) -> None:
        """发起一次探测请求，触发 security-check 流程。"""
        probe = self.fetch_api(
            "/wapi/zpgeek/search/joblist.json",
            {"scene": "1", "query": "Java", "city": "101010100", "page": "1", "pageSize": "1"},
        )
        params = extract_security_params(probe)
        if params:
            self.run_security_check(params, callback_url="/web/geek/jobs")

    def run_security_check(self, params: dict, callback_url: str = "/web/geek/jobs") -> bool:
        """访问 security-check 页面，让浏览器 JS 生成 __zp_stoken__。"""
        seed = quote(params["seed"], safe="")
        name = params["name"]
        ts = params["ts"]
        callback = quote(callback_url, safe="")

        url = (
            f"{self.SECURITY_CHECK_URL}?seed={seed}&name={name}&ts={ts}"
            f"&callbackUrl={callback}&srcReferer="
        )
        print(f"  执行 security-check (name={name}) ...")
        try:
            self.page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:
            self.page.wait_for_timeout(5000)

        human_delay(self.page, 1500, 1000)
        ok = self._wait_for_stoken(timeout_sec=20)
        if ok:
            self._security_pass_count += 1
            print(f"  security-check 通过，__zp_stoken__ 已生成。")
        else:
            print("  security-check 未完成，可能需要手动验证。")
        return ok

    def _wait_for_stoken(self, timeout_sec: int = 15) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if has_stoken(self.context.cookies()):
                return True
            self.page.wait_for_timeout(500)
        return has_stoken(self.context.cookies())

    def fetch_api(self, api_path: str, params: dict[str, Any]) -> dict:
        try:
            return self.page.evaluate(
                BROWSER_FETCH_SCRIPT,
                {"apiPath": api_path, "params": params},
            )
        except Exception as e:
            return {"code": -1, "message": str(e), "zpData": {}}

    def fetch_with_retry(
        self,
        api_path: str,
        params: dict[str, Any],
        max_retries: int = 3,
        callback_url: str = "/web/geek/jobs",
    ) -> dict:
        last: dict = {}
        for attempt in range(1, max_retries + 1):
            if "zhipin.com" not in self.page.url:
                self.page.goto(self.JOBS_URL, wait_until="domcontentloaded", timeout=60000)
                human_delay(self.page, 1000, 500)

            simulate_human_activity(self.page)
            human_delay(self.page, 400, 800)

            last = self.fetch_api(api_path, params)
            if not is_blocked_response(last):
                return last

            code = last.get("code")
            print(f"  反爬拦截 code={code}，第 {attempt}/{max_retries} 次重试 ...")

            sec_params = extract_security_params(last)
            if sec_params:
                self.run_security_check(sec_params, callback_url=callback_url)
            else:
                self.page.goto(self.JOBS_URL, wait_until="domcontentloaded", timeout=60000)
                human_delay(self.page, 2000, 1000)
                if not has_stoken(self.context.cookies()):
                    self._trigger_security_via_search_probe()

            random_delay(2.0, 2.0)

        return last

    def search_via_ui(
        self,
        keyword: str,
        city_code: str,
        page_num: int = 1,
        timeout_ms: int = 30000,
    ) -> dict | None:
        """通过 UI 搜索并拦截 joblist 网络响应（最接近真实用户）。"""
        jobs_url = f"{self.JOBS_URL}?city={city_code}&query={quote(keyword)}&page={page_num}"
        captured: dict = {}

        def on_response(response):
            if "search/joblist.json" in response.url and response.status == 200:
                try:
                    captured["data"] = response.json()
                except Exception:
                    pass

        self.page.on("response", on_response)
        try:
            self.page.goto(jobs_url, wait_until="domcontentloaded", timeout=60000)
            human_delay(self.page, 2000, 1500)
            simulate_human_activity(self.page)

            if page_num == 1:
                search_selectors = [
                    'input[placeholder*="搜索"]',
                    'input.ipt-search',
                    '.search-input input',
                    'input[name="query"]',
                ]
                for selector in search_selectors:
                    try:
                        el = self.page.locator(selector).first
                        if el.count() > 0 and el.is_visible(timeout=2000):
                            el.click()
                            el.fill("")
                            el.type(keyword, delay=random.randint(50, 120))
                            self.page.keyboard.press("Enter")
                            break
                    except Exception:
                        continue
            else:
                # 翻页：尝试点击页码或滚动加载
                try:
                    page_btn = self.page.locator(f'.options-pages a:has-text("{page_num}")').first
                    if page_btn.count() > 0 and page_btn.is_visible(timeout=3000):
                        page_btn.click()
                        human_delay(self.page, 1500, 1000)
                except Exception:
                    self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    human_delay(self.page, 1500, 1000)

            deadline = time.time() + timeout_ms / 1000
            while time.time() < deadline:
                if captured.get("data"):
                    break
                self.page.wait_for_timeout(500)

            if captured.get("data"):
                return captured["data"]

            return self.fetch_with_retry(
                "/wapi/zpgeek/search/joblist.json",
                {
                    "scene": "1",
                    "query": keyword,
                    "city": city_code,
                    "page": str(page_num),
                    "pageSize": "30",
                },
            )
        finally:
            self.page.remove_listener("response", on_response)

    def retry_call(
        self,
        func: Callable[[], dict],
        max_retries: int = 3,
        label: str = "请求",
    ) -> dict:
        last: dict = {}
        for attempt in range(1, max_retries + 1):
            last = func()
            if not is_blocked_response(last):
                return last
            print(f"  {label} 被拦截，重试 {attempt}/{max_retries} ...")
            sec = extract_security_params(last)
            if sec:
                self.run_security_check(sec)
            random_delay(2.5, 1.5)
        return last
