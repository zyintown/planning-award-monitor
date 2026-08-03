"""SQLite 数据存储、schema 迁移、状态流转与渠道健康记录。"""

import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from crawlers.base import Article
from utils.logger import get_logger
from utils.normalization import canonicalize_url, make_dedup_key, normalize_title


logger = get_logger()

SCHEMA_VERSION = 3
PIPELINE_VERSION = 2
RETRYABLE_STATUSES = ("ai_pending", "ready_to_push", "push_failed")


class Database:
    def __init__(
        self,
        db_path: str = "data/monitor.db",
        dedup_days: int = 90,
        auto_backup: bool = True,
    ):
        self.db_path = db_path
        self.dedup_days = dedup_days
        self.auto_backup = auto_backup
        self._memory_conn: sqlite3.Connection | None = None

        if db_path == ":memory:":
            self._memory_conn = self._configure_conn(sqlite3.connect(":memory:", timeout=10))
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _configure_conn(self, conn: sqlite3.Connection) -> sqlite3.Connection:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        return self._configure_conn(sqlite3.connect(self.db_path, timeout=10))

    @property
    def schema_version(self) -> int:
        with self._get_conn() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _needs_articles_migration(self, conn: sqlite3.Connection) -> bool:
        if not self._table_exists(conn, "articles"):
            return False
        required = {
            "canonical_url",
            "normalized_title",
            "dedup_key",
            "last_error",
            "attempt_count",
            "pipeline_version",
            "updated_at",
        }
        if not required.issubset(self._columns(conn, "articles")):
            return True
        for index in conn.execute("PRAGMA index_list(articles)"):
            if index["unique"]:
                index_columns = [
                    row["name"]
                    for row in conn.execute(f"PRAGMA index_info({index['name']})")
                ]
                if index_columns == ["url"]:
                    return True
        return False

    def _backup_before_migration(self) -> Path | None:
        if not self.auto_backup or self.db_path == ":memory:" or not os.path.exists(self.db_path):
            return None
        source = Path(self.db_path)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_dir = source.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{source.stem}-pre-v{SCHEMA_VERSION}-{timestamp}{source.suffix}"
        shutil.copy2(source, target)
        logger.info(f"数据库迁移前备份完成: {target}")
        return target

    def _init_db(self):
        conn = self._get_conn()
        needs_migration = self._needs_articles_migration(conn)
        current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        has_existing_schema = self._table_exists(conn, "articles")
        if has_existing_schema and (needs_migration or current_version < SCHEMA_VERSION):
            self._backup_before_migration()

        try:
            conn.execute("BEGIN IMMEDIATE")
            if not self._table_exists(conn, "articles"):
                self._create_articles_table(conn)
            elif needs_migration:
                self._migrate_articles_v1_to_v2(conn)

            self._backfill_normalized_fields(conn)
            self._create_or_migrate_run_logs(conn)
            self._create_source_runs_table(conn)
            self._create_article_extractions_table(conn)
            self._create_indexes(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        logger.debug(f"数据库初始化完成 (schema v{SCHEMA_VERSION})")

    def _create_articles_table(self, conn: sqlite3.Connection):
        conn.execute(
            """
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
                pipeline_version INTEGER NOT NULL DEFAULT 2,
                created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                pushed_at        TEXT
            )
            """
        )

    def _migrate_articles_v1_to_v2(self, conn: sqlite3.Connection):
        logger.info("开始迁移 articles schema v1 → v2")
        conn.execute("ALTER TABLE articles RENAME TO articles_legacy_v1")
        self._create_articles_table(conn)
        conn.execute(
            """
            INSERT INTO articles (
                id, title, normalized_title, url, canonical_url, dedup_key,
                source, publish_date, summary, raw_content, status, ai_reason,
                last_error, attempt_count, pipeline_version, created_at,
                updated_at, pushed_at
            )
            SELECT
                id, title, lower(trim(title)), url, url, '',
                source, publish_date, summary, raw_content, status, ai_reason,
                NULL, 0, 1, created_at, created_at, pushed_at
            FROM articles_legacy_v1
            """
        )
        conn.execute("DROP TABLE articles_legacy_v1")
        logger.info("articles schema v2 迁移完成；历史记录 pipeline_version=1，不进入自动重试")

    def _backfill_normalized_fields(self, conn: sqlite3.Connection):
        """补齐旧记录的派生字段，不改变标题、链接、状态等原始业务数据。"""
        rows = conn.execute(
            """
            SELECT id, title, url, source, publish_date
            FROM articles
            WHERE dedup_key = '' OR normalized_title = '' OR canonical_url = ''
            """
        ).fetchall()
        if not rows:
            return
        for row in rows:
            conn.execute(
                """
                UPDATE articles
                SET normalized_title=?, canonical_url=?, dedup_key=?
                WHERE id=?
                """,
                (
                    normalize_title(row["title"]),
                    canonicalize_url(row["url"]),
                    make_dedup_key(
                        row["title"],
                        row["url"],
                        row["source"],
                        row["publish_date"] or "",
                    ),
                    row["id"],
                ),
            )
        logger.info(f"已回填 {len(rows)} 条历史记录的规范化去重字段")

    def _create_or_migrate_run_logs(self, conn: sqlite3.Connection):
        if not self._table_exists(conn, "run_logs"):
            conn.execute(
                """
                CREATE TABLE run_logs (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_time           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    finished_at        TEXT,
                    status             TEXT NOT NULL DEFAULT 'running',
                    duration_seconds   REAL,
                    total_articles     INTEGER DEFAULT 0,
                    new_articles       INTEGER DEFAULT 0,
                    keyword_passed     INTEGER DEFAULT 0,
                    ai_confirmed       INTEGER DEFAULT 0,
                    pushed             INTEGER DEFAULT 0,
                    errors             TEXT
                )
                """
            )
            return

        columns = self._columns(conn, "run_logs")
        additions = {
            "finished_at": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "duration_seconds": "REAL",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE run_logs ADD COLUMN {name} {definition}")

    def _create_source_runs_table(self, conn: sqlite3.Connection):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_runs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER NOT NULL,
                source           TEXT NOT NULL,
                source_type      TEXT NOT NULL,
                status           TEXT NOT NULL,
                article_count    INTEGER NOT NULL DEFAULT 0,
                new_count        INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL,
                detail_success   INTEGER NOT NULL DEFAULT 0,
                detail_failed    INTEGER NOT NULL DEFAULT 0,
                error            TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (run_id) REFERENCES run_logs(id)
            )
            """
        )

    def _create_article_extractions_table(self, conn: sqlite3.Connection):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS article_extractions (
                article_id             INTEGER PRIMARY KEY,
                is_award_application   INTEGER NOT NULL CHECK (is_award_application IN (0, 1)),
                award_name             TEXT,
                deadline_text          TEXT,
                deadline_date          TEXT,
                applicant_scope        TEXT NOT NULL DEFAULT '[]',
                model                  TEXT NOT NULL,
                prompt_version         TEXT NOT NULL,
                raw_response           TEXT,
                created_at             TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at             TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
            )
            """
        )

    def _create_indexes(self, conn: sqlite3.Connection):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_canonical_url ON articles(canonical_url, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_title_source ON articles(normalized_title, source, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status_pipeline ON articles(status, pipeline_version)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_dedup_key ON articles(dedup_key, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_source_runs_source ON source_runs(source, id DESC)")

    def _duplicate_row(
        self,
        conn: sqlite3.Connection,
        canonical_url: str,
        normalized_title: str,
        source: str,
    ) -> sqlite3.Row | None:
        cutoff = (datetime.now() - timedelta(days=self.dedup_days)).strftime("%Y-%m-%d %H:%M:%S")
        return conn.execute(
            """
            SELECT id FROM articles
            WHERE created_at >= ?
              AND ((canonical_url <> '' AND canonical_url = ?)
                   OR (normalized_title = ? AND source = ?))
            ORDER BY id DESC LIMIT 1
            """,
            (cutoff, canonical_url, normalized_title, source),
        ).fetchone()

    def is_duplicate(self, url: str, title: str, source: str) -> bool:
        try:
            with self._get_conn() as conn:
                return self._duplicate_row(
                    conn, canonicalize_url(url), normalize_title(title), source
                ) is not None
        except sqlite3.OperationalError as exc:
            logger.warning(f"去重查询失败，按未见过处理: {exc}")
            return False

    def add_discovered(self, article: Article) -> tuple[int, bool]:
        canonical_url = canonicalize_url(article.url)
        normalized_title = normalize_title(article.title)
        dedup_key = make_dedup_key(
            article.title, article.url, article.source, article.publish_date
        )
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = self._duplicate_row(conn, canonical_url, normalized_title, article.source)
            if duplicate:
                conn.commit()
                return int(duplicate["id"]), False
            cursor = conn.execute(
                """
                INSERT INTO articles (
                    title, normalized_title, url, canonical_url, dedup_key,
                    source, publish_date, summary, raw_content, status,
                    pipeline_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?)
                """,
                (
                    article.title,
                    normalized_title,
                    article.url,
                    canonical_url,
                    dedup_key,
                    article.source,
                    article.publish_date,
                    article.summary,
                    article.raw_content,
                    PIPELINE_VERSION,
                ),
            )
            conn.commit()
            article.db_id = int(cursor.lastrowid)
            return article.db_id, True
        except Exception:
            conn.rollback()
            raise

    def insert_article(
        self,
        title: str,
        url: str,
        source: str,
        publish_date: str = None,
        summary: str = None,
        raw_content: str = None,
        status: str = "discovered",
    ) -> int | None:
        article = Article(
            title=title,
            url=url,
            source=source,
            publish_date=publish_date or "",
            summary=summary or "",
            raw_content=raw_content or "",
        )
        article_id, is_new = self.add_discovered(article)
        if is_new and status != "discovered":
            self.transition_status(article_id, status)
        return article_id if is_new else None

    def _row_to_article(self, row: sqlite3.Row) -> Article:
        applicant_scope = []
        if "applicant_scope" in row.keys() and row["applicant_scope"]:
            try:
                parsed = json.loads(row["applicant_scope"])
                if isinstance(parsed, list):
                    applicant_scope = [str(item) for item in parsed]
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"文章 {row['id']} 的 applicant_scope 不是有效JSON数组")
        return Article(
            title=row["title"],
            url=row["url"],
            source=row["source"],
            publish_date=row["publish_date"] or "",
            summary=row["summary"] or "",
            raw_content=row["raw_content"] or "",
            db_id=int(row["id"]),
            award_name=(row["award_name"] or "") if "award_name" in row.keys() else "",
            deadline_text=(row["deadline_text"] or "") if "deadline_text" in row.keys() else "",
            deadline_date=(row["deadline_date"] or "") if "deadline_date" in row.keys() else "",
            applicant_scope=applicant_scope,
            created_at=(row["created_at"] or "") if "created_at" in row.keys() else "",
            status=(row["status"] or "") if "status" in row.keys() else "",
        )

    def get_articles_by_status(
        self,
        statuses: list[str] | tuple[str, ...],
        min_pipeline_version: int = PIPELINE_VERSION,
    ) -> list[Article]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT a.*, e.award_name, e.deadline_text, e.deadline_date,
                       e.applicant_scope
                FROM articles AS a
                LEFT JOIN article_extractions AS e ON e.article_id = a.id
                WHERE a.status IN ({placeholders}) AND a.pipeline_version >= ?
                ORDER BY a.id
                """,
                (*statuses, min_pipeline_version),
            ).fetchall()
        return [self._row_to_article(row) for row in rows]

    def upsert_article_extraction(
        self,
        article_id: int,
        is_award_application: bool,
        award_name: str,
        deadline_text: str,
        deadline_date: str,
        applicant_scope: list[str] | tuple[str, ...],
        model: str,
        prompt_version: str,
        raw_response: str,
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO article_extractions (
                    article_id, is_award_application, award_name, deadline_text,
                    deadline_date, applicant_scope, model, prompt_version,
                    raw_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    is_award_application=excluded.is_award_application,
                    award_name=excluded.award_name,
                    deadline_text=excluded.deadline_text,
                    deadline_date=excluded.deadline_date,
                    applicant_scope=excluded.applicant_scope,
                    model=excluded.model,
                    prompt_version=excluded.prompt_version,
                    raw_response=excluded.raw_response,
                    updated_at=datetime('now','localtime')
                """,
                (
                    article_id,
                    int(is_award_application),
                    award_name or None,
                    deadline_text or None,
                    deadline_date or None,
                    json.dumps(list(applicant_scope), ensure_ascii=False),
                    model,
                    prompt_version,
                    raw_response or None,
                ),
            )

    def get_article_extraction(self, article_id: int) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM article_extractions WHERE article_id=?",
                (article_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["applicant_scope"] = json.loads(result["applicant_scope"] or "[]")
        except (json.JSONDecodeError, TypeError):
            result["applicant_scope"] = []
        return result

    def update_article_content(
        self,
        article_id: int,
        summary: str,
        raw_content: str,
        url: str | None = None,
    ):
        with self._get_conn() as conn:
            if url:
                row = conn.execute(
                    "SELECT title, source, publish_date FROM articles WHERE id=?",
                    (article_id,),
                ).fetchone()
                canonical_url = canonicalize_url(url)
                dedup_key = make_dedup_key(
                    row["title"], url, row["source"], row["publish_date"] or ""
                )
                conn.execute(
                    """
                    UPDATE articles SET summary=?, raw_content=?, url=?, canonical_url=?,
                        dedup_key=?, updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (summary, raw_content, url, canonical_url, dedup_key, article_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE articles SET summary=?, raw_content=?, updated_at=datetime('now','localtime')
                    WHERE id=?
                    """,
                    (summary, raw_content, article_id),
                )

    def update_article_provenance(
        self,
        article_id: int,
        source: str,
        url: str,
        reason: str,
    ):
        """升级已推送记录的来源溯源，不改变其 pushed 状态。"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT title, source, publish_date, ai_reason FROM articles WHERE id=?",
                (article_id,),
            ).fetchone()
            if not row:
                return
            old_source = row["source"] or ""
            old_reason = row["ai_reason"] or ""
            audit = f"来源升级: {old_source} -> {source}; 原因: {reason}"
            merged_reason = f"{old_reason}; {audit}" if old_reason else audit
            canonical_url = canonicalize_url(url)
            dedup_key = make_dedup_key(
                row["title"], url, source, row["publish_date"] or ""
            )
            conn.execute(
                """
                UPDATE articles SET source=?, url=?, canonical_url=?, dedup_key=?,
                    ai_reason=?, updated_at=datetime('now','localtime')
                WHERE id=?
                """,
                (source, url, canonical_url, dedup_key, merged_reason, article_id),
            )

    def transition_status(
        self,
        article_id: int,
        status: str,
        ai_reason: str | None = None,
        last_error: str | None = None,
        increment_attempt: bool = False,
        pushed: bool = False,
        clear_error: bool = False,
    ):
        assignments = ["status = ?", "updated_at = datetime('now','localtime')"]
        params: list = [status]
        if ai_reason is not None:
            assignments.append("ai_reason = ?")
            params.append(ai_reason)
        if last_error is not None:
            assignments.append("last_error = ?")
            params.append(last_error)
        elif clear_error:
            assignments.append("last_error = NULL")
        if increment_attempt:
            assignments.append("attempt_count = attempt_count + 1")
        if pushed:
            assignments.append("pushed_at = datetime('now','localtime')")
        params.append(article_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE articles SET {', '.join(assignments)} WHERE id = ?", params
            )

    def update_status(
        self,
        article_id: int,
        status: str,
        ai_reason: str = None,
        pushed: bool = False,
    ):
        self.transition_status(
            article_id,
            status,
            ai_reason=ai_reason,
            pushed=pushed,
            clear_error=pushed,
        )

    def get_article_by_id(self, article_id: int) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
            return dict(row) if row else None

    def start_run(self) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute("INSERT INTO run_logs (status) VALUES ('running')")
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        total_articles: int = 0,
        new_articles: int = 0,
        keyword_passed: int = 0,
        ai_confirmed: int = 0,
        pushed: int = 0,
        errors: list[str] | None = None,
        duration_seconds: float | None = None,
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE run_logs SET
                    finished_at=datetime('now','localtime'), status=?, duration_seconds=?,
                    total_articles=?, new_articles=?, keyword_passed=?,
                    ai_confirmed=?, pushed=?, errors=?
                WHERE id=?
                """,
                (
                    status,
                    duration_seconds,
                    total_articles,
                    new_articles,
                    keyword_passed,
                    ai_confirmed,
                    pushed,
                    json.dumps(errors, ensure_ascii=False) if errors else None,
                    run_id,
                ),
            )

    def insert_run_log(
        self,
        total_articles: int,
        new_articles: int,
        keyword_passed: int,
        ai_confirmed: int,
        pushed: int,
        errors: list = None,
    ):
        run_id = self.start_run()
        self.finish_run(
            run_id,
            status="completed_with_errors" if errors else "completed",
            total_articles=total_articles,
            new_articles=new_articles,
            keyword_passed=keyword_passed,
            ai_confirmed=ai_confirmed,
            pushed=pushed,
            errors=errors,
        )

    def record_source_run(
        self,
        run_id: int,
        source: str,
        source_type: str,
        status: str,
        article_count: int,
        new_count: int,
        duration_seconds: float,
        error: str = "",
        detail_success: int = 0,
        detail_failed: int = 0,
    ):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO source_runs (
                    run_id, source, source_type, status, article_count, new_count,
                    duration_seconds, detail_success, detail_failed, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source,
                    source_type,
                    status,
                    article_count,
                    new_count,
                    duration_seconds,
                    detail_success,
                    detail_failed,
                    error or None,
                ),
            )

    def get_source_baseline(self, source: str, limit: int = 5) -> int:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT article_count FROM source_runs
                WHERE source=? AND status='success' AND article_count > 0
                ORDER BY id DESC LIMIT ?
                """,
                (source, limit),
            ).fetchall()
        if not rows:
            return 0
        values = sorted(int(row["article_count"]) for row in rows)
        return values[len(values) // 2]

    def get_consecutive_source_failures(self, source: str, limit: int = 20) -> int:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT status FROM source_runs WHERE source=? ORDER BY id DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        count = 0
        for row in rows:
            if row["status"] in {"failed", "partial"}:
                count += 1
            else:
                break
        return count
