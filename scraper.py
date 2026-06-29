"""Boss 直聘岗位数据采集（Playwright + 反爬应对）"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

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
from config import CITIES, MAX_RETRIES, REQUEST_DELAY_JITTER, REQUEST_DELAY_SEC
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
    ):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.login_wait_sec = login_wait_sec
        self.browser_channel = browser_channel
        self.fetch_mode = fetch_mode
        self.max_retries = max_retries
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
            ],
        }
        if self.browser_channel:
            kwargs["channel"] = self.browser_channel
        return kwargs

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

        self.anti_bot = AntiBotHandler(self.page, self._context)
        self.anti_bot.apply_stealth()
        self.anti_bot.bootstrap_session()

        cookies = self._context.cookies()
        stoken_ok = has_stoken(cookies)
        login_ok = has_user_login(cookies)
        print(f"会话状态: __zp_stoken__={'有' if stoken_ok else '无'}, 用户登录={'是' if login_ok else '否（薪资可能不可见）'}")

    def wait_for_login(self, timeout_sec: int | None = None) -> bool:
        timeout = timeout_sec or self.login_wait_sec
        print(f"请在浏览器中登录 Boss 直聘（扫码/手机号，最多等待 {timeout} 秒）...")
        print("  提示: 仅 security-check 通过不够，必须完成账号登录才能看到薪资。")

        # 打开登录页方便用户操作
        try:
            self.page.goto(
                "https://www.zhipin.com/web/user/?ka=header-login",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception:
            pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            cookies = self._context.cookies() if self._context else []
            if has_user_login(cookies):
                print("检测到账号登录（wt2），可获取薪资数据。")
                if not has_stoken(cookies):
                    self.anti_bot.bootstrap_session()
                return True
            self.page.wait_for_timeout(2000)

        if has_stoken(self._context.cookies() if self._context else []):
            print("仅有 __zp_stoken__，未检测到账号登录，薪资可能为空。")
        else:
            print("未检测到有效会话。")
        return False

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
            "security_id": job.get("securityId"),
            "boss_id": boss_id,
            "boss_name": merged.get("bossName") or boss_detail.get("name"),
            "boss_title": merged.get("bossTitle") or boss_detail.get("title"),
            "boss_avatar": boss_detail.get("large") or merged.get("bossAvatar"),
            "job_name": merged.get("jobName"),
            "salary": merged.get("salaryDesc") or "",
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
    ) -> list[dict]:
        city_code = CITIES.get(city, city)
        if not city_code.isdigit():
            raise ValueError(f"未知城市: {city}，可选: {', '.join(CITIES)}")

        all_jobs: list[dict] = []
        seen_ids: set[str] = set()
        blocked_count = 0

        for ki, keyword in enumerate(keywords):
            print(f"\n搜索关键词: {keyword} | 城市: {city} ({city_code})")
            if ki > 0:
                random_delay(delay_sec, REQUEST_DELAY_JITTER)

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

                for job in job_list:
                    job_id = job.get("encryptJobId")
                    if job_id and job_id in seen_ids:
                        continue
                    if job_id:
                        seen_ids.add(job_id)

                    detail = None
                    if fetch_detail and job.get("securityId"):
                        random_delay(delay_sec, REQUEST_DELAY_JITTER)
                        detail = self.get_job_detail(job["securityId"])
                        if is_blocked_response(detail):
                            detail = None

                    normalized = self._normalize_job(job, keyword, detail)
                    all_jobs.append(normalized)

                has_more = zp_data.get("hasMore", False)
                if not has_more:
                    break
                random_delay(delay_sec, REQUEST_DELAY_JITTER)

        return all_jobs

    def enrich_salaries(self, jobs: list[dict], delay_sec: float = REQUEST_DELAY_SEC) -> list[dict]:
        """登录后补抓缺失薪资（详情 API）。"""
        if not has_user_login(self._context.cookies() if self._context else []):
            return jobs
        missing = [j for j in jobs if not j.get("salary") and j.get("security_id")]
        if not missing:
            return jobs
        print(f"\n补抓薪资: {len(missing)} 条岗位缺少薪资 ...")
        for i, job in enumerate(missing):
            if i > 0 and i % 10 == 0:
                print(f"  进度 {i}/{len(missing)}")
            detail = self.get_job_detail(job["security_id"])
            if detail.get("code") == 0:
                info = (detail.get("zpData") or {}).get("jobInfo") or {}
                sal = info.get("salaryDesc")
                if sal:
                    job["salary"] = sal
            random_delay(delay_sec * 0.6, REQUEST_DELAY_JITTER * 0.5)
        return jobs

    def enrich_hr_info(self, jobs: list[dict], delay_sec: float = REQUEST_DELAY_SEC) -> list[dict]:
        """登录后补抓缺失 boss_id（详情 API）。"""
        if not has_user_login(self._context.cookies() if self._context else []):
            return jobs
        missing = [j for j in jobs if not j.get("boss_id") and j.get("security_id")]
        if not missing:
            return jobs
        print(f"\n补抓 HR 信息: {len(missing)} 条岗位缺少 boss_id ...")
        for i, job in enumerate(missing):
            if i > 0 and i % 10 == 0:
                print(f"  进度 {i}/{len(missing)}")
            detail = self.get_job_detail(job["security_id"])
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
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()


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
