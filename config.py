"""Boss 直聘采集配置"""

# 计算机类岗位搜索关键词
COMPUTER_KEYWORDS = [
    "Java开发",
    "Python开发",
    "前端开发",
    "后端开发",
    "Go开发",
    "C++开发",
    "算法工程师",
    "数据开发",
    "大数据工程师",
    "运维工程师",
    "DevOps",
    "测试开发",
    "Android开发",
    "iOS开发",
    "全栈开发",
    "机器学习",
    "人工智能",
    "嵌入式开发",
    "网络安全",
    "云计算",
]

# 常用城市代码（Boss 直聘内部编码）
CITIES = {
    "全国": "100010000",
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "南京": "101190100",
    "武汉": "101200100",
    "西安": "101110100",
}

# 常见 IT 技能词库（用于从 JD 描述中补充提取）
SKILL_KEYWORDS = [
    "Java", "Python", "Go", "Golang", "C++", "C#", "Rust", "PHP", "Ruby", "Scala",
    "JavaScript", "TypeScript", "Vue", "React", "Angular", "Node.js", "Spring",
    "Spring Boot", "Spring Cloud", "MyBatis", "Django", "Flask", "FastAPI",
    "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch", "Kafka", "RabbitMQ",
    "Docker", "Kubernetes", "K8s", "Linux", "Nginx", "Git", "CI/CD", "Jenkins",
    "AWS", "Azure", "阿里云", "腾讯云", "Hadoop", "Spark", "Flink", "Hive",
    "TensorFlow", "PyTorch", "机器学习", "深度学习", "NLP", "CV", "LLM",
    "微服务", "分布式", "高并发", "MySQL", "Oracle", "SQL Server",
    "Android", "iOS", "Swift", "Kotlin", "Flutter", "React Native",
    "HTML", "CSS", "Webpack", "Vite", "Next.js", "Nuxt",
    "Shell", "Ansible", "Terraform", "Prometheus", "Grafana",
    "Selenium", "JUnit", "pytest", "JMeter",
]

DEFAULT_CITY = "北京"
DEFAULT_KEYWORDS = ["Java开发", "Python开发", "前端开发", "后端开发", "算法工程师"]
MAX_PAGES_PER_KEYWORD = 3

# 反爬相关：请求间隔（秒），含随机抖动
REQUEST_DELAY_SEC = 2.5
REQUEST_DELAY_JITTER = 2.0
MAX_RETRIES = 4
# 同一 security-check 参数冷却期内不重复执行
SECURITY_CHECK_COOLDOWN_SEC = 20
# 连续详情 API 失败后暂停详情抓取（列表数据仍保留）
DETAIL_FAIL_STREAK_LIMIT = 3

# 采集模式: ui=模拟用户搜索(更稳), api=直接调接口(更快)
DEFAULT_FETCH_MODE = "ui"
