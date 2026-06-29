"""对已有岗位 JSON 补抓薪资（需登录 Boss 求职者账号）"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anti_bot import has_user_login
from main import data_quality_report, print_quality
from scraper import BossZhipinScraper, save_results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="登录后补抓已有岗位 JSON 的薪资字段")
    p.add_argument("input", help="岗位 JSON 文件路径")
    p.add_argument("--profile", default=".browser_profile", help="浏览器 profile 目录")
    p.add_argument("--login-wait", type=int, default=120, help="等待登录秒数")
    p.add_argument("--output", default="output", help="输出目录")
    p.add_argument(
        "--in-place",
        action="store_true",
        help="覆盖原文件（默认写入 output/ 新文件）",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"文件不存在: {input_path}")
        return 1

    jobs = json.loads(input_path.read_text(encoding="utf-8"))
    missing_before = sum(1 for j in jobs if not (j.get("salary") or "").strip())
    print(f"读取 {len(jobs)} 条岗位，{missing_before} 条缺少薪资")

    profile_dir = str(Path(args.profile).resolve())
    with BossZhipinScraper(
        headless=False,
        user_data_dir=profile_dir,
        browser_channel="chrome",
        fetch_mode="ui",
        defer_bootstrap=True,
    ) as scraper:
        scraper.wait_for_login(timeout_sec=args.login_wait)
        if not has_user_login(scraper._context.cookies()):
            print("未检测到账号登录，无法补抓薪资")
            return 2
        jobs = scraper.enrich_salaries(jobs)

    report = data_quality_report(jobs)
    print_quality(report)

    if args.in_place:
        input_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已覆盖: {input_path}")
    else:
        json_path, csv_path = save_results(jobs, args.output)
        print(f"\n已保存: {json_path}")
        if csv_path.suffix == ".csv":
            print(f"  CSV: {csv_path}")

    return 0 if report["salary_rate"] >= 0.6 else 2


if __name__ == "__main__":
    raise SystemExit(main())
