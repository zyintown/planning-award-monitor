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
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
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
