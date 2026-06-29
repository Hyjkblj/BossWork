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

## 系统架构（OpenAI Agents SDK）

基于 [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) 原生架构：**Agent + Tools + Handoffs + Runner + Context**。

```
┌─────────────────────────────────────────────────────────────┐
│                    app.py (CLI 入口)                        │
│              interactive | run | execute                    │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              BossWorkOrchestrator (Triage Agent)             │
│         handoff ──┬──────────┬──────────┐                     │
└───────────────────┼──────────┼──────────┼─────────────────────┘
                    ▼          ▼          ▼
            JobMatcher   JobSeekerAgent   OutreachPlanner
            岗位筛选      求职者回复 HR      主动联系策略
                    │          │          │
                    └──────────┴──────────┘
                               │ function_tool
┌──────────────────────────────▼──────────────────────────────┐
│  BossWorkContext（依赖注入：profile / jobs / pending_actions）│
└──────────────────────────────┬──────────────────────────────┘
         │                                      │
         ▼                                      ▼
  services/jobs.py                    runtime/browser.py
  本地岗位 JSON/CSV                   Playwright 登录态发消息
  scraper.py (采集)                   chat_client.py
```

### Agent 职责

| Agent | 职责 | 工具 |
|-------|------|------|
| **BossWorkOrchestrator** | 理解意图，路由到专家 Agent | handoffs |
| **JobMatcher** | 按城市/技能筛选推荐岗位 | `filter_job_listings`, `get_job_by_id` |
| **JobSeekerAgent** | **以求职者身份回复 HR 说的话** | `reply_to_hr_message`, `queue_browser_reply` |
| **OutreachPlanner** | 制定打招呼计划 | `filter_job_listings`, `queue_browser_greet` |

### 快速开始（Agent 系统）

```bash
pip install -r requirements.txt
$env:OPENAI_API_KEY = "sk-xxx"

# 交互模式
python app.py interactive

# 单次任务
python app.py run "帮我从广州深圳数据里筛选 Java Spring 岗位，推荐 top5"

# 求职者回复 HR（传入 HR 原话）
python app.py run "HR 说：您好，请问您有几年 Java 经验？期望薪资多少？请以我（求职者）的身份回复"

# Agent 排队浏览器操作后执行
python app.py run "为 top3 岗位写打招呼并排队" --execute
```

## HR 实时沟通 Agent（需登录）

在**已登录 Boss 账号**（wt2）下，可与 HR 进行接近实时的问答（轮询聊天 API + 浏览器 UI 发消息）：

```bash
# 1. 从已有数据筛选广州/深圳岗位
python chat_agent.py filter --skills Java Spring Redis --limit 10

# 2. 打开浏览器登录后，对筛选岗位逐个「立即沟通」
python chat_agent.py greet --limit 5

# 3. 与某个 HR 实时问答（半自动：Agent 建议回复，你确认后发送）
python chat_agent.py chat --job-id <job_id>

# 4. 查看历史聊天记录
python chat_agent.py history --boss-id <bossId>
```

| 能力 | 说明 |
|------|------|
| 读消息 | `/wapi/zpchat/geek/historyMsg`（登录 Cookie） |
| 发消息 | 聊天页 UI 模拟输入（复用页面 MQTT 连接） |
| 实时性 | 每 2.5 秒轮询新 HR 消息 |
| 问答 | **LLM + 用户预设资料**（`user_profile.json`），结合对话历史生成回复 |

### 配置 LLM 与个人资料

```bash
# 1. 复制并编辑你的求职信息
copy user_profile.example.json user_profile.json

# 2. 复制并编辑 LLM 配置（支持 OpenAI 兼容 API：OpenAI / DeepSeek / 通义等）
copy llm_config.example.json llm_config.json

# 3. 设置 API Key（Windows PowerShell）
$env:OPENAI_API_KEY = "sk-xxx"
# 若用 DeepSeek 等，可同时设置：
# $env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"

# 4. 离线测试 LLM 回复（无需登录 Boss）
python chat_agent.py test-llm --hr-msg "您好，请问您有几年Java经验？期望薪资多少？"

# 5. 登录后与 HR 实时问答（LLM 结合历史消息 + 你的资料）
python chat_agent.py chat --job-id <job_id>
```

**`user_profile.json` 可配置项：**

| 字段 | 说明 |
|------|------|
| `name` / `title` | 姓名、职位方向 |
| `skills` / `projects_summary` | 技能与项目经历 |
| `salary_expectation` | 期望薪资 |
| `availability` | 到岗时间 |
| `work_preferences` | 加班/远程等偏好 |
| `self_intro` | 自我介绍 |
| `custom_rules` | 自定义回复规则 |
| `reply_style` | 语气风格 |

LLM 不可用时自动回退到规则模板；聊天中输入 `r` 可重新生成回复。

**注意**：批量自动打招呼/回复可能触发平台风控，默认半自动（确认后发送）。

## 项目结构

```
BossWork/
├── app.py                  # OpenAI Agents SDK 主入口
├── bosswork/               # Agent 系统核心包
│   ├── context.py          # BossWorkContext 依赖注入
│   ├── agents/
│   │   ├── definitions.py  # Orchestrator + 专家 Agent + Handoffs
│   │   ├── tools.py          # @function_tool 工具
│   │   └── runner.py         # Runner.run_sync 封装
│   ├── services/
│   │   ├── jobs.py           # 岗位加载/筛选
│   │   └── profile.py        # 用户资料
│   └── runtime/
│       └── browser.py        # 执行 pending 浏览器操作
├── main.py                 # 数据采集 CLI
├── scraper.py              # Playwright 采集
├── anti_bot.py             # 反爬
├── chat_client.py          # 登录态聊天
├── chat_agent.py           # 旧版 CLI（可逐步迁移到 app.py）
├── user_profile.json       # 求职者预设信息
├── llm_config.json         # 模型配置
└── output/                 # 岗位数据
```
