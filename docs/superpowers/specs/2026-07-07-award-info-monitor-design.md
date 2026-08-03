# 报奖信息监测系统 - 设计文档

> 创建日期：2026-07-07
> 最后更新：2026-08-03
> 状态：P0+P1 可靠性改造、P2 评估与结构化抽取、P3 标题门禁及 Agnes/Ollama provider 链已实现；生产库已迁移到 schema v3

## 1. 项目背景

用户是广州的城市规划从业者，日常工作包括报各种规划行业奖和科学技术奖。目前需要手动浏览多个网站和微信公众号来发现报奖信息，效率低且容易遗漏。

本项目旨在构建一个自动化监测程序，每天定时抓取多个信息源，通过关键词初筛 + 本地AI二次确认，筛选出"申报通知"类信息，通过飞书 Webhook 推送到群聊，确保第一时间获取报奖信息。

## 2. 用户画像

- 技术水平：能看懂简单代码，能改配置文件，但写不了代码
- 运行环境：Windows 11，先本地跑通，后续考虑上云
- 通知偏好：飞书群聊通知（Webhook）
- AI环境：Agnes 优先；Key 只从项目根目录 `local.env` 读取；本地 Ollama `qwen3.5:latest` 作为 fallback

## 3. 整体架构与运行流程

### 3.1 运行流程

程序每次运行执行以下流水线：

```
定时触发（Windows计划任务，每天 09:00 和 21:00）
  │
  ▼
① 读取 config.yaml（网站列表、关键词、飞书 Webhook、AI配置等）
  │
  ▼
② 逐个爬取所有信息源
   ├─ 网站爬虫：抓取各网站通知公告栏，提取标题+链接+发布日期+摘要
   └─ 公众号爬虫：通过搜狗微信搜索指定公众号最新文章
  │
  ▼
③ 90天窗口去重：规范化 URL，并用“标题+来源”兜底；新增项写为 discovered
  │
  ▼
④ 仅为新增项抓取详情正文，补充摘要和最终跳转 URL
  │
  ▼
⑤ P3 标题门禁：拒绝获奖结果、非奖项机会、非行业奖项、标题年度过期
  │
  ▼
⑥ 关键词初筛：标题/摘要命中预设关键词且不命中排除词 → ai_pending
  │
  ▼
⑦ AI二次确认：严格解析 JSON 布尔值并抽取奖项名称、截止日期、申报对象；不可用或响应无效则保留 ai_pending
  │
  ▼
⑧ 飞书分批推送：成功项标记 pushed，失败项标记 push_failed 并在下次精确重试
  │
  ▼
⑨ 写入 run_logs 和 source_runs：记录整次运行及每个渠道的状态、数量、耗时和错误
```

### 3.2 关键设计原则

- **可恢复状态机**：处理中断后从 `discovered`、`ai_pending`、`ready_to_push` 或 `push_failed` 继续
- **窗口去重一致**：数据库约束与90天业务窗口一致，不再由永久 URL UNIQUE 阻断重现通知
- **容错隔离**：单个信息源失败不影响其他信息源，并写入独立渠道健康记录
- **AI调用控制**：只有通过关键词初筛的才调AI，控制资源消耗
- **失败不等于通过**：AI不可用或返回无效 JSON 时等待重试，避免将系统故障变成群消息噪音
- **测试零外发**：自动化测试使用 fake notifier；`--dry-run` 使用生产库副本且禁止飞书

## 4. 信息源

### 4.1 信息源清单

#### 政府主管部门（5个，2个禁用）

| 信息源 | 适配器类型 | 状态 |
|--------|-----------|------|
| 住房和城乡建设部 | gov_general | 禁用：官网无报奖信息，只有政策发布 |
| 广东省住房和城乡建设厅 | gov_general | 启用 |
| 自然资源部 | gov_general | 启用 |
| 广东省自然资源厅 | gov_general | 启用 |
| 广州市规划和自然资源局 | gov_general | 启用 |

#### 行业学会/协会（6个，1个禁用）

