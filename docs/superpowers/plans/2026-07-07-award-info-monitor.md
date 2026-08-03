# 报奖信息监测系统 Implementation Plan

> **历史文档** — 本计划为 v1 初稿，推送方案（PushPlus）、schema（v1）和部分文件结构已被后续迭代取代。
> 当前架构以 `docs/superpowers/specs/2026-07-07-award-info-monitor-design.md` 为准。
> 本文仅用于追溯初始实现思路，不是当前安装、配置或运行手册；请以项目根目录 `README.md`、`AGENTS.md` 和当前配置模板为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个定时抓取15个信息源、通过关键词+本地AI筛选"申报通知"类报奖信息、经PushPlus推送到微信的监测系统

**Architecture:** 单体Python项目，模块化设计。爬虫模块（BaseCrawler模板方法模式）→ 去重（SQLite）→ 关键词初筛 → Ollama AI二次确认 → PushPlus推送。Windows计划任务定时触发。

**Tech Stack:** Python 3.10+, requests, beautifulsoup4, lxml, pyyaml, SQLite3, Ollama (qwen3.5:latest), PushPlus API

---

## 文件结构总览

| 文件 | 职责 |
|------|------|
| `CLAUDE.md` | 项目规范、目录结构约定、新增网站流程 |
| `.gitignore` | 排除 config.yaml / data / logs / __pycache__ |
| `requirements.txt` | Python依赖 |
| `config.yaml` | 所有配置（信息源、关键词、AI、推送、存储） |
| `main.py` | 入口，编排完整流水线 |
| `setup_task.bat` | 注册Windows计划任务 |
| `utils/logger.py` | 日志配置 |
| `utils/http_client.py` | HTTP请求封装（重试、UA伪装、超时） |
| `storage/database.py` | SQLite建表、插入、去重查询、状态更新 |
| `crawlers/base.py` | BaseCrawler基类 + Article数据结构 |
| `crawlers/gov_general.py` | 政府网站通用爬虫 |
| `crawlers/org_general.py` | 学会/协会网站通用爬虫 |
| `crawlers/sogo_wechat.py` | 搜狗微信搜索爬虫 |
| `crawlers/feeddd_fallback.py` | feeddd降级爬虫 |
| `filters/keyword_filter.py` | 关键词+排除词初筛 |
| `filters/ai_filter.py` | Ollama AI二次确认 |
| `notifiers/pushplus.py` | PushPlus微信推送 |

---

### Task 1: 项目脚手架

**Files:**
- Create: `g:/AI/报奖信息监测/CLAUDE.md`
- Create: `g:/AI/报奖信息监测/.gitignore`
- Create: `g:/AI/报奖信息监测/requirements.txt`
- Create: `g:/AI/报奖信息监测/config.yaml`

- [ ] **Step 1: 创建 .gitignore**

```gitignore
# 敏感配置
config.yaml

# 运行数据
data/

# 日志
logs/

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/

# IDE
.vscode/
.idea/
```

- [ ] **Step 2: 创建 requirements.txt**

```txt
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
pyyaml>=6.0
```

- [ ] **Step 3: 创建 config.yaml 模板**

```yaml
# 报奖信息监测系统配置文件
# 将此文件复制为 config.yaml 并填入你的实际配置

# 信息源配置
sources:
  websites:
    # 政府主管部门
    - name: "住房和城乡建设部"
      url: "https://www.mohurd.gov.cn/jjbzgg/index.html"
      type: "gov_general"
      enabled: true

    - name: "广东省住房和城乡建设厅"
      url: "https://zfcxjst.gd.gov.cn/xxgk/wjtz/index.html"
      type: "gov_general"
      enabled: true

    - name: "自然资源部"
      url: "https://www.mnr.gov.cn/channels/213.aspx"
      type: "gov_general"
      enabled: true

    - name: "广东省自然资源厅"
      url: "https://nr.gd.gov.cn/zwgknew/tzgg/gg/index.html"
      type: "gov_general"
      enabled: true

    - name: "广州市规划和自然资源局"
      url: "https://ghzyj.gz.gov.cn/gkmlpt/tzgg"
      type: "gov_general"
      enabled: true

    # 行业学会/协会
    - name: "中国城市规划学会"
      url: "https://www.planning.org.cn/newslist?type=1"
      type: "org_general"
      enabled: true

    - name: "中国城市规划协会"
      url: "http://www.cacp.org.cn/tzgg/index.jhtml"
      type: "org_general"
      enabled: true

    - name: "广东省城市规划协会"
      url: "https://www.gdplan.com/"
      type: "org_general"
      enabled: true

    - name: "中国自然资源学会"
      url: "http://www.csnr.org/"
      type: "org_general"
      enabled: true

    - name: "广东省国土空间规划协会"
      url: "https://www.gdtspa.org.cn/news/35"
      type: "org_general"
      enabled: true

    # 科技奖渠道
    - name: "科技部"
      url: "https://www.most.gov.cn/tztg/"
      type: "gov_general"
      enabled: true

    - name: "广东省科技厅"
      url: "https://gdstc.gd.gov.cn/zwgk_n/tzgg/"
      type: "gov_general"
      enabled: true

    # 其他
    - name: "华夏建设科学技术奖励委员会"
      url: "https://www.chinagb.net/"
      type: "org_general"
      enabled: true

    - name: "中国风景园林学会"
      url: "http://www.chsla.org.cn/"
      type: "org_general"
      enabled: true

    - name: "奖项竞赛申报信息库"
      url: "http://www.funresearch.cn/award/index"
      type: "org_general"
      enabled: true

  # 微信公众号（通过搜狗微信搜索）
  wechat_accounts:
    - name: "中国城市规划学会"
      keyword: "中国城市规划学会"
      enabled: true
    - name: "中国城市规划协会"
      keyword: "中国城市规划协会"
      enabled: true
    - name: "广东省国土空间规划协会"
      keyword: "广东省国土空间规划协会"
      enabled: true
    - name: "中国自然资源学会"
      keyword: "中国自然资源学会"
      enabled: true
    - name: "华夏建设科学技术奖励委员会"
      keyword: "华夏建设科学技术奖"
      enabled: true
    - name: "中国风景园林学会"
      keyword: "中国风景园林学会"
      enabled: true

# 筛选配置
filter:
  # 正向关键词（标题或摘要命中任意一个即进入AI确认）
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

  # 排除关键词（命中任意一个直接跳过）
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

  # AI二次确认（本地Ollama）
  ai:
    enabled: true
    api_url: "http://localhost:11434/api/chat"
    model: "qwen3.5:latest"
    max_summary_length: 500
    timeout: 30

# 通知配置
notification:
  pushplus:
    token: "在此填入你的PushPlus Token"
    topic: ""

# 存储配置
storage:
  db_path: "data/monitor.db"
  dedup_days: 90

# 日志配置
logging:
  level: "INFO"
  dir: "logs"
  max_days: 30

# 爬虫配置
crawler:
  timeout: 15
  retry_times: 3
  retry_delay: 2
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
```

