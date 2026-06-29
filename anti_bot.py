"""Boss 直聘反爬识别与应对"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote

from config import SECURITY_CHECK_COOLDOWN_SEC

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

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

# 空白页 → 登录页（绝不跳职位页，避免 _security_check 死循环）
ANTI_BLANK_SCRIPT = """
(() => {
  const LOGIN = 'https://www.zhipin.com/web/user/?ka=header-login';
  if (window.location.href === 'about:blank') {
    window.location.replace(LOGIN);
  }
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


LOGIN_COOKIE_NAMES = ("wt2", "geek_zp_token", "zp_token")


def has_user_login(cookies: list[dict]) -> bool:
    """真实用户登录（wt2 等），有薪资权限。"""
    return any(
        c.get("name") in LOGIN_COOKIE_NAMES and c.get("value")
        for c in cookies
    )


def list_response_has_salary(data: dict, sample_size: int = 10) -> bool:
    """列表 API 是否返回明文 salaryDesc。"""
    jobs = (data.get("zpData") or {}).get("jobList") or []
    for job in jobs[:sample_size]:
        if job.get("salaryDesc"):
            return True
    return False


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
    USER_LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"
    LOGIN_FALLBACK_URLS = (
        USER_LOGIN_URL,
        "https://login.zhipin.com/?ka=header-login&zpwww=1",
        "https://www.zhipin.com/",
    )
    BLANK_URLS = frozenset({"about:blank", "", "chrome://newtab/"})
    LOGIN_FLOW_MARKERS = (
        "/web/passport",
        "/web/user",
        "login.zhipin.com",
        "verify.html",
        "security-check",
        "ka=header-login",
    )
    LOGIN_BUTTON_SELECTORS = (
        "text=登录",
        "a[ka='header-login']",
        ".nav-login",
        "header a:has-text('登录')",
        ".header-login",
    )
    LOGIN_UI_SELECTORS = (
        "canvas",
        "img[alt*='码']",
        ".login-qrcode",
        ".sign-form",
        "text=扫码登录",
        "text=手机号登录",
        "text=登录/注册",
        "text=微信登录",
        ".login-container",
    )

    def __init__(self, page: Page, context: "BrowserContext"):
        self.page = page
        self.context = context
        self._security_pass_count = 0
        self._login_paused = False
        self._last_login_open = 0.0
        self._popup_handler = None
        self._blank_nav_handler = None
        self._last_security_key: str | None = None
        self._last_security_at: float = 0.0

    def set_login_paused(self, paused: bool) -> None:
        """登录等待期间暂停 security-check / API 探测，且不自动跳转页面。"""
        self._login_paused = paused

    @classmethod
    def _is_security_check_pending(cls, url: str | None) -> bool:
        if not url:
            return False
        return "_security_check" in url or "security-check.html" in url

    def ensure_stoken(self, timeout_sec: int = 60, *, for_login: bool = False) -> bool:
        """确保 __zp_stoken__ 就绪。for_login=True 时不触发跳回职位页的 security-check。"""
        if has_stoken(self.context.cookies()):
            print("  __zp_stoken__ 已就绪")
            return True

        url = self.page.url or ""
        if self._is_security_check_pending(url):
            print("检测到 security-check 校验页，切换到登录入口 ...")
            self._escape_security_check_loop()
            return has_stoken(self.context.cookies())

        if for_login:
            if self._is_blank_url(url) or not self._page_is_usable():
                self.navigate_to_login_portal()
            print("  登录前跳过 jobs security-check（登录成功后会自动补齐 stoken）")
            return has_stoken(self.context.cookies())

        print("尝试通过 security-check.html 完成验证 ...")
        self._trigger_security_via_search_probe()
        if self._wait_for_stoken(timeout_sec=20):
            print("  security-check 已通过")
            return True

        ok = has_stoken(self.context.cookies())
        if not ok:
            print("  __zp_stoken__ 仍未生成")
        return ok

    def prepare_for_login(self) -> None:
        """登录前准备：离开 security-check 循环、修复空白页、打开登录入口。"""
        url = self.page.url if self.page else ""
        if self._is_security_check_pending(url):
            self._escape_security_check_loop()
            return
        if self._is_blank_url(url) or not self._page_is_usable():
            self.navigate_to_login_portal()

    def navigate_to_login_portal(self) -> bool:
        """依次尝试多个登录入口，直到页面有内容（非 about:blank）。"""
        print("正在打开 Boss 登录页 ...")
        for url in self.LOGIN_FALLBACK_URLS:
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                human_delay(self.page, 2500, 1500)
                cur = self.page.url or ""
                if self._is_blank_url(cur):
                    continue
                if url.endswith("zhipin.com/") or url.endswith("zhipin.com"):
                    for sel in self.LOGIN_BUTTON_SELECTORS:
                        try:
                            btn = self.page.locator(sel).first
                            if btn.count() > 0 and btn.is_visible(timeout=2000):
                                btn.click()
                                human_delay(self.page, 2000, 1000)
                                break
                        except Exception:
                            continue
                if self._page_is_usable() or self._page_has_login_ui():
                    print(f"  登录页就绪: {cur[:80]}")
                    self._last_login_open = time.time()
                    return True
            except Exception as e:
                print(f"  打开 {url} 失败: {e}")
        print("  自动打开登录页失败，请手动在地址栏输入:")
        print(f"  {self.USER_LOGIN_URL}")
        return False

    def _escape_security_check_loop(self) -> None:
        """离开 jobs?_security_check 死循环。"""
        url = self.page.url or ""
        if not self._is_security_check_pending(url) and not self._is_blank_url(url):
            return
        print("  离开 security-check / 空白页 → 登录入口 ...")
        self.navigate_to_login_portal()

    @classmethod
    def _is_login_flow_url(cls, url: str | None) -> bool:
        if not url:
            return False
        return any(m in url for m in cls.LOGIN_FLOW_MARKERS)

    @staticmethod
    def _is_blank_url(url: str | None) -> bool:
        if not url:
            return True
        if url in AntiBotHandler.BLANK_URLS:
            return True
        return url.startswith("chrome://") or url.startswith("about:")

    def _page_has_login_ui(self, page: Page | None = None) -> bool:
        page = page or self.page
        if not page:
            return False
        try:
            for sel in self.LOGIN_UI_SELECTORS:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=800):
                    return True
        except Exception:
            pass
        try:
            for frame in page.frames:
                fu = frame.url or ""
                if "login.zhipin.com" in fu or "passport" in fu:
                    return True
                for sel in ("canvas", ".login-qrcode", "text=扫码登录"):
                    try:
                        if frame.locator(sel).first.count() > 0:
                            return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    def _page_is_usable(self, page: Page | None = None) -> bool:
        page = page or self.page
        if not page:
            return False
        url = page.url or ""
        if self._is_blank_url(url):
            return False
        if self._is_login_flow_url(url):
            return True
        if self._page_has_login_ui(page):
            return True
        if "zhipin.com" in url:
            try:
                length = page.evaluate(
                    "() => (document.body && document.body.innerText)"
                    " ? document.body.innerText.trim().length : 0"
                )
                return int(length or 0) > 30
            except Exception:
                return True
        return False

    def _close_blank_tabs(self, keep: Page | None = None) -> None:
        """关闭多余的 about:blank 标签，避免焦点落在空白页。"""
        keep = keep or self.page
        for p in list(self.context.pages):
            if p is keep:
                continue
            if self._is_blank_url(p.url):
                try:
                    p.close()
                except Exception:
                    pass

    def _wait_popup_ready(self, popup: Page, timeout_sec: float = 12.0) -> bool:
        """Boss 登录常先开 about:blank 再跳转，需等待真实 URL。"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            url = popup.url or ""
            if not self._is_blank_url(url) and (
                "zhipin.com" in url or self._page_has_login_ui(popup)
            ):
                return True
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=2000)
            except Exception:
                pass
            popup.wait_for_timeout(400)
        return not self._is_blank_url(popup.url or "")

    def adopt_best_page(self, prefer_login: bool = False) -> bool:
        """扫描所有标签页，切换到最有内容的一页（含登录弹窗）。"""
        if (
            self._login_paused
            and self.page
            and self._page_has_login_ui(self.page)
        ):
            self._close_blank_tabs(keep=self.page)
            return True

        candidates = list(self.context.pages)
        if not candidates:
            self.page = self.context.new_page()
            return False

        best: Page | None = None
        best_score = -1
        for p in candidates:
            url = p.url or ""
            score = 0
            if self._is_blank_url(url):
                score = 0
            elif self._page_has_login_ui(p):
                score = 100
            elif "zhipin.com" in url:
                score = 50
                if prefer_login and "login" in url:
                    score += 30
                try:
                    n = p.evaluate(
                        "() => document.body ? document.body.innerText.length : 0"
                    )
                    score += min(int(n or 0) // 100, 20)
                except Exception:
                    pass
            if score > best_score:
                best_score = score
                best = p

        if best and best_score > 0:
            self.page = best
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            self._close_blank_tabs(keep=self.page)
            return True
        return False

    def ensure_not_blank(self) -> None:
        """保证当前页不是 about:blank；登录 OAuth 进行中则不打扰。"""
        if self._login_paused:
            url = self.page.url if self.page else ""
            if self._is_login_flow_url(url) or self._page_has_login_ui():
                return
        if self.adopt_best_page(prefer_login=True):
            return
        url = self.page.url if self.page else ""
        if self._is_blank_url(url):
            try:
                self.page.goto(self.USER_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                human_delay(self.page, 1500, 800)
            except Exception:
                pass

    def wire_blank_guard(self) -> None:
        """已禁用：登录 OAuth 期间不能自动跳回职位页。"""
        return

    def unwire_blank_guard(self) -> None:
        self._blank_nav_handler = None

    def wire_login_popups(self) -> None:
        """已禁用：自动切换登录标签会打断微信扫码回调。"""
        return

    def unwire_login_popups(self) -> None:
        self._popup_handler = None

    def stabilize_login_page(self) -> None:
        """登录等待期间不主动跳转（避免打断微信 OAuth）。仅关闭多余空白标签。"""
        if not self._login_paused:
            return
        url = self.page.url if self.page else ""
        if self._is_login_flow_url(url) or self._page_has_login_ui():
            self._close_blank_tabs(keep=self.page)
            return
        if self._is_blank_url(url):
            if time.time() - self._last_login_open < 30:
                return
            print("  页面空白，重新打开登录入口（仅此一次）...")
            self.open_login_page()

    def open_login_page(self) -> None:
        """打开登录页（多入口 fallback）。"""
        print("打开登录页（请在浏览器中扫码或输入手机号）...")
        self.prepare_for_login()
        if not self._page_is_usable() and not self._page_has_login_ui():
            self.navigate_to_login_portal()
        if self._page_is_usable() or self._page_has_login_ui():
            print("  请用微信扫码；扫码后在手机点「确认登录」，并保持本标签页不动")
        self._last_login_open = time.time()

    def apply_stealth(self) -> None:
        self.context.add_init_script(STEALTH_INIT_SCRIPT)
        self.context.add_init_script(ANTI_BLANK_SCRIPT)

    def bootstrap_session(self, light: bool = False) -> None:
        """初始化会话。light=True 时不主动探测 API，避免未登录时 security-check 循环跳转。"""
        if self._login_paused:
            return
        if light:
            print("轻量初始化：打开登录入口（不打开职位页）...")
            self.prepare_for_login()
            return

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
        if self._login_paused:
            return
        probe = self.fetch_api(
            "/wapi/zpgeek/search/joblist.json",
            {"scene": "1", "query": "Java", "city": "101010100", "page": "1", "pageSize": "1"},
        )
        params = extract_security_params(probe)
        if params:
            self.run_security_check(params, callback_url="/web/geek/jobs")

    def _ensure_on_jobs_page(self) -> None:
        """采集前确保在职位页（有 Cookie 上下文）。"""
        if self._login_paused:
            return
        url = self.page.url or ""
        if self._is_login_flow_url(url) or self._is_security_check_pending(url):
            return
        if "zhipin.com/web/geek/jobs" not in url:
            try:
                self.page.goto(self.JOBS_URL, wait_until="domcontentloaded", timeout=60000)
                human_delay(self.page, 1500, 800)
            except Exception:
                pass

    def _security_key(self, params: dict) -> str:
        return f"{params.get('name')}:{params.get('ts')}"

    def _should_run_security_check(self, params: dict | None) -> bool:
        if not params:
            return False
        key = self._security_key(params)
        if key == self._last_security_key:
            if time.time() - self._last_security_at < SECURITY_CHECK_COOLDOWN_SEC:
                return False
        return True

    def _mark_security_check(self, params: dict) -> None:
        self._last_security_key = self._security_key(params)
        self._last_security_at = time.time()

    def _recover_from_block(self, last: dict, callback_url: str) -> None:
        """被拦截后恢复：已有 stoken 时优先刷新会话，避免重复 security-check 循环。"""
        sec_params = extract_security_params(last)

        if has_stoken(self.context.cookies()):
            print("  已有 stoken，刷新页面后重试（跳过重复 security-check）...")
            self._ensure_on_jobs_page()
            simulate_human_activity(self.page)
            random_delay(3.0, 2.0)
            if sec_params and self._should_run_security_check(sec_params):
                self.run_security_check(sec_params, callback_url=callback_url)
                self._mark_security_check(sec_params)
            return

        if sec_params and self._should_run_security_check(sec_params):
            self.run_security_check(sec_params, callback_url=callback_url)
            self._mark_security_check(sec_params)
        else:
            self._ensure_on_jobs_page()
            if not has_stoken(self.context.cookies()):
                self._trigger_security_via_search_probe()
        random_delay(2.5, 1.5)

    def run_security_check(self, params: dict, callback_url: str = "/web/geek/jobs") -> bool:
        """访问 security-check 页面，让浏览器 JS 生成 __zp_stoken__。"""
        if self._login_paused:
            return False
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
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            self.page.wait_for_timeout(5000)

        human_delay(self.page, 1500, 1000)
        ok = self._wait_for_stoken(timeout_sec=20)
        if ok:
            self._security_pass_count += 1
            print(f"  security-check 通过，__zp_stoken__ 已生成。")
            try:
                self.page.goto(self.JOBS_URL, wait_until="domcontentloaded", timeout=60000)
                human_delay(self.page, 2000, 1000)
            except Exception:
                pass
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
            if self._login_paused:
                return {"code": -1, "message": "login_paused", "zpData": {}}

            if "zhipin.com" not in (self.page.url or ""):
                self.page.goto(self.JOBS_URL, wait_until="domcontentloaded", timeout=60000)
                human_delay(self.page, 1000, 500)

            simulate_human_activity(self.page)
            human_delay(self.page, 400, 800)

            last = self.fetch_api(api_path, params)
            if not is_blocked_response(last):
                return last

            code = last.get("code")
            print(f"  反爬拦截 code={code}，第 {attempt}/{max_retries} 次重试 ...")
            self._recover_from_block(last, callback_url=callback_url)

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
                data = captured["data"]
                if not is_blocked_response(data):
                    return data
                captured.clear()

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
            self._recover_from_block(last, callback_url="/web/geek/jobs")
            random_delay(2.5, 1.5)
        return last
