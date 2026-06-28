# BossWork — Boss 直聘岗位数据采集

从 Boss 直聘采集**计算机类岗位**信息，重点提取**所需技能**和**薪资**。

## 原理说明

Boss 直聘的 `/wapi/` 接口有多层反爬，本项目内置对应策略：

| 反爬机制 | 应对方式 |
|----------|----------|
| `code=37` 访问异常 | 自动跳转 `security-check`，生成 `__zp_stoken__` |
| 缺少 `__zp_stoken__` Cookie | 启动时 bootstrap 会话，探测并触发安全校验 |
| Playwright 自动化检测 | 隐藏 `navigator.webdriver` 等特征 |
| 行为分析 | 随机延迟、鼠标移动、页面滚动 |
| 直接 fetch 易被拦 | 默认 `ui` 模式：模拟搜索 + 拦截网络响应 |
| 频率限制 | 请求间隔 2.5~4.5 秒，失败自动重试最多 3 次 |

本项目使用 **Playwright + 本机 Google Chrome**，在真实浏览器会话中调用内部 API，可获取：

- 岗位名称、薪资（明文，非字体反爬乱码）
- 经验、学历、城市、公司信息
- 技能标签（`skills` / `jobLabels`）
- 岗位详情 JD（可选，用于补充技能提取）

## 快速开始

```bash
# 1. 安装依赖（使用本机 Chrome，无需下载 Playwright Chromium）
pip install -r requirements.txt

# 2. 运行采集（会打开 Google Chrome，需手动登录 Boss 直聘）
python main.py --city 北京 --keywords Java开发 Python开发 前端开发

# 3. 第二次运行可跳过登录等待（登录态保存在 .browser_profile）
python main.py --skip-login-wait --city 深圳 --pages 2
```

## 常用参数

| 参数 | 说明 |
|------|------|
| `--keywords` | 搜索关键词，可多个 |
| `--all-keywords` | 使用全部预置计算机类关键词（约 20 个） |
| `--city` | 城市：北京、上海、深圳、杭州 等 |
| `--pages` | 每个关键词最多抓取页数（默认 3，平台单条件上限约 10 页） |
| `--no-detail` | 不抓详情页，速度更快 |
| `--headless` | 无头模式（首次登录建议不用） |
| `--browser` | 浏览器：`chrome`（默认）、`msedge`、`chromium` |
| `--mode` | 采集模式：`ui`（默认，更稳）、`api`（更快） |
| `--skip-login-wait` | 跳过登录等待 |
| `--output` | 输出目录，默认 `output/` |

## 输出文件

- `output/boss_jobs_YYYYMMDD_HHMMSS.json` — 完整岗位数据
- `output/boss_jobs_YYYYMMDD_HHMMSS.csv` — 表格格式，便于 Excel 分析
- `output/skill_summary.json` — 技能出现频次统计

## 数据结构示例

```json
{
  "job_name": "Java高级开发工程师",
  "salary": "20-35K",
  "experience": "3-5年",
  "degree": "本科",
  "company": "某科技公司",
  "extracted_skills": ["Java", "Spring Boot", "MySQL", "Redis", "微服务"],
  "search_keyword": "Java开发"
}
```

## 注意事项

1. **合规使用**：仅供个人学习/求职分析，请控制频率，遵守 Boss 直聘用户协议
2. **强烈建议登录**：未登录极易触发 code=37；登录后成功率显著提升
3. **首次运行**：浏览器会打开并完成 security-check，请耐心等待
4. **频率限制**：建议 `--pages` 不超过 5，关键词不宜一次过多
5. **被拦截时**：程序会自动重试 security-check；若仍失败，在浏览器中手动完成验证后重跑

## 项目结构

```
BossWork/
├── main.py           # CLI 入口
├── scraper.py        # Playwright 采集核心
├── anti_bot.py       # 反爬识别与应对
├── skill_analyzer.py # 技能提取与统计
├── config.py         # 关键词、城市、技能词库
├── requirements.txt
└── output/           # 采集结果
```