- [ ] **Step 4: 创建 CLAUDE.md**

```markdown
# 报奖信息监测系统 - 项目规范

## 项目概述
定时抓取规划行业报奖信息，通过关键词+AI筛选后推送到微信。

## 目录结构
- `config.yaml` — 所有配置（不进git）
- `main.py` — 程序入口
- `crawlers/` — 爬虫模块，每个文件一种爬虫类型
- `filters/` — 筛选模块（关键词+AI）
- `notifiers/` — 通知模块（PushPlus）
- `storage/` — 数据存储（SQLite）
- `utils/` — 工具模块（日志、HTTP客户端）
- `data/` — 运行数据（不进git）
- `logs/` — 日志文件（不进git）

## 新增网站流程
1. 在 config.yaml 的 sources.websites 中添加一条配置
2. 设置 name、url、type（gov_general 或 org_general）、enabled: true
3. 如果网站结构特殊，通用爬虫无法解析，在 crawlers/ 下新建适配器继承 BaseCrawler

## 配置修改规范
- 关键词增删：直接改 config.yaml 的 filter.keywords / filter.exclude_keywords
- 临时关闭某个源：设 enabled: false
- 切换AI模型：改 filter.ai.model

## 安全约定
- PushPlus token 只放 config.yaml，不进git
- config.yaml 在 .gitignore 中排除
- 日志和数据文件不进git

## 运行方式
- 手动运行：`python main.py`
- 定时运行：双击 setup_task.bat 注册Windows计划任务（每天09:00和21:00）
```

- [ ] **Step 5: 创建目录结构和 __init__.py**

创建以下空文件（Python包标识）：
- `utils/__init__.py`
- `storage/__init__.py`
- `crawlers/__init__.py`
- `filters/__init__.py`
- `notifiers/__init__.py`

- [ ] **Step 6: Commit**

```bash
cd "g:/AI/报奖信息监测"
git init
git add CLAUDE.md .gitignore requirements.txt
git commit -m "chore: 项目脚手架 - CLAUDE.md, .gitignore, requirements.txt"
```

注意：config.yaml 不 add（在 .gitignore 中排除），但需创建 config.yaml.example 作为模板供git追踪。

---

### Task 2: 日志模块

**Files:**
- Create: `g:/AI/报奖信息监测/utils/logger.py`

- [ ] **Step 1: 实现 logger.py**

```python
"""日志配置模块"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler


def setup_logger(log_dir: str = "logs", level: str = "INFO", max_days: int = 30):
    """配置全局日志器，按天滚动，保留 max_days 天。

    Args:
        log_dir: 日志目录
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        max_days: 保留天数
    """
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("monitor")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 文件handler：按天滚动
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "monitor.log"),
        when="midnight",
        interval=1,
        backupCount=max_days,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"

    # 控制台handler
    console_handler = logging.StreamHandler()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger():
    """获取已配置的日志器"""
    return logging.getLogger("monitor")
```

- [ ] **Step 2: Commit**

```bash
git add utils/__init__.py utils/logger.py
git commit -m "feat: 日志模块 - 按天滚动，保留30天"
```

---

### Task 3: HTTP客户端封装

**Files:**
- Create: `g:/AI/报奖信息监测/utils/http_client.py`

- [ ] **Step 1: 实现 http_client.py**

```python
"""HTTP请求封装 - 含重试、超时、UA伪装"""

import time
import requests
from utils.logger import get_logger

logger = get_logger()


def fetch_html(
    url: str,
    timeout: int = 15,
    retry_times: int = 3,
    retry_delay: float = 2,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    encoding: str = None,
) -> str | None:
    """请求页面，返回HTML文本。失败返回None。

    Args:
        url: 目标URL
        timeout: 超时秒数
        retry_times: 重试次数
        retry_delay: 重试间隔秒数
        user_agent: User-Agent字符串
        encoding: 强制编码（部分政府网站需要），None则自动检测

    Returns:
        HTML文本或None（全部失败时）
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    for attempt in range(1, retry_times + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()

            if encoding:
                resp.encoding = encoding
            else:
                # 尝试从内容判断编码，fallback到utf-8
                if resp.encoding and resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding

            logger.debug(f"请求成功: {url} (第{attempt}次尝试)")
            return resp.text

        except requests.RequestException as e:
            logger.warning(
                f"请求失败: {url} (第{attempt}/{retry_times}次) - {type(e).__name__}: {e}"
            )
            if attempt < retry_times:
                time.sleep(retry_delay)

    logger.error(f"请求全部失败: {url}")
    return None
```

- [ ] **Step 2: Commit**

```bash
git add utils/http_client.py
git commit -m "feat: HTTP客户端封装 - 重试、超时、UA伪装、编码检测"
```

---

### Task 4: 数据存储模块

**Files:**
- Create: `g:/AI/报奖信息监测/storage/database.py`

- [ ] **Step 1: 实现 database.py**