| 信息源 | 适配器类型 | 状态 |
|--------|-----------|------|
| 中国城市规划学会 | org_general | 启用 |
| 中国城市规划协会 | cacp_api | 启用（JSONP API） |
| 广东省城市规划协会 | org_general | 禁用：网站已无法访问 |
| 中国自然资源学会 | org_general | 启用 |
| 广东省国土空间规划协会 | org_general | 启用（SSL证书过期，已通过 `ssl_verify: false` 跳过） |
| 中国风景园林学会 | chsla_api | 启用（签名 API） |

#### 科技奖渠道（2个）

| 信息源 | 适配器类型 | 状态 |
|--------|-----------|------|
| 科技部 | gov_general | 启用 |
| 广东省科技厅 | gov_general | 启用 |

#### 其他（2个）

| 信息源 | 适配器类型 | 说明 |
|--------|-----------|------|
| 华夏建设科学技术奖励委员会 | org_general | 华夏奖原始通知发布渠道；公众号单账号直搜已停用，保留主题检索白名单 |
| 奖项竞赛申报信息库 (funresearch.cn) | org_general | 第三方汇总，按行业关键词二次过滤 |

#### 微信公众号（账号直搜 + 主题检索）

通过 `sogo_wechat` 爬虫抓取启用的公众号账号，并额外执行全局主题检索。`资源中国` 和 `华夏建设科学技术奖励委员会` 已停用单账号直搜，但保留 `topic_match_enabled: true`，继续作为主题检索的账号白名单；搜狗原始结果未命中白名单时记为 `no_match`，不当作网络故障。

### 4.2 爬虫适配器设计

采用**模板方法模式**，统一接口：

```python
class BaseCrawler:
    """所有爬虫的基类，定义统一接口"""
    def fetch(self) -> list[Article]:
        """抓取页面，返回文章列表"""
        html = self._request_page()
        articles = self._parse(html)
        return articles

    def _request_page(self) -> str: ...           # 子类实现：请求页面
    def _parse(self, html) -> list[Article]: ...  # 子类实现：解析HTML
```

适配器类型：
- **gov_general**：政府网站通用适配器，处理典型 gov.cn 页面结构，支持翻页
- **org_general**：学会/协会网站通用适配器，适配各协会网站结构，支持自定义 selectors 和翻页
- **cacp_api**：中国城市规划协会 JSONP API 爬虫，通过数据接口获取文章列表
- **chsla_api**：中国风景园林学会签名 API 爬虫，逆向签名算法调用接口
- **sogo_wechat**：搜狗微信搜索爬虫，按公众号名称搜索文章
- `feeddd_fallback.py` 是旧的降级爬虫，feeddd.org 已停服，不再调用，文件保留仅供参考

### 4.3 Article 数据结构

```python
@dataclass
class Article:
    title: str
    url: str
    source: str
    publish_date: str = ""
    summary: str = ""
    raw_content: str = ""
    db_id: int | None = None
    award_name: str = ""
    deadline_text: str = ""
    deadline_date: str = ""
    applicant_scope: list[str] = field(default_factory=list)
```

### 4.4 搜狗微信搜索

- 按公众号名称搜索最新文章
- 需要处理反爬（cookie、频率限制）
- 搜狗有反爬机制，连续快速请求会触发限流；生产环境每天1-2次不会触发
- feeddd.org 已停服，不再作为降级方案

### 4.5 config.yaml 信息源配置

```yaml
sources:
  websites:
    - name: "广东省住房和城乡建设厅"
      url: "https://zfcxjst.gd.gov.cn/xxgk/wjtz/index.html"
      type: "gov_general"
      enabled: true
      pagination:
        mode: construct
        template: "...&page={page}"
        max_pages: 3
      selectors:
        items: "ul.list li a"
        # ...
    # ... 其余网站

  wechat_accounts:
    - name: "中国城市规划学会"
      keyword: "中国城市规划学会"
      enabled: true
    # ... 其余公众号
```

每个信息源有 `enabled` 开关，可临时关闭。网站支持 `pagination`（翻页）和 `selectors`（自定义 CSS 选择器）配置。新增网站只需在 yaml 中加一条配置。

## 5. 筛选逻辑

### 5.1 关键词初筛

**正向关键词（命中→进入AI确认）：**

```yaml
keywords:
  - 申报
  - 推荐
  - 提名
  - 评选
  - 评选表彰
  - 报名
  - 征集
  - 科学技术奖
  - 优秀规划
  - 优秀设计
  - 优秀城乡规划
  - 优秀国土空间规划
  - 科技进步奖
  - 华夏奖
  - 评选活动
  - 优秀城市规划设计奖
```

