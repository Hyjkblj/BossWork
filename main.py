"""Boss 直聘计算机类岗位数据采集 CLI"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from config import (
    CITIES,
    COMPUTER_KEYWORDS,
    DEFAULT_CITY,
    DEFAULT_FETCH_MODE,
    DEFAULT_KEYWORDS,
    MAX_PAGES_PER_KEYWORD,
)
from scraper import BossZhipinScraper, save_results
from skill_analyzer import aggregate_skill_stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boss 直聘计算机类岗位数据采集")
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=DEFAULT_KEYWORDS,
        help=f"搜索关键词，默认: {', '.join(DEFAULT_KEYWORDS)}",
    )
    parser.add_argument(
        "--all-keywords",
        action="store_true",
        help="使用预置的全部计算机类关键词",
    )
    parser.add_argument(
        "--city",
        default=DEFAULT_CITY,
        choices=list(CITIES.keys()),
        help=f"目标城市，默认: {DEFAULT_CITY}",
    )
    parser.add_argument(
        "--cities",
        nargs="+",
        choices=list(CITIES.keys()),
        help="多城市采集，如: --cities 广州 深圳（优先于 --city）",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=MAX_PAGES_PER_KEYWORD,
        help=f"每个关键词最多抓取页数，默认: {MAX_PAGES_PER_KEYWORD}",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="不抓取岗位详情（更快，但技能信息可能不完整）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式运行浏览器",
    )
    parser.add_argument(
        "--login-wait",
        type=int,
        default=90,
        help="等待手动登录的秒数，默认 90",
    )
    parser.add_argument(
        "--profile",
        default=".browser_profile",
        help="浏览器持久化 profile 目录，用于保存登录态",
    )
    parser.add_argument(
        "--no-profile",
        action="store_true",
        help="不使用持久化 profile（避免 profile 被占用）",
    )
    parser.add_argument(
        "--output",
        default="output",
        help="输出目录，默认 output",
    )
    parser.add_argument(
        "--skip-login-wait",
        action="store_true",
        help="跳过登录等待（profile 已有登录态时使用）",
    )
    parser.add_argument(
        "--browser",
        default="chrome",
        choices=["chrome", "msedge", "chromium"],
        help="浏览器类型，默认 chrome（本机 Google Chrome，无需额外下载）",
    )
    parser.add_argument(
        "--mode",
        default=DEFAULT_FETCH_MODE,
        choices=["ui", "api"],
        help="采集模式：ui=模拟用户搜索(更稳), api=直接调接口(更快)",
    )
    return parser


def print_skill_summary(jobs: list[dict], top_n: int = 30) -> None:
    stats = aggregate_skill_stats(jobs)
    print(f"\n{'=' * 50}")
    print(f"技能统计 Top {top_n}（共 {len(jobs)} 个岗位）")
    print(f"{'=' * 50}")
    for skill, count in stats.most_common(top_n):
        pct = count / len(jobs) * 100 if jobs else 0
        print(f"  {skill:20s} {count:4d} ({pct:.1f}%)")


def data_quality_report(jobs: list[dict]) -> dict:
    total = len(jobs)
    if total == 0:
        return {"total": 0, "salary_rate": 0.0, "skill_rate": 0.0, "is_real": False}
    salary_ok = sum(1 for j in jobs if j.get("salary"))
    skill_ok = sum(1 for j in jobs if j.get("extracted_skills"))
    desc_ok = sum(1 for j in jobs if j.get("description"))
    salary_rate = salary_ok / total
    return {
        "total": total,
        "salary_ok": salary_ok,
        "skill_ok": skill_ok,
        "desc_ok": desc_ok,
        "salary_rate": salary_rate,
        "skill_rate": skill_ok / total,
        "is_real": total >= 10 and salary_rate >= 0.6,
    }


def print_quality(report: dict) -> None:
    print(f"\n数据质量: 共 {report['total']} 条")
    print(f"  薪资有效: {report.get('salary_ok', 0)} ({report['salary_rate']*100:.1f}%)")
    print(f"  技能有效: {report.get('skill_ok', 0)} ({report['skill_rate']*100:.1f}%)")
    print(f"  描述有效: {report.get('desc_ok', 0)}")
    if report["is_real"]:
        print("  判定: 真实数据 ✓")
    else:
        print("  判定: 数据不完整（需登录或重试）")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    keywords = COMPUTER_KEYWORDS if args.all_keywords else args.keywords
    cities = args.cities if args.cities else [args.city]
    profile_dir = None if args.no_profile else str(Path(args.profile).resolve())

    print("Boss 直聘岗位数据采集")
    print(f"  城市: {', '.join(cities)}")
    print(f"  关键词: {', '.join(keywords)}")
    print(f"  每词页数: {args.pages}")
    print(f"  抓取详情: {not args.no_detail}")
    print(f"  浏览器: {args.browser}")
    print(f"  采集模式: {args.mode}")
    print(f"  Profile: {profile_dir or '无（临时会话）'}")

    browser_channel = None if args.browser == "chromium" else args.browser
    with BossZhipinScraper(
        headless=args.headless,
        user_data_dir=profile_dir,
        login_wait_sec=args.login_wait,
        browser_channel=browser_channel,
        fetch_mode=args.mode,
    ) as scraper:
        if not args.skip_login_wait:
            scraper.wait_for_login(timeout_sec=args.login_wait)

        all_jobs: list[dict] = []
        for city in cities:
            print(f"\n{'='*50}\n开始采集城市: {city}\n{'='*50}")
            city_jobs = scraper.collect_jobs(
                keywords=keywords,
                city=city,
                max_pages=args.pages,
                fetch_detail=not args.no_detail,
            )
            for job in city_jobs:
                job["target_city"] = city
            all_jobs.extend(city_jobs)

        jobs = all_jobs

    if not jobs:
        print("\n未采集到任何岗位数据。请检查是否已登录，或尝试更换城市/关键词。")
        return

    json_path, csv_path = save_results(jobs, args.output)
    print(f"\n采集完成，共 {len(jobs)} 条岗位")
    print(f"  JSON: {json_path}")
    if csv_path.suffix == ".csv":
        print(f"  CSV:  {csv_path}")

    print_skill_summary(jobs)
    report = data_quality_report(jobs)
    print_quality(report)

    summary_path = Path(args.output) / "skill_summary.json"
    stats = aggregate_skill_stats(jobs)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(dict(stats.most_common()), f, ensure_ascii=False, indent=2)
    print(f"  技能统计: {summary_path}")


if __name__ == "__main__":
    main()