```python
"""SQLite数据存储 - 建表、插入、去重查询、状态更新"""

import os
import sqlite3
import json
from datetime import datetime, timedelta
from utils.logger import get_logger

logger = get_logger()


class Database:
    def __init__(self, db_path: str = "data/monitor.db", dedup_days: int = 90):
        self.db_path = db_path
        self.dedup_days = dedup_days
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """创建表结构"""
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL,
                    url         TEXT UNIQUE NOT NULL,
                    source      TEXT NOT NULL,
                    publish_date TEXT,
                    summary     TEXT,
                    raw_content TEXT,
                    status      TEXT DEFAULT 'new',
                    ai_reason   TEXT,
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    pushed_at   TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_time    TEXT DEFAULT (datetime('now','localtime')),
                    total_articles   INTEGER,
                    new_articles     INTEGER,
                    keyword_passed   INTEGER,
                    ai_confirmed     INTEGER,
                    pushed           INTEGER,
                    errors           TEXT
                )
                """
            )
            # 创建索引加速去重查询
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_title_source ON articles(title, source)"
            )
        logger.debug("数据库初始化完成")

    def is_duplicate(self, url: str, title: str, source: str) -> bool:
        """检查是否已存在（URL匹配 或 标题+来源匹配），只对比最近 dedup_days 天的记录"""
        cutoff = (datetime.now() - timedelta(days=self.dedup_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._get_conn() as conn:
            # URL去重
            row = conn.execute(
                "SELECT 1 FROM articles WHERE url = ? AND created_at >= ?",
                (url, cutoff),
            ).fetchone()
            if row:
                return True

            # 标题+来源兜底去重
            row = conn.execute(
                "SELECT 1 FROM articles WHERE title = ? AND source = ? AND created_at >= ?",
                (title, source, cutoff),
            ).fetchone()
            if row:
                return True

        return False

    def insert_article(
        self,
        title: str,
        url: str,
        source: str,
        publish_date: str = None,
        summary: str = None,
        raw_content: str = None,
        status: str = "new",
    ) -> int | None:
        """插入一条文章记录，返回id。URL已存在则返回None。"""
        try:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO articles
                        (title, url, source, publish_date, summary, raw_content, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (title, url, source, publish_date, summary, raw_content, status),
                )
                if cursor.rowcount > 0:
                    return cursor.lastrowid
                return None
        except sqlite3.IntegrityError:
            return None

    def update_status(
        self,
        article_id: int,
        status: str,
        ai_reason: str = None,
        pushed: bool = False,
    ):
        """更新文章状态"""
        with self._get_conn() as conn:
            if pushed:
                conn.execute(
                    "UPDATE articles SET status = ?, ai_reason = ?, pushed_at = datetime('now','localtime') WHERE id = ?",
                    (status, ai_reason, article_id),
                )
            else:
                conn.execute(
                    "UPDATE articles SET status = ?, ai_reason = ? WHERE id = ?",
                    (status, ai_reason, article_id),
                )

    def get_article_by_id(self, article_id: int) -> dict | None:
        """按id获取文章"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            return dict(row) if row else None

    def insert_run_log(
        self,
        total_articles: int,
        new_articles: int,
        keyword_passed: int,
        ai_confirmed: int,
        pushed: int,
        errors: list = None,
    ):
        """记录本次运行日志"""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO run_logs
                    (total_articles, new_articles, keyword_passed, ai_confirmed, pushed, errors)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    total_articles,
                    new_articles,
                    keyword_passed,
                    ai_confirmed,
                    pushed,
                    json.dumps(errors, ensure_ascii=False) if errors else None,
                ),
            )
```

- [ ] **Step 2: Commit**

```bash
git add storage/__init__.py storage/database.py
git commit -m "feat: 数据存储模块 - SQLite建表、去重、状态更新、运行日志"
```

---

### Task 5: 爬虫基类

**Files:**
- Create: `g:/AI/报奖信息监测/crawlers/base.py`

- [ ] **Step 1: 实现 base.py**

```python
"""爬虫基类 - 模板方法模式，定义统一接口"""

from dataclasses import dataclass, field
from utils.logger import get_logger

logger = get_logger()


@dataclass
class Article:
    """统一文章数据结构"""
    title: str
    url: str
    source: str
    publish_date: str = ""
    summary: str = ""
    raw_content: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "publish_date": self.publish_date,
            "summary": self.summary,
            "raw_content": self.raw_content,
        }


class BaseCrawler:
    """所有爬虫的基类，子类需实现 _request_page 和 _parse"""

    def __init__(self, name: str, url: str, config: dict):
        self.name = name
        self.url = url
        self.config = config  # 完整config字典

    def fetch(self) -> list[Article]:
        """抓取页面，返回文章列表。失败返回空列表。"""
        logger.info(f"开始抓取: {self.name}")
        try:
            html = self._request_page()
            if not html:
                logger.warning(f"抓取失败（页面为空）: {self.name}")
                return []
            articles = self._parse(html)
            logger.info(f"抓取完成: {self.name} ({len(articles)}条)")
            return articles
        except Exception as e:
            logger.warning(f"抓取异常: {self.name} - {type(e).__name__}: {e}")
            return []

    def _request_page(self) -> str | None:
        """子类实现：请求页面，返回HTML文本"""
        raise NotImplementedError

    def _parse(self, html: str) -> list[Article]:
        """子类实现：解析HTML，返回Article列表"""
        raise NotImplementedError
```

- [ ] **Step 2: Commit**

```bash
git add crawlers/__init__.py crawlers/base.py
git commit -m "feat: 爬虫基类 - BaseCrawler模板方法模式 + Article数据结构"
```

---

### Task 6: 政府网站通用爬虫

**Files:**
- Create: `g:/AI/报奖信息监测/crawlers/gov_general.py`

- [ ] **Step 1: 实现 gov_general.py**

```python
"""政府网站通用爬虫 - 适配典型 gov.cn 页面结构"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()


class GovGeneralCrawler(BaseCrawler):
    """政府网站通用爬虫

    适配典型结构：<ul><li><a href="...">标题</a><span>日期</span></li></ul>
    或 <table><tr><td><a>标题</a></td><td>日期</td></tr></table>
    """

    def _request_page(self) -> str | None:
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=self.url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=crawler_config.get("retry_times", 3),
            retry_delay=crawler_config.get("retry_delay", 2),
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # 策略1: 查找列表中的链接（最常见的政府网站结构）
        # 匹配 <li><a href="...">标题</a> ... 日期</li>
        items = soup.select("ul li a") + soup.select("table td a") + soup.select("div.list a")

        seen_urls = set()
        for a_tag in items:
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue
            if len(title) < 6:  # 过滤太短的（可能是导航按钮）
                continue
            if href.startswith("javascript:") or href.startswith("#"):
                continue

            full_url = urljoin(self.url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # 尝试从父元素中提取日期
            publish_date = self._extract_date(a_tag)

            articles.append(
                Article(
                    title=title,
                    url=full_url,
                    source=self.name,
                    publish_date=publish_date,
                    summary="",
                )
            )

        return articles

    def _extract_date(self, a_tag) -> str:
        """从a标签的父元素中提取日期"""
        parent = a_tag.parent
        if parent:
            text = parent.get_text()
            # 匹配 YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY年MM月DD日
            date_match = re.search(
                r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", text
            )
            if date_match:
                date_str = date_match.group(1)
                # 统一为 YYYY-MM-DD
                date_str = date_str.replace("年", "-").replace("月", "-").replace("日", "")
                date_str = date_str.replace(".", "-").replace("/", "-")
                return date_str
        return ""
```