**排除关键词（命中→直接跳过）：**

```yaml
exclude_keywords:
  - 获奖名单
  - 获奖公示
  - 拟授奖
  - 评审结果
  - 评委
  - 招标
  - 采购
  - 招聘
  - 培训班
  - 继续教育
  - 注册城乡规划师
```

排除词逻辑：用户只要"申报通知"（还能赶上报），排除"获奖名单""评审结果"等已结束的信息。

### 5.2 AI二次确认

通过关键词初筛的候选信息，送入 Agnes 判断；Agnes 请求失败、返回空内容或 Key 不可用时回退本地 Ollama。两者都不可用时保持 `ai_pending`，不会按通过处理。

**当前 provider：Agnes `agnes-2.5-flash`**
- Key 只从项目根目录 `local.env` 读取，按请求轮换；不读取进程或系统环境变量
- 本地 Ollama `qwen3.5:latest` 作为 fallback
- 使用 `/no_think` 指令跳过思考模式，加快响应

**判断逻辑（p3-v1 提示词）：**
- 输入：判断日期 + 发布日期 + 标题 + 正文摘要
- Prompt 要求同时确认行业相关性、申报动作和可行动性，并列出高频误报排除项
- 返回结构化 JSON：`{"is_award_application": true/false, "reason": "...", "award_name": "...", "deadline_text": "...", "deadline_date": "YYYY-MM-DD", "applicant_scope": ["..."]}`
- 返回 `true` → 写入 `article_extractions` 并进入 `ready_to_push`
- 返回 `false` → 写入 `article_extractions` 并标记 `ai_rejected`
- API调用失败或 JSON/字段类型无效 → 保持 `ai_pending`，记录原因并在下次运行重试
- `filter.ai.max_workers` 支持 1～4 路受控并发，传输故障后熔断本批后续请求

### 5.3 AI配置

```yaml
filter:
  ai:
    enabled: true
    provider: agnes
    api_url: "https://api.agnes-ai.cn/v1/chat/completions"
    model: "agnes-2.5-flash"
    api_key_file: "local.env"
    fallback:
      provider: ollama
      api_url: "http://localhost:11434/api/chat"
      model: "qwen3.5:latest"
    max_summary_length: 5000
    timeout: 60
    max_workers: 2  # 1～4，需先用 benchmark_ai.py 验证
```

### 5.4 P3 标题门禁

在 AI 之前增加标题门禁（`filters/title_gate.py`），仅拒绝标题已能确定无关的记录：
- 获奖结果、名单公示、评审结果、喜报、网络投票 → 拒绝
- 未体现申报/推荐/报名/征集等动作 → 拒绝
- 不是奖项或竞赛 → 拒绝
- 标题年度早于判断年度 → 拒绝
- 非规划/建设行业奖项 → 拒绝
- 边界不明时仍交 AI 判断

## 6. 通知推送

### 6.1 飞书 Webhook 推送

每条报奖信息包含：标题、来源、发布日期、摘要、原文链接。

多条命中合并为一条富文本消息推送，避免连续打扰。

**推送消息格式（飞书 post 富文本）：**

```
📰 报奖信息监测 (本次发现 N 条)

━━━━━━━━━━━━━━━━
📌 {标题}
来源：{来源} | 日期：{日期}
摘要：{摘要前200字}
🔗 查看原文（超链接）
━━━━━━━━━━━━━━━━
```

### 6.2 特殊场景处理

| 场景 | 处理方式 |
|------|----------|
| 本次无新信息 | 不推送 |
| 所有信息源抓取失败 | 推送告警"⚠️ 本次抓取全部失败，请检查" |
| 部分信息源失败 | 写入 `source_runs` 和 `run_logs.errors`，正常处理其他渠道 |
| AI不可用 | 保留 `ai_pending`，不推送，下一次重试 |
| 飞书部分批次失败 | 已成功批次标记 `pushed`，失败批次标记 `push_failed` |

### 6.3 推送配置

```yaml
notification:
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
    secret: ""  # 加签密钥（可选）
```

## 7. 数据存储

### 7.1 SQLite 数据库

