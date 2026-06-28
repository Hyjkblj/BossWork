"""循环采集直到获得含薪资的真实数据（广州 + 深圳）"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from config import DEFAULT_KEYWORDS
from main import data_quality_report, print_quality, print_skill_summary
from anti_bot import has_user_login
from scraper import BossZhipinScraper, save_results
from skill_analyzer import aggregate_skill_stats

TARGET_CITIES = ["广州", "深圳"]
KEYWORDS = DEFAULT_KEYWORDS
MAX_PAGES = 2
MAX_ATTEMPTS = 2
LOGIN_WAIT_SEC = 300
PROFILE = ".boss_profile"


def main() -> int:
    profile_dir = str(Path(PROFILE).resolve())
    all_jobs: list[dict] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n{'#'*60}")
        print(f"第 {attempt}/{MAX_ATTEMPTS} 轮采集 | 城市: {', '.join(TARGET_CITIES)}")
        print(f"{'#'*60}")

        if attempt == 1:
            print("\n>>> 请在弹出的 Chrome 窗口中登录 Boss 直聘（扫码或手机号）<<<\n")

        with BossZhipinScraper(
            headless=False,
            user_data_dir=profile_dir,
            login_wait_sec=LOGIN_WAIT_SEC,
            browser_channel="chrome",
            fetch_mode="ui",
        ) as scraper:
            scraper.wait_for_login(timeout_sec=LOGIN_WAIT_SEC)

            round_jobs: list[dict] = []
            for city in TARGET_CITIES:
                print(f"\n{'='*50}\n采集城市: {city}\n{'='*50}")
                jobs = scraper.collect_jobs(
                    keywords=KEYWORDS,
                    city=city,
                    max_pages=MAX_PAGES,
                    fetch_detail=False,
                )
                for job in jobs:
                    job["target_city"] = city
                round_jobs.extend(jobs)

            if round_jobs and has_user_login(scraper._context.cookies()):
                round_jobs = scraper.enrich_salaries(round_jobs)

        report = data_quality_report(round_jobs)
        print_quality(report)

        if report["is_real"]:
            all_jobs = round_jobs
            break

        all_jobs = round_jobs if round_jobs else all_jobs
        if attempt < MAX_ATTEMPTS:
            print(f"\n数据未达标，{30} 秒后重试（请确认已登录 Boss 直聘）...")
            time.sleep(30)

    if not all_jobs:
        print("\n未能采集到任何数据。请手动登录后重试。")
        return 1

    report = data_quality_report(all_jobs)
    json_path, csv_path = save_results(all_jobs, "output")
    print(f"\n最终输出 {report['total']} 条岗位")
    print(f"  JSON: {json_path}")
    if csv_path.suffix == ".csv":
        print(f"  CSV:  {csv_path}")

    print_skill_summary(all_jobs)

    summary_path = Path("output") / "skill_summary_gz_sz.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        import json
        json.dump(dict(aggregate_skill_stats(all_jobs).most_common()), f, ensure_ascii=False, indent=2)
    print(f"  技能统计: {summary_path}")

    if not report["is_real"]:
        print("\n警告: 薪资数据仍不完整，可能未登录。请在 Chrome 窗口登录后重新运行:")
        print("  python run_until_real.py")
        return 2

    print("\n采集成功，已获取广州 + 深圳真实岗位数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