- [ ] **Step 2: Commit**

```bash
git add crawlers/gov_general.py
git commit -m "feat: 政府网站通用爬虫 - 列表/表格解析、日期提取、URL补全"
```

---

### Task 7: 学会/协会网站通用爬虫

**Files:**
- Create: `g:/AI/报奖信息监测/crawlers/org_general.py`

- [ ] **Step 1: 实现 org_general.py**

```python
"""学会/协会网站通用爬虫 - 适配各协会网站不同结构"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()


class OrgGeneralCrawler(BaseCrawler):
    """学会/协会网站通用爬虫

    学会协会网站结构差异较大，采用更宽松的解析策略：
    1. 查找所有带href的a标签
    2. 过滤导航/底部等无关链接
    3. 按标题长度和关键词相关性筛选
    """

    # 与报奖相关的标题关键词，用于过滤无关文章
    RELEVANT_KEYWORDS = [
        "奖", "申报", "推荐", "提名", "评选", "通知", "公告",
        "开展", "组织", "征集", "报名", "转发", "公示",
    ]

    def _request_page(self) -> str | None:
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=self.url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=crawler_config.get("retry_times", 3),
            retry_delay=crawler_config.get("retry_delay", 2),
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # 查找所有a标签
        all_links = soup.find_all("a", href=True)

        seen_urls = set()
        for a_tag in all_links:
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue
            if len(title) < 8:  # 协会网站过滤更严格
                continue
            if href.startswith("javascript:") or href.startswith("#"):
                continue
            if href.startswith("mailto:") or href.startswith("tel:"):
                continue

            full_url = urljoin(self.url, href)
            if full_url in seen_urls:
                continue
            # 排除站外导航链接（但保留文章链接）
            if self._is_navigation_link(a_tag):
                continue
            seen_urls.add(full_url)

            # 检查是否与报奖相关
            if not any(kw in title for kw in self.RELEVANT_KEYWORDS):
                continue

            publish_date = self._extract_date(a_tag)

            articles.append(
                Article(
                    title=title,
                    url=full_url,
                    source=self.name,
                    publish_date=publish_date,
                    summary="",
                )
            )

        return articles

    def _is_navigation_link(self, a_tag) -> bool:
        """判断是否为导航/菜单链接（而非文章链接）"""
        # 检查父元素是否为 nav, menu 等导航区域
        for parent in a_tag.parents:
            if parent.name in ("nav", "header", "footer"):
                return True
            parent_class = " ".join(parent.get("class", []))
            if any(
                nav_kw in parent_class.lower()
                for nav_kw in ["nav", "menu", "header", "footer", "sidebar"]
            ):
                return True
        return False

    def _extract_date(self, a_tag) -> str:
        """从a标签的父元素中提取日期"""
        parent = a_tag.parent
        if parent:
            text = parent.get_text()
            date_match = re.search(
                r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", text
            )
            if date_match:
                date_str = date_match.group(1)
                date_str = date_str.replace("年", "-").replace("月", "-").replace("日", "")
                date_str = date_str.replace(".", "-").replace("/", "-")
                return date_str
        return ""
```

- [ ] **Step 2: Commit**

```bash
git add crawlers/org_general.py
git commit -m "feat: 学会/协会网站通用爬虫 - 宽松解析、相关性过滤、导航排除"
```

---

### Task 8: 搜狗微信搜索爬虫

**Files:**
- Create: `g:/AI/报奖信息监测/crawlers/sogo_wechat.py`

- [ ] **Step 1: 实现 sogo_wechat.py**

```python
"""搜狗微信搜索爬虫 - 按公众号名称搜索最新文章"""

import re
import time
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()

# 搜狗微信搜索URL模板
SOGO_SEARCH_URL = "https://weixin.sogou.com/weixin?type=2&query={}&ie=utf8"


class SogoWechatCrawler(BaseCrawler):
    """搜狗微信搜索爬虫

    按公众号名称搜索文章，提取搜索结果中的文章列表。
    搜狗有反爬机制，失败时返回空列表，由上层决定是否降级到feeddd。
    """

    def __init__(self, name: str, keyword: str, config: dict):
        super().__init__(name=name, url="", config=config)
        self.keyword = keyword

    def fetch(self) -> list[Article]:
        """重写fetch，搜狗需要特殊处理"""
        logger.info(f"开始搜狗微信搜索: {self.keyword}")
        self._fail_count = 0
        try:
            html = self._request_page()
            if not html:
                self._fail_count += 1
                return []
            articles = self._parse(html)
            if not articles:
                self._fail_count += 1
            else:
                logger.info(f"搜狗搜索完成: {self.keyword} ({len(articles)}条)")
            return articles
        except Exception as e:
            logger.warning(f"搜狗搜索异常: {self.keyword} - {type(e).__name__}: {e}")
            self._fail_count += 1
            return []

    @property
    def fail_count(self) -> int:
        return getattr(self, "_fail_count", 0)

    def _request_page(self) -> str | None:
        search_url = SOGO_SEARCH_URL.format(quote(self.keyword))
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=search_url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=2,  # 搜狗减少重试次数，避免触发更严格的封禁
            retry_delay=5,  # 搜狗重试间隔更长
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # 搜狗微信搜索结果结构：<div class="news-box"> ... <h3><a href="...">标题</a></h3> ...
        news_items = soup.select("div.news-box") or soup.select("div.txt-box")

        for item in news_items:
            a_tag = item.select_one("h3 a") or item.select_one("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue

            # 搜狗的链接是相对路径，需要拼接
            if href.startswith("/"):
                full_url = urljoin("https://weixin.sogou.com", href)
            else:
                full_url = href

            # 提取摘要
            summary = ""
            summary_tag = item.select_one("p.txt-info") or item.select_one("p")
            if summary_tag:
                summary = summary_tag.get_text(strip=True)

            # 提取公众号名称和日期
            account_name = ""
            account_tag = item.select_one("a.account")
            if account_tag:
                account_name = account_tag.get_text(strip=True)

            # 提取日期（搜狗显示的是相对时间或绝对日期）
            publish_date = ""
            time_tag = item.select_one("span.s2") or item.select_one("span.time")
            if time_tag:
                date_text = time_tag.get_text(strip=True)
                date_match = re.search(r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", date_text)
                if date_match:
                    publish_date = date_match.group(1).replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-").replace("/", "-")

            articles.append(
                Article(
                    title=title,
                    url=full_url,
                    source=f"微信公众号:{account_name or self.keyword}",
                    publish_date=publish_date,
                    summary=summary,
                )
            )

        return articles
```