本地文件 `data/monitor.db`，核心四张表。schema 迁移前自动备份到 `data/backups/`。

**articles 表（所有抓取过的信息）：**

```sql
CREATE TABLE articles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    url              TEXT NOT NULL,
    canonical_url    TEXT NOT NULL,
    dedup_key        TEXT NOT NULL,
    source           TEXT NOT NULL,
    publish_date     TEXT,
    summary          TEXT,
    raw_content      TEXT,
    status           TEXT NOT NULL DEFAULT 'discovered',
    ai_reason        TEXT,
    last_error       TEXT,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    pipeline_version INTEGER NOT NULL DEFAULT 3,
    created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    pushed_at        TEXT
);
```

**status 流转：**

```
discovered → title_gate_rejected
           → keyword_rejected
           → ai_pending → ai_rejected
                        → ready_to_push → pushed
                                        → push_failed → 下次重试
```

**article_extractions 表（schema v3 新增，一对一关联 articles）：**

```sql
CREATE TABLE article_extractions (
    article_id           INTEGER PRIMARY KEY REFERENCES articles(id),
    is_award_application BOOLEAN NOT NULL,
    award_name           TEXT,
    deadline_text        TEXT,
    deadline_date        TEXT,
    applicant_scope      TEXT,  -- JSON 数组
    model                TEXT,
    prompt_version       TEXT,
    raw_response         TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```

**run_logs 表（每次运行记录）：**

```sql
CREATE TABLE run_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time         TEXT,
    finished_at      TEXT,
    status           TEXT,
    duration_seconds REAL,
    total_articles   INTEGER,
    new_articles     INTEGER,
    keyword_passed   INTEGER,
    ai_confirmed     INTEGER,
    pushed           INTEGER,
    errors            TEXT
);
```

**source_runs 表（每个渠道每次运行记录）：**

记录 `source`、`source_type`、`status`、抓取数、新增数、耗时、详情成功/失败数和错误。渠道状态区分 `success`、`empty`、`partial`、`failed`、`anomaly`、`no_match`；历史正常渠道突然返回0条时标记 `anomaly`。`no_match` 仅表示搜狗结果未命中公众号白名单，不计入连续网络失败；连续失败告警只统计 `failed/partial`。

### 7.2 去重逻辑

- URL 去除 fragment、常见追踪参数并稳定 query 顺序后得到 `canonical_url`
- 在最近 `dedup_days` 天内，规范化 URL 相同或“规范化标题+来源”相同即视为重复
- `articles.url` 不再永久 UNIQUE；超过窗口后，同一稳定 URL 可以作为新一轮通知重新进入流程
- 插入与去重在 `BEGIN IMMEDIATE` 事务中完成，避免并发查询/插入竞争

### 7.3 存储配置

```yaml
storage:
  db_path: "data/monitor.db"
  dedup_days: 90
```

## 8. 定时任务

使用 Windows 任务计划程序，每天运行2次：09:00 和 21:00。

提供 `setup_task.bat` 一键注册：

```bat
schtasks /create /tn "报奖信息监测-上午" /tr "cmd /c g:\AI\报奖信息监测\run.bat" /sc daily /st 09:00 /f
schtasks /create /tn "报奖信息监测-晚上" /tr "cmd /c g:\AI\报奖信息监测\run.bat" /sc daily /st 21:00 /f
```

使用 `pythonw` 避免弹出命令行窗口。

## 9. 日志系统

日志写入 `logs/monitor.log`，按日期滚动，保留最近30天。

```yaml
logging:
  level: INFO
  dir: "logs"
  max_days: 30
```

## 10. 项目目录结构

