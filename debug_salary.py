"""快速诊断：检查登录态、securityId 与 API 返回的薪资字段"""

import json
from pathlib import Path

from anti_bot import has_stoken, has_user_login
from scraper import BossZhipinScraper

PROFILE = str(Path(".browser_profile").resolve())

with BossZhipinScraper(
    headless=False,
    user_data_dir=PROFILE,
    browser_channel="chrome",
    fetch_mode="ui",
    defer_bootstrap=True,
) as scraper:
    scraper.wait_for_login(timeout_sec=120)
    cookies = scraper._context.cookies()
    login_names = [c["name"] for c in cookies if c["name"] in (
        "wt2", "geek_zp_token", "zp_token", "__zp_stoken__", "bst"
    )]
    print("登录相关 Cookie:", login_names)
    print("用户登录:", has_user_login(cookies), "| stoken:", has_stoken(cookies))
    scraper.verify_salary_access()

    for city, code, label in [("深圳", "101280600", "深圳"), ("广州", "101280100", "广州")]:
        result = scraper.search_jobs("Java开发", code, page=1)
        jobs = (result.get("zpData") or {}).get("jobList") or []
        print(f"\n{label} Java开发 首条岗位字段:")
        if jobs:
            j = jobs[0]
            print(f"  jobName={j.get('jobName')}")
            print(f"  salaryDesc={j.get('salaryDesc')!r}")
            print(f"  securityId={'有' if j.get('securityId') else '无'}")
            print(f"  skills={j.get('skills')}")
        else:
            print("  无数据", result.get("code"), result.get("message"))

    # 测试详情页解析 securityId
    if jobs:
        jid = jobs[0].get("encryptJobId")
        if jid:
            sid = scraper.resolve_security_id(jid)
            print(f"\n详情页解析 securityId: {sid or '失败'}")
            if sid:
                detail = scraper.get_job_detail(sid)
                if detail.get("code") == 0:
                    info = (detail.get("zpData") or {}).get("jobInfo") or {}
                    print(f"  详情 salaryDesc={info.get('salaryDesc')!r}")