- [ ] **Step 2: Commit**

```bash
git add crawlers/sogo_wechat.py
git commit -m "feat: 搜狗微信搜索爬虫 - 按公众号名称搜索、反爬处理、fail_count跟踪"
```

---

### Task 9: feeddd降级爬虫

**Files:**
- Create: `g:/AI/报奖信息监测/crawlers/feeddd_fallback.py`

- [ ] **Step 1: 实现 feeddd_fallback.py**

```python
"""feeddd降级爬虫 - 搜狗不可用时的备用公众号文章源"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from crawlers.base import BaseCrawler, Article
from utils.http_client import fetch_html
from utils.logger import get_logger

logger = get_logger()

# feeddd搜索URL
FEEDDD_SEARCH_URL = "https://feeddd.org/feeds/search?keyword={}"


class FeedddFallbackCrawler(BaseCrawler):
    """feeddd降级爬虫

    通过 feeddd.org 搜索公众号文章。
    feeddd 是免费服务，不保证长期稳定，作为搜狗的降级方案。
    """

    def __init__(self, name: str, keyword: str, config: dict):
        super().__init__(name=name, url="", config=config)
        self.keyword = keyword

    def fetch(self) -> list[Article]:
        logger.info(f"feeddd降级搜索: {self.keyword}")
        try:
            html = self._request_page()
            if not html:
                return []
            articles = self._parse(html)
            logger.info(f"feeddd搜索完成: {self.keyword} ({len(articles)}条)")
            return articles
        except Exception as e:
            logger.warning(f"feeddd搜索异常: {self.keyword} - {type(e).__name__}: {e}")
            return []

    def _request_page(self) -> str | None:
        search_url = FEEDDD_SEARCH_URL.format(self.keyword)
        crawler_config = self.config.get("crawler", {})
        return fetch_html(
            url=search_url,
            timeout=crawler_config.get("timeout", 15),
            retry_times=2,
            retry_delay=3,
            user_agent=crawler_config.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )

    def _parse(self, html: str) -> list[Article]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # feeddd 页面结构可能变化，采用宽松解析
        items = soup.select("div.feed-item") or soup.select("article") or soup.select("div.item")

        for item in items:
            a_tag = item.select_one("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")

            if not title or not href:
                continue

            full_url = urljoin("https://feeddd.org", href)

            summary = ""
            summary_tag = item.select_one("p") or item.select_one("div.summary")
            if summary_tag:
                summary = summary_tag.get_text(strip=True)

            publish_date = ""
            date_tag = item.select_one("time") or item.select_one("span.date")
            if date_tag:
                date_text = date_tag.get_text(strip=True)
                date_match = re.search(r"(\d{4}[-./年]\d{1,2}[-./月]\d{1,2})", date_text)
                if date_match:
                    publish_date = date_match.group(1).replace("年", "-").replace("月", "-").replace("日", "").replace(".", "-").replace("/", "-")

            articles.append(
                Article(
                    title=title,
                    url=full_url,
                    source=f"微信公众号:{self.keyword}",
                    publish_date=publish_date,
                    summary=summary,
                )
            )

        return articles
```

- [ ] **Step 2: Commit**

```bash
git add crawlers/feeddd_fallback.py
git commit -m "feat: feeddd降级爬虫 - 搜狗不可用时的备用公众号文章源"
```

---

### Task 10: 关键词筛选模块

**Files:**
- Create: `g:/AI/报奖信息监测/filters/keyword_filter.py`

- [ ] **Step 1: 实现 keyword_filter.py**

```python
"""关键词初筛 - 正向关键词命中 + 排除关键词不命中"""

import re
from crawlers.base import Article
from utils.logger import get_logger

logger = get_logger()


class KeywordFilter:
    def __init__(self, keywords: list[str], exclude_keywords: list[str]):
        self.keywords = keywords
        self.exclude_keywords = exclude_keywords
        # 编译正则关键词（支持 "开展.*奖" 这类模式）
        self._keyword_patterns = [re.compile(kw, re.IGNORECASE) for kw in keywords]
        self._exclude_patterns = [
            re.compile(ek, re.IGNORECASE) for ek in exclude_keywords
        ]

    def filter(self, article: Article) -> bool:
        """判断文章是否通过关键词初筛。

        规则：
        1. 标题或摘要命中任意正向关键词 → 通过初筛候选
        2. 标题或摘要命中任意排除关键词 → 直接拒绝
        3. 两者都命中 → 排除词优先，拒绝

        Args:
            article: 文章对象

        Returns:
            True=通过初筛, False=未通过
        """
        text = f"{article.title} {article.summary}"

        # 先检查排除词
        for pattern in self._exclude_patterns:
            if pattern.search(text):
                logger.debug(f"排除词命中 [{pattern.pattern}]: {article.title}")
                return False

        # 再检查正向关键词
        for pattern in self._keyword_patterns:
            if pattern.search(text):
                logger.debug(f"关键词命中 [{pattern.pattern}]: {article.title}")
                return True

        return False

    def batch_filter(self, articles: list[Article]) -> list[Article]:
        """批量筛选，返回通过的文章列表"""
        passed = [a for a in articles if self.filter(a)]
        logger.info(f"关键词初筛: {len(articles)}条 → {len(passed)}条通过")
        return passed
```

- [ ] **Step 2: Commit**