```
g:\AI\报奖信息监测\
├── CLAUDE.md                    # 项目规范文件
├── config.yaml                  # 所有配置（不进git）
├── config.yaml.example          # 配置模板
├── main.py                      # 入口文件
├── gen.py                       # 爬虫测试脚本（逐渠道调试用）
├── setup_task.bat               # 定时任务注册脚本
├── requirements.txt             # Python依赖
│
├── crawlers/                    # 爬虫模块
│   ├── __init__.py
│   ├── base.py                  # BaseCrawler 基类 + Article
│   ├── gov_general.py           # 政府网站通用爬虫（含翻页）
│   ├── org_general.py           # 学会/协会通用爬虫（含 selectors + 翻页）
│   ├── cacp_api.py              # 中国城市规划协会 JSONP API
│   ├── chsla_api.py             # 中国风景园林学会签名 API
│   ├── sogo_wechat.py           # 搜狗微信搜索爬虫
│   └── feeddd_fallback.py       # 已停服，保留仅供参考
│
├── filters/                     # 筛选模块
│   ├── __init__.py
│   ├── keyword_filter.py        # 关键词初筛
│   ├── title_gate.py            # P3 标题门禁
│   └── ai_filter.py             # Ollama AI二次确认 + 结构化抽取
│
├── notifiers/                   # 通知模块
│   ├── __init__.py
│   └── feishu.py                # 飞书 Webhook 推送
│
├── storage/                     # 存储模块
│   ├── __init__.py
│   ├── database.py              # SQLite操作
│   ├── export_legacy_review.py  # 历史 ai_confirmed 导出
│   ├── export_evaluation_review.py  # P2 评估优先队列导出
│   ├── evaluate_labels.py       # 加权指标计算
│   ├── benchmark_ai.py          # AI 并发基准
│   └── replay_p3_evaluation.py  # P3 离线回放
│
├── utils/                       # 工具模块
│   ├── __init__.py
│   ├── logger.py                # 日志配置
│   ├── http_client.py           # HTTP请求封装
│   ├── normalization.py         # URL/标题规范化
│   └── process_lock.py          # 进程锁
│
├── tests/                      # 自动化测试（不调真实飞书）
│
├── docs/                        # 文档
│   ├── P2-评估结构化抽取与并发优化.md
│   ├── P3-筛选规则回归优化.md
│   └── superpowers/
│       ├── plans/               # 实现计划（历史）
│       └── specs/               # 设计文档
│
├── data/                        # 运行数据（git忽略）
│   ├── monitor.db
│   ├── backups/                 # 迁移前备份
│   ├── dry-runs/                # dry-run 副本
│   └── reports/                 # 人工审核清单
│
├── logs/                        # 日志目录（git忽略）
│   └── monitor.log
│
├── check_p3_run.py             # 生产库只读观察脚本
├── gen.py                       # 逐渠道调试脚本
├── run.bat                      # 计划任务调用入口
├── setup_task.bat               # 定时任务注册
└── .gitignore
```

## 11. Python 依赖

```txt
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
pyyaml>=6.0
```

## 12. 容错与错误处理

| 场景 | 处理方式 |
|------|----------|
| 单个网站抓取超时/失败 | 记录 `failed/partial` 渠道结果，继续其他站，进程退出码为4 |
| 搜狗微信被限流 | 公众号渠道记录失败；下一次运行仍可补抓最近3天 |
| Agnes 或 Ollama 不可用 | 保持 `ai_pending` 并增加尝试次数，下次重试 |
| 飞书推送失败 | HTTP层重试后标记 `push_failed`，下次只重试失败批次 |
| 程序整体崩溃 | 操作系统释放进程锁；下次从持久化状态继续 |
| 测试通知链路 | fake notifier 或 `--dry-run`，禁止真实飞书消息 |

## 13. 安全约定

- 飞书 webhook_url 和 secret 只放 config.yaml，不进 git
- config.yaml 在 .gitignore 中排除
- 日志和数据文件不进 git
- Agnes Key 只放项目根目录 `local.env`，该文件不进 git；Ollama fallback 在本机运行。使用 Agnes 时，发送给 AI 的文章标题和正文摘要会离开本机，部署前需确认数据合规边界

## 14. 端到端验证结果（2026-07-09）

以下是 v1 初稿的历史基线，不代表 P0+P1 当前状态机。P0+P1 于 2026-07-10 完成数据库副本 dry-run、schema v2 生产迁移和自动化回归验证；测试过程未发送真实飞书消息。

| 阶段 | 数量 | 耗时 |
|------|------|------|
| 爬取 | 870 条（13个网站 + 6个公众号） | ~43秒 |
| 去重后新增 | 814 条 | ~29秒 |
| 关键词初筛 | 814 → 120 条通过 | <1秒 |
| AI二次确认 | 120 → 11 条通过 | ~7分钟 |
| 飞书推送 | 11 条推送成功 | ~1秒 |
| **总耗时** | | **~8分钟** |
