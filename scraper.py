"""Boss 直聘岗位数据采集（Playwright + 反爬应对）"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from anti_bot import (
    AntiBotHandler,
    has_login,
    has_stoken,
    has_user_login,
    human_delay,
    is_blocked_response,
    random_delay,
    simulate_human_activity,
)
from config import CITIES, DETAIL_FAIL_STREAK_LIMIT, MAX_RETRIES, REQUEST_DELAY_JITTER, REQUEST_DELAY_SEC, SECURITY_CHECK_COOLDOWN_SEC
from skill_analyzer import extract_skills_from_job


class BossZhipinScraper:
    """通过浏览器会话调用 Boss 直聘内部 API 采集岗位数据。"""

    SEARCH_API = "/wapi/zpgeek/search/joblist.json"
    DETAIL_API = "/wapi/zpgeek/job/detail.json"

    def __init__(
        self,
        headless: bool = False,
        user_data_dir: str | None = None,
        login_wait_sec: int = 60,
        browser_channel: str | None = "chrome",
        fetch_mode: str = "ui",
        max_retries: int = MAX_RETRIES,
        defer_bootstrap: bool = False,
    ):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.login_wait_sec = login_wait_sec
        self.browser_channel = browser_channel
        self.fetch_mode = fetch_mode
        self.max_retries = max_retries
        self.defer_bootstrap = defer_bootstrap
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        self.anti_bot: AntiBotHandler | None = None

    def __enter__(self) -> "BossZhipinScraper":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _launch_kwargs(self) -> dict:
        kwargs: dict = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-popup-blocking",
            ],
        }
        if self.browser_channel:
            kwargs["channel"] = self.browser_channel
        return kwargs

    def _prepare_browser_pages(self) -> None:
        """启动后立即离开 about:blank，打开登录页。"""
        if not self._context:
            return
        from anti_bot import AntiBotHandler

        if not self.page:
            self.page = self._context.new_page()

        if AntiBotHandler._is_blank_url(self.page.url):
            try:
                self.page.goto(
                    AntiBotHandler.USER_LOGIN_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
            except Exception:
                pass

        for p in list(self._context.pages):
            if p is self.page:
                continue
            if AntiBotHandler._is_blank_url(p.url):
                try:
                    p.close()
                except Exception:
                    pass

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        launch_kwargs = self._launch_kwargs()
        browser_label = "Google Chrome" if self.browser_channel == "chrome" else (
            self.browser_channel or "Chromium"
        )
        print(f"启动浏览器: {browser_label}")

        if self.user_data_dir:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                **launch_kwargs,
            )
            self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            self._browser = self._playwright.chromium.launch(**launch_kwargs)
            self._context = self._browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            self.page = self._context.new_page()

        self._prepare_browser_pages()

        self.anti_bot = AntiBotHandler(self.page, self._context)
        self.anti_bot.apply_stealth()
        if AntiBotHandler._is_blank_url(self.page.url if self.page else ""):
            self.anti_bot.navigate_to_login_portal()
        if self.defer_bootstrap:
            self.anti_bot.bootstrap_session(light=True)
        else:
            self.anti_bot.bootstrap_session(light=False)

        cookies = self._context.cookies()
        stoken_ok = has_stoken(cookies)
        login_ok = has_user_login(cookies)
        print(f"会话状态: __zp_stoken__={'有' if stoken_ok else '无'}, 用户登录={'是' if login_ok else '否（薪资可能不可见）'}")

    def wait_for_login(self, timeout_sec: int | None = None) -> bool:
        timeout = timeout_sec or self.login_wait_sec
        print(f"请在浏览器中登录 Boss 直聘（扫码/手机号，最多等待 {timeout} 秒）...")

        print("\n步骤 1/2: 准备登录页（修复空白 / security-check）...")
        self.anti_bot.ensure_stoken(timeout_sec=30, for_login=True)
        self.anti_bot.prepare_for_login()

        print("步骤 2/2: 打开登录页...")
        print("  【重要】微信扫码后在手机点「确认登录」，并保持浏览器标签页不动")
        self.anti_bot.set_login_paused(True)
        self.anti_bot.open_login_page()

        deadline = time.time() + timeout
        last_hint = 0.0
        last_api_check = 0.0
        while time.time() < deadline:
            cookies = self._context.cookies() if self._context else []
            if has_user_login(cookies):
                print("检测到账号登录（wt2），初始化采集会话 ...")
                self.anti_bot.set_login_paused(False)
                self.anti_bot.bootstrap_session(light=False)
                self.verify_salary_access()
                return True

            now = time.time()
            if now - last_api_check >= 5:
                last_api_check = now
                if self._check_login_via_api():
                    print("检测到账号登录（API），初始化采集会话 ...")
                    self.anti_bot.set_login_paused(False)
                    self.anti_bot.bootstrap_session(light=False)
                    self.verify_salary_access()
                    return True

            if now - last_hint >= 20:
                url = (self.page.url or "")[:90]
                if self.anti_bot._is_blank_url(url):
                    flow = "（空白页，正在重新打开登录页）"
                    self.anti_bot.navigate_to_login_portal()
                elif self.anti_bot._is_security_check_pending(url):
                    flow = "（security-check，切换到登录页）"
                    self.anti_bot._escape_security_check_loop()
                elif self.anti_bot._is_login_flow_url(url):
                    flow = "（登录流程页，请完成扫码）"
                else:
                    flow = ""
                print(f"  等待登录中... 当前页: {url}{flow}")
                last_hint = now

            self.page.wait_for_timeout(2000)

        self.anti_bot.set_login_paused(False)
        if has_stoken(self._context.cookies() if self._context else []):
            print("仅有 __zp_stoken__，未检测到账号登录，薪资可能为空。")
            print("  若已扫码但无反应：请确认 Boss 小程序/APP 上点了「确认登录」，并保持浏览器标签页未关闭")
        else:
            print("未检测到有效会话。")
        return False

    def _check_login_via_api(self) -> bool:
        """备用：通过用户信息 API 判断登录（wt2 Cookie 写入稍有延迟时）。"""
        if not self.anti_bot or not self.page:
            return False
        if self.anti_bot._is_blank_url(self.page.url or ""):
            return False
        try:
            data = self.anti_bot.fetch_api("/wapi/zpgeek/common/data.json", {})
            if data.get("code") == 0:
                zp = data.get("zpData") or {}
                if zp.get("uid") or zp.get("encryptUserId"):
                    return True
        except Exception:
            pass
        return False

    def verify_salary_access(self, city_code: str = "101280600") -> bool:
        """登录后探测列表 API 是否返回明文 salaryDesc。"""
        print("验证薪资权限（探测列表 API）...")
        result = self.search_jobs("Java开发", city_code, page=1)
        if is_blocked_response(result):
            print(f"  列表 API 被拦截 code={result.get('code')}，采集时可能仍无薪资")
            return False
        jobs = (result.get("zpData") or {}).get("jobList") or []
        if not jobs:
            print("  列表 API 无岗位数据")
            return False
        sal_samples = [j.get("salaryDesc") for j in jobs[:10] if j.get("salaryDesc")]
        sid_count = sum(1 for j in jobs[:10] if j.get("securityId"))
        if sal_samples:
            print(f"  列表含明文薪资 ✓（样例: {sal_samples[0]}）")
            return True
        print(f"  列表未返回 salaryDesc（securityId {sid_count}/10 条）")
        print("  将尝试通过详情 API 补抓；若仍失败请确认登录的是求职者账号")
        return False

    def resolve_security_id(self, job_id: str, timeout_ms: int = 20000) -> str | None:
        """打开岗位详情页，从网络请求解析 securityId。"""
        if not self.page:
            return None

        url = f"https://www.zhipin.com/job_detail/{job_id}.html"
        captured: dict[str, Any] = {}

        def on_response(response):
            if "job/detail.json" not in response.url or response.status != 200:
                return
            try:
                m = re.search(r"securityId=([^&]+)", response.url)
                if m:
                    captured["security_id"] = unquote(m.group(1))
                captured["data"] = response.json()
            except Exception:
                pass

        self.page.on("response", on_response)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            human_delay(self.page, 2000, 1000)
            deadline = time.time() + timeout_ms / 1000
            while time.time() < deadline:
                if captured.get("security_id"):
                    return captured["security_id"]
                if captured.get("data"):
                    break
                self.page.wait_for_timeout(300)

            sid = self.page.evaluate("""
            () => {
                const entries = performance.getEntriesByType('resource');
                for (let i = entries.length - 1; i >= 0; i--) {
                    const u = entries[i].name;
                    if (!u.includes('job/detail.json')) continue;
                    const m = u.match(/securityId=([^&]+)/);
                    if (m) return decodeURIComponent(m[1]);
                }
                return null;
            }
            """)
            return sid
        except Exception:
            return None
        finally:
            self.page.remove_listener("response", on_response)

    def search_jobs(
        self,
        keyword: str,
        city_code: str,
        page: int = 1,
        page_size: int = 30,
    ) -> dict:
        params = {
            "scene": "1",
            "query": keyword,
            "city": city_code,
            "page": str(page),
            "pageSize": str(page_size),
        }
        callback = f"/web/geek/jobs?city={city_code}&query={keyword}"

        if self.fetch_mode == "ui":
            result = self.anti_bot.search_via_ui(keyword, city_code, page_num=page)
            if result and not is_blocked_response(result):
                return result

        return self.anti_bot.fetch_with_retry(
            self.SEARCH_API,
            params,
            max_retries=self.max_retries,
            callback_url=callback,
        )

    def get_job_detail(self, security_id: str) -> dict:
        return self.anti_bot.fetch_with_retry(
            self.DETAIL_API,
            {"securityId": security_id},
            max_retries=self.max_retries,
        )

    def _normalize_job(self, job: dict, keyword: str, detail: dict | None = None) -> dict:
        detail_info = {}
        boss_detail = {}
        if detail and detail.get("code") == 0:
            zp = detail.get("zpData") or {}
            detail_info = zp.get("jobInfo") or {}
            boss_detail = zp.get("bossInfo") or {}

        merged = {**job, **detail_info}
        skills = extract_skills_from_job(merged)

        boss_id = (
            boss_detail.get("encryptBossId")
            or boss_detail.get("bossId")
            or job.get("encryptBossId")
        )

        return {
            "job_id": job.get("encryptJobId") or detail_info.get("encryptId"),
            "security_id": job.get("securityId") or job.get("security_id"),
            "lid": job.get("lid"),
            "boss_id": boss_id,
            "boss_name": merged.get("bossName") or boss_detail.get("name"),
            "boss_title": merged.get("bossTitle") or boss_detail.get("title"),
            "boss_avatar": boss_detail.get("large") or merged.get("bossAvatar"),
            "job_name": merged.get("jobName"),
            "salary": merged.get("salaryDesc") or merged.get("salary") or "",
            "experience": merged.get("jobExperience") or merged.get("experienceName"),
            "degree": merged.get("jobDegree") or merged.get("degreeName"),
            "city": merged.get("cityName"),
            "district": merged.get("areaDistrict"),
            "business_district": merged.get("businessDistrict"),
            "company": merged.get("brandName"),
            "company_industry": merged.get("brandIndustry") or merged.get("industryName"),
            "company_scale": merged.get("brandScaleName") or merged.get("scaleName"),
            "company_stage": merged.get("brandStageName") or merged.get("stageName"),
            "job_labels": merged.get("jobLabels") or [],
            "skills": merged.get("skills") or merged.get("showSkills") or [],
            "extracted_skills": skills,
            "description": (merged.get("postDescription") or "")[:2000],
            "search_keyword": keyword,
            "job_url": (
                f"https://www.zhipin.com/job_detail/{job.get('encryptJobId')}.html"
                if job.get("encryptJobId")
                else None
            ),
        }

    def collect_jobs(
        self,
        keywords: list[str],
        city: str = "北京",
        max_pages: int = 3,
        fetch_detail: bool = True,
        delay_sec: float = REQUEST_DELAY_SEC,
        checkpoint_path: Path | str | None = None,
    ) -> list[dict]:
        city_code = CITIES.get(city, city)
        if not city_code.isdigit():
            raise ValueError(f"未知城市: {city}，可选: {', '.join(CITIES)}")

        all_jobs: list[dict] = []
        seen_ids: set[str] = set()
        blocked_count = 0
        detail_fail_streak = 0
        skip_detail = False
        ckpt = Path(checkpoint_path) if checkpoint_path else None

        for ki, keyword in enumerate(keywords):
            print(f"\n搜索关键词: {keyword} | 城市: {city} ({city_code})", flush=True)
            if ki > 0:
                random_delay(delay_sec, REQUEST_DELAY_JITTER)

            self.anti_bot._ensure_on_jobs_page()

            for page in range(1, max_pages + 1):
                print(f"  第 {page} 页...", end=" ", flush=True)
                simulate_human_activity(self.page)
                result = self.search_jobs(keyword, city_code, page=page)

                if is_blocked_response(result):
                    code = result.get("code")
                    msg = result.get("message", "")
                    print(f"被拦截 code={code} msg={msg}")
                    blocked_count += 1
                    if page > 1:
                        print("  翻页失败，跳过该关键词后续页。")
                        break
                    if blocked_count >= 3:
                        print("连续多次被拦截，暂停采集。请登录账号或降低频率后重试。")
                        return all_jobs
                    random_delay(5.0, 3.0)
                    continue

                blocked_count = 0
                zp_data = result.get("zpData") or {}
                job_list = zp_data.get("jobList") or []
                print(f"获取 {len(job_list)} 条")

                if not job_list:
                    break

                page_jobs: list[dict] = []
                for job in job_list:
                    job_id = job.get("encryptJobId")
                    if job_id and job_id in seen_ids:
                        continue
                    if job_id:
                        seen_ids.add(job_id)

                    detail = None
                    if fetch_detail and not skip_detail and job.get("securityId"):
                        random_delay(delay_sec * 0.8, REQUEST_DELAY_JITTER * 0.5)
                        detail = self.get_job_detail(job["securityId"])
                        if is_blocked_response(detail) or detail.get("code") != 0:
                            detail_fail_streak += 1
                            detail = None
                            if detail_fail_streak >= DETAIL_FAIL_STREAK_LIMIT:
                                skip_detail = True
                                print(
                                    f"\n  详情 API 连续失败 {detail_fail_streak} 次，"
                                    f"本批改用列表数据（更稳）",
                                    flush=True,
                                )
                        else:
                            detail_fail_streak = 0

                    normalized = self._normalize_job(job, keyword, detail)
                    page_jobs.append(normalized)

                all_jobs.extend(page_jobs)
                if ckpt and page_jobs:
                    save_checkpoint(all_jobs, ckpt)
                    print(f"  [已保存 checkpoint: {len(all_jobs)} 条]", flush=True)

                has_more = zp_data.get("hasMore", False)
                if not has_more:
                    break
                random_delay(delay_sec, REQUEST_DELAY_JITTER)

        return all_jobs

    def enrich_salaries(self, jobs: list[dict], delay_sec: float = REQUEST_DELAY_SEC) -> list[dict]:
        """登录后补抓缺失薪资（详情 API；无 security_id 时从详情页解析）。"""
        if not has_user_login(self._context.cookies() if self._context else []):
            print("未检测到账号登录，跳过薪资补抓（仅 __zp_stoken__ 不足以获取薪资）")
            return jobs

        missing = [j for j in jobs if not (j.get("salary") or "").strip()]
        if not missing:
            print("所有岗位已有薪资字段")
            return jobs

        no_sid = sum(1 for j in missing if not j.get("security_id"))
        print(f"\n补抓薪资: {len(missing)} 条缺少薪资（{no_sid} 条无 security_id，将从详情页解析）...")
        ok = 0
        for i, job in enumerate(missing):
            if i > 0 and i % 10 == 0:
                print(f"  进度 {i}/{len(missing)}，已成功 {ok} 条")

            sid = job.get("security_id")
            if not sid and job.get("job_id"):
                sid = self.resolve_security_id(job["job_id"])
                if sid:
                    job["security_id"] = sid

            if not sid:
                continue

            detail = self.get_job_detail(sid)
            if detail.get("code") == 0:
                zp = detail.get("zpData") or {}
                info = zp.get("jobInfo") or {}
                sal = info.get("salaryDesc") or info.get("salary")
                if sal:
                    job["salary"] = sal
                    ok += 1
                if not job.get("description") and info.get("postDescription"):
                    job["description"] = (info.get("postDescription") or "")[:2000]
                if not job.get("boss_id"):
                    boss = zp.get("bossInfo") or {}
                    bid = boss.get("encryptBossId") or boss.get("bossId")
                    if bid:
                        job["boss_id"] = bid
            random_delay(delay_sec * 0.6, REQUEST_DELAY_JITTER * 0.5)

        print(f"薪资补抓完成: {ok}/{len(missing)} 条成功")
        return jobs

    def enrich_hr_info(self, jobs: list[dict], delay_sec: float = REQUEST_DELAY_SEC) -> list[dict]:
        """登录后补抓缺失 boss_id（详情 API）。"""
        if not has_user_login(self._context.cookies() if self._context else []):
            return jobs
        missing = [j for j in jobs if not j.get("boss_id")]
        if not missing:
            return jobs
        no_sid = sum(1 for j in missing if not j.get("security_id"))
        print(f"\n补抓 HR 信息: {len(missing)} 条缺少 boss_id（{no_sid} 条无 security_id）...")
        for i, job in enumerate(missing):
            if i > 0 and i % 10 == 0:
                print(f"  进度 {i}/{len(missing)}")

            sid = job.get("security_id")
            if not sid and job.get("job_id"):
                sid = self.resolve_security_id(job["job_id"])
                if sid:
                    job["security_id"] = sid
            if not sid:
                continue

            detail = self.get_job_detail(sid)
            if detail.get("code") == 0:
                boss = (detail.get("zpData") or {}).get("bossInfo") or {}
                bid = boss.get("encryptBossId") or boss.get("bossId")
                if bid:
                    job["boss_id"] = bid
                if boss.get("name"):
                    job["boss_name"] = boss.get("name")
                if boss.get("title"):
                    job["boss_title"] = boss.get("title")
                if boss.get("large"):
                    job["boss_avatar"] = boss.get("large")
            random_delay(delay_sec * 0.6, REQUEST_DELAY_JITTER * 0.5)
        return jobs

    def close(self) -> None:
        if self.anti_bot:
            self.anti_bot.unwire_login_popups()
            self.anti_bot.unwire_blank_guard()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()


def save_checkpoint(jobs: list[dict], path: Path) -> None:
    """每页采集后写入 checkpoint，中断不丢数据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def save_results(jobs: list[dict], output_dir: str | Path = "output") -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_path / f"boss_jobs_{timestamp}.json"
    csv_path = output_path / f"boss_jobs_{timestamp}.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)

    try:
        import pandas as pd

        rows = []
        for job in jobs:
            row = {**job}
            row["job_labels"] = ", ".join(job.get("job_labels") or [])
            row["skills"] = ", ".join(job.get("skills") or [])
            row["extracted_skills"] = ", ".join(job.get("extracted_skills") or [])
            rows.append(row)
        pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    except ImportError:
        csv_path = json_path

    return json_path, csv_path