```bash
git add filters/__init__.py filters/keyword_filter.py
git commit -m "feat: 关键词筛选模块 - 正向关键词+排除词，支持正则模式"
```

---

### Task 11: AI二次确认模块

**Files:**
- Create: `g:/AI/报奖信息监测/filters/ai_filter.py`

- [ ] **Step 1: 实现 ai_filter.py**

```python
"""AI二次确认 - 调用本地Ollama判断是否为报奖申报通知"""

import json
import requests
from crawlers.base import Article
from utils.logger import get_logger

logger = get_logger()

# 判断Prompt
JUDGE_PROMPT = """你是一个报奖信息筛选助手。请判断以下信息是否为"报奖申报通知"。

判断标准：
- 是：信息内容是通知组织/单位开展某项奖项的申报、推荐或提名工作，且处于申报期内
- 否：信息是获奖公示、评审结果、会议通知、培训通知、招标采购等

信息标题：{title}
信息摘要：{summary}

请只回答JSON：{{"is_award_application": true/false, "reason": "简要理由"}}"""


class AIFilter:
    def __init__(
        self,
        api_url: str = "http://localhost:11434/api/chat",
        model: str = "qwen3.5:latest",
        max_summary_length: int = 500,
        timeout: int = 30,
        enabled: bool = True,
    ):
        self.api_url = api_url
        self.model = model
        self.max_summary_length = max_summary_length
        self.timeout = timeout
        self.enabled = enabled

    def judge(self, article: Article) -> tuple[bool, str]:
        """判断单篇文章是否为报奖申报通知。

        Args:
            article: 文章对象

        Returns:
            (is_award_application, reason) 元组
            AI不可用时返回 (True, "AI不可用，保守推送")
        """
        if not self.enabled:
            return True, "AI未启用，直接通过"

        summary = (article.summary or article.raw_content or "")[: self.max_summary_length]

        prompt = JUDGE_PROMPT.format(title=article.title, summary=summary)

        try:
            response = self._call_ollama(prompt)
            if response is None:
                # AI不可用，保守策略
                logger.warning(f"AI不可用，保守推送: {article.title}")
                return True, "AI不可用，保守推送"

            is_award, reason = self._parse_response(response)
            logger.debug(f"AI判断: {article.title} → {is_award} ({reason})")
            return is_award, reason

        except Exception as e:
            logger.warning(f"AI判断异常: {article.title} - {type(e).__name__}: {e}")
            return True, f"AI异常，保守推送: {e}"

    def batch_judge(self, articles: list[Article]) -> list[tuple[Article, bool, str]]:
        """批量判断，返回 [(article, is_award, reason), ...]"""
        results = []
        for article in articles:
            is_award, reason = self.judge(article)
            results.append((article, is_award, reason))

        confirmed = sum(1 for _, is_award, _ in results if is_award)
        logger.info(f"AI二次确认: {len(articles)}条 → {confirmed}条通过")
        return results

    def _call_ollama(self, prompt: str) -> str | None:
        """调用Ollama API"""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": 0.1,  # 低温度，提高判断一致性
                "num_predict": 200,
            },
        }

        resp = requests.post(
            self.api_url,
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        data = resp.json()
        # Ollama /api/chat 返回格式: {"message": {"content": "..."}}
        content = data.get("message", {}).get("content", "")
        if not content:
            logger.warning("Ollama返回空内容")
            return None

        return content

    def _parse_response(self, content: str) -> tuple[bool, str]:
        """解析AI返回的JSON"""
        # 尝试直接解析JSON
        try:
            # qwen3.5可能输出 <think>...</think> 标签，需要去除
            import re
            content_clean = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

            # 尝试提取JSON部分
            json_match = re.search(r'\{[^}]+\}', content_clean)
            if json_match:
                data = json.loads(json_match.group())
                return bool(data.get("is_award_application", False)), data.get("reason", "")
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"AI返回解析失败: {content[:100]}... - {e}")

        # 解析失败，保守推送
        return True, "AI返回解析失败，保守推送"
```

- [ ] **Step 2: Commit**

```bash
git add filters/ai_filter.py
git commit -m "feat: AI二次确认模块 - Ollama本地调用、JSON解析、保守策略"
```

---

### Task 12: PushPlus推送模块

**Files:**
- Create: `g:/AI/报奖信息监测/notifiers/pushplus.py`

- [ ] **Step 1: 实现 pushplus.py**

```python
"""PushPlus微信推送模块"""

import time
import requests
from crawlers.base import Article
from utils.logger import get_logger

logger = get_logger()

PUSHPLUS_URL = "https://www.pushplus.plus/send"


class PushPlusNotifier:
    def __init__(self, token: str, topic: str = "", retry_times: int = 3):
        self.token = token
        self.topic = topic
        self.retry_times = retry_times

    def notify(self, articles: list[Article]) -> bool:
        """推送文章列表到微信。

        Args:
            articles: 待推送的文章列表

        Returns:
            True=推送成功, False=推送失败
        """
        if not articles:
            logger.info("无新信息，跳过推送")
            return True

        content = self._build_content(articles)
        title = f"报奖信息监测 ({len(articles)}条新信息)"

        for attempt in range(1, self.retry_times + 1):
            try:
                payload = {
                    "token": self.token,
                    "title": title,
                    "content": content,
                    "template": "txt",
                }
                if self.topic:
                    payload["topic"] = self.topic

                resp = requests.post(PUSHPLUS_URL, json=payload, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") == 200:
                    logger.info(f"PushPlus推送成功 ({len(articles)}条)")
                    return True
                else:
                    logger.warning(
                        f"PushPlus推送失败: {data.get('msg', '未知错误')} (第{attempt}次)"
                    )
            except Exception as e:
                logger.warning(
                    f"PushPlus推送异常: {type(e).__name__}: {e} (第{attempt}次)"
                )

            if attempt < self.retry_times:
                time.sleep(2)

        logger.error("PushPlus推送全部失败")
        return False

    def notify_alert(self, message: str) -> bool:
        """推送告警消息"""
        for attempt in range(1, self.retry_times + 1):
            try:
                payload = {
                    "token": self.token,
                    "title": "报奖监测告警",
                    "content": message,
                    "template": "txt",
                }
                resp = requests.post(PUSHPLUS_URL, json=payload, timeout=15)
                data = resp.json()
                if data.get("code") == 200:
                    return True
            except Exception as e:
                logger.warning(f"告警推送异常 (第{attempt}次): {e}")
            if attempt < self.retry_times:
                time.sleep(2)
        return False

    def _build_content(self, articles: list[Article]) -> str:
        """构建推送消息内容"""
        parts = [f"📰 报奖信息监测 (本次发现 {len(articles)} 条)\n"]

        for i, article in enumerate(articles, 1):
            part = f"""━━━━━━━━━━━━━━━━
📌 {article.title}
来源：{article.source} | 日期：{article.publish_date or '未知'}
摘要：{article.summary[:200] if article.summary else '无摘要'}
🔗 {article.url}"""
            parts.append(part)

        parts.append("━━━━━━━━━━━━━━━━")
        return "\n\n".join(parts)
```

- [ ] **Step 2: Commit**

```bash
git add notifiers/__init__.py notifiers/pushplus.py
git commit -m "feat: PushPlus推送模块 - 多条合并、重试、告警通知"
```

---

### Task 13: 主程序入口

**Files:**
- Create: `g:/AI/报奖信息监测/main.py`

- [ ] **Step 1: 实现 main.py**

```python
"""报奖信息监测系统 - 主入口

运行流水线：
读取配置 → 并发爬取 → 去重 → 关键词初筛 → AI确认 → 推送 → 记录
"""

import sys
import os
import time
import yaml

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logger, get_logger
from storage.database import Database
from crawlers.base import Article
from crawlers.gov_general import GovGeneralCrawler
from crawlers.org_general import OrgGeneralCrawler
from crawlers.sogo_wechat import SogoWechatCrawler
from crawlers.feeddd_fallback import FeedddFallbackCrawler
from filters.keyword_filter import KeywordFilter
from filters.ai_filter import AIFilter
from notifiers.pushplus import PushPlusNotifier


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_crawlers(config: dict) -> tuple[list[Article], list[str]]:
    """运行所有爬虫，返回 (所有文章列表, 错误列表)"""
    all_articles = []
    errors = []
    sources_config = config.get("sources", {})

    # 网站爬虫
    crawler_map = {
        "gov_general": GovGeneralCrawler,
        "org_general": OrgGeneralCrawler,
    }

    for site in sources_config.get("websites", []):
        if not site.get("enabled", True):
            continue

        crawler_class = crawler_map.get(site.get("type", "org_general"), OrgGeneralCrawler)
        crawler = crawler_class(
            name=site["name"],
            url=site["url"],
            config=config,
        )
        articles = crawler.fetch()
        if not articles and crawler.fetch:  # fetch() 已被调用
            # 如果抓取返回空，可能是正常（无新文章）也可能是失败
            pass
        all_articles.extend(articles)

    # 微信公众号爬虫（搜狗 → feeddd降级）
    for account in sources_config.get("wechat_accounts", []):
        if not account.get("enabled", True):
            continue

        sogo_crawler = SogoWechatCrawler(
            name=account["name"],
            keyword=account["keyword"],
            config=config,
        )
        articles = sogo_crawler.fetch()

        # 搜狗失败3次，降级到feeddd
        if sogo_crawler.fail_count >= 3 or not articles:
            logger = get_logger()
            logger.info(f"搜狗失败，降级到feeddd: {account['keyword']}")
            feeddd_crawler = FeedddFallbackCrawler(
                name=account["name"],
                keyword=account["keyword"],
                config=config,
            )
            fallback_articles = feeddd_crawler.fetch()
            if fallback_articles:
                articles = fallback_articles
            else:
                errors.append(f"公众号渠道失败: {account['name']}")

        all_articles.extend(articles)

    return all_articles, errors


def main():
    # 加载配置
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    config = load_config(config_path)

    # 初始化日志
    log_config = config.get("logging", {})
    setup_logger(
        log_dir=log_config.get("dir", "logs"),
        level=log_config.get("level", "INFO"),
        max_days=log_config.get("max_days", 30),
    )
    logger = get_logger()
    logger.info("=" * 50)
    logger.info("开始运行报奖信息监测")

    start_time = time.time()

    # 初始化各模块
    storage_config = config.get("storage", {})
    db = Database(
        db_path=storage_config.get("db_path", "data/monitor.db"),
        dedup_days=storage_config.get("dedup_days", 90),
    )

    filter_config = config.get("filter", {})
    keyword_filter = KeywordFilter(
        keywords=filter_config.get("keywords", []),
        exclude_keywords=filter_config.get("exclude_keywords", []),
    )

    ai_config = filter_config.get("ai", {})
    ai_filter = AIFilter(
        api_url=ai_config.get("api_url", "http://localhost:11434/api/chat"),
        model=ai_config.get("model", "qwen3.5:latest"),
        max_summary_length=ai_config.get("max_summary_length", 500),
        timeout=ai_config.get("timeout", 30),
        enabled=ai_config.get("enabled", True),
    )

    notif_config = config.get("notification", {}).get("pushplus", {})
    notifier = PushPlusNotifier(
        token=notif_config.get("token", ""),
        topic=notif_config.get("topic", ""),
    )

    # Step 1: 爬取
    all_articles, errors = run_crawlers(config)
    total_count = len(all_articles)
    logger.info(f"爬取完成，共 {total_count} 条")

    # 全部失败告警
    if total_count == 0:
        if errors:
            notifier.notify_alert("⚠️ 本次抓取全部失败，请检查程序和网站状态。\n错误: " + "; ".join(errors))
        else:
            logger.info("本次无任何文章被抓取")
        db.insert_run_log(0, 0, 0, 0, 0, errors)
        logger.info("运行结束")
        return

    # Step 2: 去重 + 入库
    new_articles = []
    for article in all_articles:
        if not db.is_duplicate(article.url, article.title, article.source):
            article_id = db.insert_article(
                title=article.title,
                url=article.url,
                source=article.source,
                publish_date=article.publish_date,
                summary=article.summary,
                raw_content=article.raw_content,
                status="new",
            )
            if article_id:
                article._db_id = article_id  # 临时存储db id
                new_articles.append(article)
            else:
                # 插入失败说明已存在
                pass

    logger.info(f"去重后新增: {len(new_articles)} 条")

    if not new_articles:
        logger.info("无新信息需要处理")
        db.insert_run_log(total_count, 0, 0, 0, 0, errors)
        logger.info("运行结束")
        return

    # Step 3: 关键词初筛
    keyword_passed = keyword_filter.batch_filter(new_articles)
    for article in keyword_passed:
        if hasattr(article, "_db_id"):
            db.update_status(article._db_id, "keyword_passed")

    if not keyword_passed:
        logger.info("关键词初筛无通过项")
        db.insert_run_log(total_count, len(new_articles), 0, 0, 0, errors)
        logger.info("运行结束")
        return

    # Step 4: AI二次确认
    ai_results = ai_filter.batch_judge(keyword_passed)
    confirmed_articles = []

    for article, is_award, reason in ai_results:
        if hasattr(article, "_db_id"):
            if is_award:
                db.update_status(article._db_id, "ai_confirmed", ai_reason=reason)
                confirmed_articles.append(article)
            else:
                db.update_status(article._db_id, "ai_rejected", ai_reason=reason)

    logger.info(f"AI确认通过: {len(confirmed_articles)} 条")

    # Step 5: 推送
    pushed_count = 0
    if confirmed_articles:
        success = notifier.notify(confirmed_articles)
        if success:
            for article in confirmed_articles:
                if hasattr(article, "_db_id"):
                    db.update_status(article._db_id, "pushed", pushed=True)
            pushed_count = len(confirmed_articles)
        else:
            errors.append("PushPlus推送失败")

    # Step 6: 记录运行日志
    elapsed = time.time() - start_time
    db.insert_run_log(
        total_articles=total_count,
        new_articles=len(new_articles),
        keyword_passed=len(keyword_passed),
        ai_confirmed=len(confirmed_articles),
        pushed=pushed_count,
        errors=errors,
    )

    logger.info(
        f"运行结束，耗时{elapsed:.1f}秒 | "
        f"总计{total_count} → 新增{len(new_articles)} → "
        f"初筛{len(keyword_passed)} → AI确认{len(confirmed_articles)} → "
        f"推送{pushed_count}"
    )
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: 主程序入口 - 完整流水线编排（爬取→去重→筛选→AI→推送→记录）"
```

---

### Task 14: 定时任务脚本

**Files:**
- Create: `g:/AI/报奖信息监测/setup_task.bat`

- [ ] **Step 1: 创建 setup_task.bat**

```bat
@echo off
chcp 65001 >nul
echo ====================================
echo  报奖信息监测 - 定时任务注册
echo ====================================
echo.

:: 获取脚本所在目录
set PROJECT_DIR=%~dp0
set PROJECT_DIR=%PROJECT_DIR:~0,-1%

:: 注册上午任务（09:00）
schtasks /create /tn "报奖信息监测-上午" /tr "pythonw \"%PROJECT_DIR%\main.py\"" /sc daily /st 09:00 /f
if %errorlevel% equ 0 (
    echo [OK] 上午任务注册成功 (每天 09:00)
) else (
    echo [FAIL] 上午任务注册失败
)

:: 注册晚上任务（21:00）
schtasks /create /tn "报奖信息监测-晚上" /tr "pythonw \"%PROJECT_DIR%\main.py\"" /sc daily /st 21:00 /f
if %errorlevel% equ 0 (
    echo [OK] 晚上任务注册成功 (每天 21:00)
) else (
    echo [FAIL] 晚上任务注册失败
)

echo.
echo ====================================
echo  注册完成！
echo  上午: 每天 09:00
echo  晚上: 每天 21:00
echo ====================================
echo.
echo  如需删除定时任务，运行:
echo  schtasks /delete /tn "报奖信息监测-上午" /f
echo  schtasks /delete /tn "报奖信息监测-晚上" /f
echo.
pause
```

- [ ] **Step 2: Commit**

```bash
git add setup_task.bat
git commit -m "feat: 定时任务注册脚本 - Windows计划任务一键注册"
```

---

### Task 15: config.yaml.example 和最终验证

**Files:**
- Create: `g:/AI/报奖信息监测/config.yaml.example`
- Create: `g:/AI/报奖信息监测/data/.gitkeep`
- Create: `g:/AI/报奖信息监测/logs/.gitkeep`

- [ ] **Step 1: 创建 config.yaml.example**

将 Task 1 Step 3 中的 config.yaml 内容复制为 config.yaml.example（token字段改为占位提示）。这个文件会进git，作为模板。

```yaml
# 报奖信息监测系统配置文件模板
# 使用方法：复制此文件为 config.yaml，填入你的实际配置
# cp config.yaml.example config.yaml

# ... (与 Task 1 Step 3 内容相同，但 token 字段改为 "在此填入你的PushPlus Token")
```

- [ ] **Step 2: 创建目录占位文件**

创建 `data/.gitkeep` 和 `logs/.gitkeep`（空文件），确保目录结构被git追踪。

- [ ] **Step 3: 安装依赖并验证**

```bash
cd "g:/AI/报奖信息监测"
pip install -r requirements.txt
python -c "from utils.logger import setup_logger; setup_logger(); print('OK')"
python -c "from storage.database import Database; db = Database(); print('OK')"
python -c "from crawlers.base import BaseCrawler, Article; print('OK')"
python -c "from filters.keyword_filter import KeywordFilter; kf = KeywordFilter(['申报'], ['招标']); print('OK')"
python -c "from filters.ai_filter import AIFilter; ai = AIFilter(); print('OK')"
python -c "from notifiers.pushplus import PushPlusNotifier; n = PushPlusNotifier('test'); print('OK')"
```

全部输出 OK 表示模块导入正常。

- [ ] **Step 4: 手动运行测试**

```bash
cd "g:/AI/报奖信息监测"
python main.py
```

观察日志输出，确认流水线正常执行。

- [ ] **Step 5: 最终Commit**

```bash
git add config.yaml.example data/.gitkeep logs/.gitkeep
git commit -m "chore: 配置模板、目录占位、最终验证"
```

---

## 实现完成后的使用步骤

1. `cp config.yaml.example config.yaml`
2. 在 config.yaml 中填入 PushPlus Token
3. 确认 Ollama 运行中：`ollama list` 检查 qwen3.5:latest 可用
4. `pip install -r requirements.txt`
5. `python main.py` 手动运行一次验证
6. 双击 `setup_task.bat` 注册定时任务
