"""报奖信息监测系统主入口。"""

import argparse
import copy
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from crawlers.base import Article, CrawlResult, enrich_article
from crawlers.cacp_api import CacpApiCrawler
from crawlers.chsla_api import ChslaApiCrawler
from crawlers.gov_general import GovGeneralCrawler
from crawlers.org_general import OrgGeneralCrawler
from crawlers.sogo_wechat import SogoWechatCrawler, filter_backfill_window
from filters.ai_filter import AIFilter
from filters.keyword_filter import KeywordFilter
from filters.title_gate import TitleGate
from notifiers.feishu import FeishuNotifier
from storage.database import Database
from utils.event_identity import (
    aggregator_due,
    completeness_score,
    event_key,
    source_is_aggregator,
    source_priority,
)
from utils.logger import get_logger, setup_logger
from utils.process_lock import ProcessLock


class ConfigError(ValueError):
    pass


def load_config(config_path: str | Path = "config.yaml") -> dict:
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ConfigError("配置文件顶层必须是对象")
    return config


def resolve_config_paths(config: dict, base_dir: Path = PROJECT_ROOT) -> dict:
    """将数据库与日志相对路径固定到项目目录，避免计划任务工作目录漂移。"""
    resolved = copy.deepcopy(config)
    storage = resolved.setdefault("storage", {})
    db_path = Path(storage.get("db_path", "data/monitor.db"))
    if not db_path.is_absolute():
        db_path = base_dir / db_path
    storage["db_path"] = str(db_path.resolve())

    logging_config = resolved.setdefault("logging", {})
    log_dir = Path(logging_config.get("dir", "logs"))
    if not log_dir.is_absolute():
        log_dir = base_dir / log_dir
    logging_config["dir"] = str(log_dir.resolve())

    ai_config = resolved.setdefault("filter", {}).setdefault("ai", {})
    api_key_file = Path(ai_config.get("api_key_file", "local.env"))
    if not api_key_file.is_absolute():
        api_key_file = base_dir / api_key_file
    ai_config["api_key_file"] = str(api_key_file.resolve())
    return resolved


def validate_config(config: dict, dry_run: bool = False):
    sources = config.get("sources")
    if not isinstance(sources, dict):
        raise ConfigError("缺少 sources 配置")

    crawler_types = {"gov_general", "org_general", "cacp_api", "chsla_api"}
    enabled_count = 0
    names = set()
    for site in sources.get("websites", []):
        if not isinstance(site, dict):
            raise ConfigError("sources.websites 每项必须是对象")
        if not site.get("enabled", True):
            continue
        enabled_count += 1
        for field in ("name", "url"):
            if not site.get(field):
                raise ConfigError(f"启用的网站缺少 {field}: {site}")
        if site.get("type", "org_general") not in crawler_types:
            raise ConfigError(f"未知爬虫类型: {site.get('type')}")
        identity = ("website", site["name"])
        if identity in names:
            raise ConfigError(f"网站名称重复: {site['name']}")
        names.add(identity)

    for account in sources.get("wechat_accounts", []):
        if not isinstance(account, dict):
            raise ConfigError("sources.wechat_accounts 每项必须是对象")
        if not account.get("enabled", True):
            continue
        enabled_count += 1
        if not account.get("name") or not account.get("keyword"):
            raise ConfigError(f"启用的公众号缺少 name/keyword: {account}")

    if enabled_count == 0:
        raise ConfigError("至少需要启用一个信息源")

    filter_config = config.get("filter", {})
    if not isinstance(filter_config.get("keywords"), list) or not filter_config.get("keywords"):
        raise ConfigError("filter.keywords 必须是非空列表")
    if not isinstance(filter_config.get("exclude_keywords", []), list):
        raise ConfigError("filter.exclude_keywords 必须是列表")

    ai_config = filter_config.get("ai", {})
    if ai_config.get("enabled", True) and (
        not ai_config.get("api_url") or not ai_config.get("model")
    ):
        raise ConfigError("启用 AI 时必须配置 filter.ai.api_url 和 model")
    if ai_config.get("enabled", True):
        provider = str(ai_config.get("provider", "ollama")).lower()
        if provider not in {"ollama", "agnes"}:
            raise ConfigError(f"未知 AI provider: {provider}")
        fallback = ai_config.get("fallback", {}) or {}
        fallback_provider = str(fallback.get("provider", "")).lower()
        if fallback_provider and fallback_provider not in {"ollama", "agnes"}:
            raise ConfigError(f"未知 AI fallback provider: {fallback_provider}")
        if fallback_provider and (
            not fallback.get("api_url") or not fallback.get("model")
        ):
            raise ConfigError("启用 AI fallback 时必须配置 fallback.api_url 和 model")
    max_workers = ai_config.get("max_workers", 1)
    if not isinstance(max_workers, int) or not 1 <= max_workers <= 4:
        raise ConfigError("filter.ai.max_workers 必须是1到4之间的整数")

    dedup_days = config.get("storage", {}).get("dedup_days", 90)
    if not isinstance(dedup_days, int) or dedup_days < 1:
        raise ConfigError("storage.dedup_days 必须是正整数")

    retry_delays = config.get("health", {}).get(
        "all_failed_retry_delays_minutes", [30, 90]
    )
    if (
        not isinstance(retry_delays, list)
        or any(not isinstance(delay, (int, float)) or delay < 0 for delay in retry_delays)
    ):
        raise ConfigError("health.all_failed_retry_delays_minutes 必须是非负数字列表")

    webhook_url = config.get("notification", {}).get("feishu", {}).get("webhook_url", "")
    if not dry_run and not webhook_url:
        raise ConfigError("非 dry-run 模式必须配置飞书 webhook_url")


def prepare_dry_run_config(config: dict) -> tuple[dict, Path]:
    """复制生产数据库供 dry-run 使用；副本保留，绝不写生产库。"""
    dry_config = copy.deepcopy(config)
    source_db = Path(dry_config.get("storage", {}).get("db_path", PROJECT_ROOT / "data/monitor.db"))
    dry_dir = PROJECT_ROOT / "data" / "dry-runs"
    dry_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dry_db = dry_dir / f"monitor-dry-run-{timestamp}.db"
    if source_db.exists():
        shutil.copy2(source_db, dry_db)
    dry_config.setdefault("storage", {})["db_path"] = str(dry_db)
    return dry_config, dry_db


def source_matches(
    source_filter: str | None,
    source_name: str,
    source_type: str,
) -> bool:
    """匹配 CLI 渠道过滤器；支持 website:/wechat: 前缀消除同名歧义。"""
    if not source_filter:
        return True
    if ":" in source_filter:
        prefix, value = source_filter.split(":", 1)
        if prefix in {"website", "wechat"}:
            return prefix == source_type and value == source_name
    return source_filter in {source_name, source_type}


def topic_account_aliases(accounts: list[dict]) -> list[str]:
    """主题检索保留显式白名单，即使对应单账号抓取已停用。"""
    aliases = []
    for account in accounts:
        if not account.get("enabled", True) and not account.get(
            "topic_match_enabled", False
        ):
            continue
        aliases.extend([account.get("name", ""), *(account.get("account_aliases") or [])])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def run_crawlers(config: dict, source_filter: str | None = None) -> list[CrawlResult]:
    """运行全部启用渠道，并保留每个渠道的真实成功/失败状态。"""
    logger = get_logger()
    results: list[CrawlResult] = []
    sources_config = config.get("sources", {})
    crawler_map = {
        "gov_general": GovGeneralCrawler,
        "org_general": OrgGeneralCrawler,
        "cacp_api": CacpApiCrawler,
        "chsla_api": ChslaApiCrawler,
    }

    for site in sources_config.get("websites", []):
        if not site.get("enabled", True):
            continue
        if not source_matches(
            source_filter,
            site["name"],
            site.get("type", "org_general"),
        ) and not source_matches(source_filter, site["name"], "website"):
            continue
        started_at = time.monotonic()
        try:
            crawler_class = crawler_map[site.get("type", "org_general")]
            crawler = crawler_class(
                name=site["name"],
                url=site["url"],
                config=config,
                pagination=site.get("pagination", {}),
                max_pages_override=site.get("max_pages"),
                ssl_verify=site.get("ssl_verify", True),
            )
            result = crawler.fetch_result()
            industry_keywords = site.get("industry_keywords")
            if industry_keywords and result.articles:
                before = len(result.articles)
                result.articles = [
                    article
                    for article in result.articles
                    if any(keyword in article.title for keyword in industry_keywords)
                ]
                logger.info(f"行业过滤[{site['name']}]: {before} → {len(result.articles)} 条")
            result.metrics.setdefault("raw_result_count", len(result.articles))
            result.metrics.setdefault("account_matched_count", len(result.articles))
            result.metrics["window_kept_count"] = len(result.articles)
            results.append(result)
        except Exception as exc:
            results.append(
                CrawlResult(
                    source=site.get("name", "未知网站"),
                    source_type="website",
                    status="failed",
                    articles=[],
                    duration_seconds=time.monotonic() - started_at,
                    error=f"初始化或抓取异常 {type(exc).__name__}: {exc}",
                )
            )

    wechat_config = config.get("crawler", {}).get("wechat", {})
    backfill_days = int(wechat_config.get("backfill_days", 3))
    interval_seconds = float(wechat_config.get("interval_seconds", 3))
    accounts = [
        account
        for account in sources_config.get("wechat_accounts", [])
        if account.get("enabled", True)
        and source_matches(source_filter, account["name"], "wechat")
    ]
    for index, account in enumerate(accounts):
        crawler = SogoWechatCrawler(
            name=account["name"],
            keyword=account["keyword"],
            config=config,
            account_aliases=account.get("account_aliases"),
        )
        result = crawler.fetch_result()
        result.source = f"微信公众号:{account['name']}"
        before = len(result.articles)
        result.articles = filter_backfill_window(result.articles, days=backfill_days)
        result.metrics["window_kept_count"] = len(result.articles)
        filtered = before - len(result.articles)
        if filtered:
            logger.info(
                f"公众号[{account['name']}]: 丢弃{filtered}条超过{backfill_days}天补抓窗口的文章"
            )
        results.append(result)
        if index < len(accounts) - 1 and interval_seconds > 0:
            time.sleep(interval_seconds)

    topic_config = wechat_config.get("topic_search", {})
    current_year = datetime.now().year
    topic_queries = [
        str(query).replace("{year}", str(current_year))
        for query in topic_config.get("queries", [])
        if str(query).strip()
    ]
    topic_source = "微信公众号主题检索"
    if (
        topic_config.get("enabled", True)
        and topic_queries
        and source_matches(source_filter, topic_source, "wechat")
    ):
        crawler = SogoWechatCrawler(
            name=topic_source,
            keyword=topic_queries[0],
            config=config,
            account_aliases=topic_account_aliases(
                sources_config.get("wechat_accounts", [])
            ),
            search_queries=topic_queries,
            max_pages=int(topic_config.get("max_pages", 2)),
            interval_seconds=float(
                topic_config.get("interval_seconds", interval_seconds)
            ),
            require_account_match=True,
        )
        result = crawler.fetch_result()
        result.source = topic_source
        before = len(result.articles)
        result.articles = filter_backfill_window(result.articles, days=backfill_days)
        result.metrics["window_kept_count"] = len(result.articles)
        filtered = before - len(result.articles)
        if filtered:
            logger.info(f"公众号主题检索: 丢弃{filtered}条超过{backfill_days}天补抓窗口的文章")
        results.append(result)

    return results


def run_crawlers_with_retries(
    config: dict,
    source_filter: str | None = None,
    sleep_fn=None,
) -> tuple[list[CrawlResult], int]:
    """全源失败时按配置重试；单源过滤、部分失败和空结果不触发重试。"""
    crawl_results = run_crawlers(config, source_filter=source_filter)
    if source_filter or not crawl_results:
        return crawl_results, 0
    if not all(result.status == "failed" for result in crawl_results):
        return crawl_results, 0

    delays = config.get("health", {}).get(
        "all_failed_retry_delays_minutes", [30, 90]
    )
    sleep_fn = sleep_fn or time.sleep
    retry_count = 0
    for delay in delays:
        logger = get_logger()
        logger.warning(
            f"全部信息源抓取失败，{delay}分钟后进行第{retry_count + 1}次重试"
        )
        sleep_fn(float(delay) * 60)
        crawl_results = run_crawlers(config, source_filter=source_filter)
        retry_count += 1
        if not crawl_results or not all(
            result.status == "failed" for result in crawl_results
        ):
            break
    return crawl_results, retry_count


def write_source_semantics(
    run_id: int,
    result: CrawlResult,
    db_path: str | Path,
    status: str | None = None,
):
    """将公众号语义计数写入 JSONL，不改数据库 schema。"""
    if not result.metrics or str(db_path) == ":memory:":
        return
    report_path = Path(db_path).resolve().parent / "reports" / "source_semantics.jsonl"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "source": result.source,
        "source_type": result.source_type,
        "status": status or result.status,
        "metrics": result.metrics,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    with report_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def process_discovered_articles(
    db: Database,
    config: dict,
    keyword_filter: KeywordFilter,
) -> tuple[int, dict[str, tuple[int, int]]]:
    """处理所有 v2 discovered，进程中断后下次可从该状态继续。"""
    logger = get_logger()
    title_gate = TitleGate()
    details: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    keyword_passed = 0
    detail_config = config.get("crawler", {}).get("detail", {})
    detail_enabled = detail_config.get("enabled", True)
    min_existing_length = int(detail_config.get("min_existing_length", 120))

    for article in db.get_articles_by_status(["discovered"]):
        if detail_enabled and len(article.raw_content or article.summary) < min_existing_length:
            article, detail_error = enrich_article(article, config)
            if detail_error:
                details[article.source][1] += 1
                logger.warning(f"详情补充失败: {article.title} - {detail_error}")
            else:
                details[article.source][0] += 1
                db.update_article_content(
                    article.db_id,
                    article.summary,
                    article.raw_content,
                    url=article.url,
                )

        title_allowed, title_reason = title_gate.check(article)
        if not title_allowed:
            db.transition_status(article.db_id, "keyword_rejected", ai_reason=title_reason)
        elif keyword_filter.filter(article):
            db.transition_status(article.db_id, "ai_pending")
            keyword_passed += 1
        else:
            db.transition_status(article.db_id, "keyword_rejected", ai_reason="未命中P3关键词")

    return keyword_passed, {
        source: (counts[0], counts[1]) for source, counts in details.items()
    }


def process_ai_pending(db: Database, ai_filter: AIFilter) -> tuple[int, int, int]:
    confirmed = rejected = pending = 0
    articles = db.get_articles_by_status(["ai_pending"])
    for article, result in ai_filter.batch_judge(articles):
        if result.decision is True:
            db.upsert_article_extraction(
                article.db_id,
                is_award_application=True,
                award_name=result.award_name,
                deadline_text=result.deadline_text,
                deadline_date=result.deadline_date,
                applicant_scope=result.applicant_scope,
                model=ai_filter.model,
                prompt_version=ai_filter.prompt_version,
                raw_response=result.raw_response,
            )
            db.transition_status(
                article.db_id,
                "ready_to_push",
                ai_reason=result.reason,
                clear_error=True,
            )
            confirmed += 1
        elif result.decision is False:
            db.upsert_article_extraction(
                article.db_id,
                is_award_application=False,
                award_name=result.award_name,
                deadline_text=result.deadline_text,
                deadline_date=result.deadline_date,
                applicant_scope=result.applicant_scope,
                model=ai_filter.model,
                prompt_version=ai_filter.prompt_version,
                raw_response=result.raw_response,
            )
            db.transition_status(
                article.db_id,
                "ai_rejected",
                ai_reason=result.reason,
                clear_error=True,
            )
            rejected += 1
        else:
            db.transition_status(
                article.db_id,
                "ai_pending",
                ai_reason=result.reason,
                last_error=result.reason,
                increment_attempt=True,
            )
            pending += 1
    return confirmed, rejected, pending


def process_pending_notifications(
    db: Database,
    notifier,
    dry_run: bool = False,
    config: dict | None = None,
    now: datetime | None = None,
) -> int:
    """按事件和来源等级归并后推送；dry-run 不调用外部服务也不改变状态。"""
    logger = get_logger()
    articles = db.get_articles_by_status(["ready_to_push", "push_failed"])
    if not articles:
        return 0
    if dry_run:
        logger.info(f"dry-run: 有 {len(articles)} 条待推送，已禁止真实通知且保持原状态")
        return 0

    config = config or {}
    already_pushed = db.get_articles_by_status(["pushed"])
    pending_by_event: dict[str, list[Article]] = defaultdict(list)
    pushed_by_event: dict[str, list[Article]] = defaultdict(list)
    for article in articles:
        pending_by_event[event_key(article)].append(article)
    for article in already_pushed:
        pushed_by_event[event_key(article)].append(article)

    candidates: list[Article] = []
    delay_hours = int(
        config.get("notification", {}).get("aggregator_delay_hours", 24)
    )

    def ranking(article: Article):
        return (
            source_priority(article.source, config),
            completeness_score(article),
            article.publish_date or "",
            article.db_id or 0,
        )

    for key, pending_group in pending_by_event.items():
        pushed_group = pushed_by_event.get(key, [])
        if pushed_group:
            best = max([*pending_group, *pushed_group], key=ranking)
            if best in pushed_group:
                for article in pending_group:
                    db.transition_status(
                        article.db_id,
                        "source_superseded",
                        ai_reason=f"事件 {key} 已有更高或同等级来源完成推送",
                    )
            else:
                pushed_best = max(pushed_group, key=ranking)
                db.update_article_provenance(
                    pushed_best.db_id,
                    best.source,
                    best.url,
                    reason=f"事件 {key} 发现更高等级来源，未重复通知",
                )
                for article in pending_group:
                    db.transition_status(
                        article.db_id,
                        "source_superseded",
                        ai_reason=f"事件 {key} 已完成推送，来源已升级",
                    )
            continue

        winner = max(pending_group, key=ranking)
        for article in pending_group:
            if article.db_id != winner.db_id:
                db.transition_status(
                    article.db_id,
                    "source_superseded",
                    ai_reason=f"事件 {key} 由更高优先级来源主导",
                )
        if source_is_aggregator(winner.source, config) and not aggregator_due(
            winner, now=now, delay_hours=delay_hours
        ):
            logger.info(f"聚合站来源延迟中: {winner.title} ({delay_hours}小时窗口)")
            continue
        candidates.append(winner)

    pushed_count = 0
    if not candidates:
        return 0
    for batch_result in notifier.notify_batches(candidates):
        for article in batch_result.articles:
            if batch_result.success:
                db.transition_status(
                    article.db_id,
                    "pushed",
                    pushed=True,
                    clear_error=True,
                )
                pushed_count += 1
            else:
                db.transition_status(
                    article.db_id,
                    "push_failed",
                    last_error=batch_result.error or "飞书推送失败",
                    increment_attempt=True,
                )
    return pushed_count


def run_pipeline(
    config: dict,
    dry_run: bool = False,
    source_filter: str | None = None,
    db: Database | None = None,
    notifier=None,
) -> dict:
    logger = get_logger()
    started_at = time.monotonic()
    storage_config = config.get("storage", {})
    db = db or Database(
        db_path=storage_config.get("db_path", str(PROJECT_ROOT / "data/monitor.db")),
        dedup_days=storage_config.get("dedup_days", 90),
    )
    run_id = db.start_run()
    errors: list[str] = []

    counts = {
        "total_articles": 0,
        "new_articles": 0,
        "keyword_passed": 0,
        "ai_confirmed": 0,
        "pushed": 0,
    }

    try:
        crawl_results, retry_count = run_crawlers_with_retries(
            config, source_filter=source_filter
        )
        if source_filter and not crawl_results:
            raise ConfigError(f"未找到启用的渠道: {source_filter}")
        if retry_count and crawl_results and all(
            result.status == "failed" for result in crawl_results
        ):
            errors.append(f"全源失败后重试{retry_count}次仍失败")
        counts["total_articles"] = sum(len(result.articles) for result in crawl_results)
        new_counts: dict[str, int] = defaultdict(int)

        for result in crawl_results:
            for article in result.articles:
                article_id, is_new = db.add_discovered(article)
                if is_new:
                    article.db_id = article_id
                    new_counts[result.source] += 1
                    counts["new_articles"] += 1

        filter_config = config.get("filter", {})
        keyword_filter = KeywordFilter(
            keywords=filter_config.get("keywords", []),
            exclude_keywords=filter_config.get("exclude_keywords", []),
        )
        counts["keyword_passed"], detail_counts = process_discovered_articles(
            db, config, keyword_filter
        )

        for result in crawl_results:
            health_status = result.status
            health_error = result.error
            metrics = result.metrics or {}
            raw_count = metrics.get("raw_result_count")
            matched_count = metrics.get("account_matched_count")
            if (
                result.source_type == "wechat"
                and raw_count is not None
                and matched_count == 0
                and raw_count > 0
            ):
                health_status = "no_match"
                health_error = "搜狗返回结果但公众号身份全部未匹配；已过滤且不计入渠道故障"
            baseline = db.get_source_baseline(result.source)
            if health_status == result.status and result.status == "empty" and baseline > 0:
                health_status = "anomaly"
                health_error = f"本次为0条，最近成功基线约{baseline}条，可能是页面结构变化"
            if health_status in {"failed", "partial", "anomaly"}:
                errors.append(f"{result.source}: {health_error or health_status}")
            detail_success, detail_failed = detail_counts.get(result.source, (0, 0))
            write_source_semantics(
                run_id,
                result,
                getattr(
                    db,
                    "db_path",
                    storage_config.get("db_path", PROJECT_ROOT / "data" / "monitor.db"),
                ),
                status=health_status,
            )
            db.record_source_run(
                run_id=run_id,
                source=result.source,
                source_type=result.source_type,
                status=health_status,
                article_count=len(result.articles),
                new_count=new_counts.get(result.source, 0),
                duration_seconds=result.duration_seconds,
                error=health_error,
                detail_success=detail_success,
                detail_failed=detail_failed,
            )

        ai_config = filter_config.get("ai", {})
        ai_filter = AIFilter(
            provider=ai_config.get("provider", "ollama"),
            api_url=ai_config.get(
                "api_url",
                "https://api.agnes-ai.cn/v1/chat/completions"
                if ai_config.get("provider", "ollama") == "agnes"
                else "http://localhost:11434/api/chat",
            ),
            model=ai_config.get(
                "model",
                "agnes-2.5-flash"
                if ai_config.get("provider", "ollama") == "agnes"
                else "qwen3.5:latest",
            ),
            max_summary_length=ai_config.get("max_summary_length", 500),
            timeout=ai_config.get("timeout", 30),
            enabled=ai_config.get("enabled", True),
            max_workers=ai_config.get("max_workers", 1),
            api_key_file=ai_config.get("api_key_file", ""),
            fallback_provider=ai_config.get("fallback", {}).get("provider", ""),
            fallback_api_url=ai_config.get("fallback", {}).get("api_url", ""),
            fallback_model=ai_config.get("fallback", {}).get("model", ""),
        )
        confirmed, _, ai_pending = process_ai_pending(db, ai_filter)
        counts["ai_confirmed"] = confirmed
        if ai_pending:
            errors.append(f"AI待重试 {ai_pending} 条")

        if notifier is None:
            notification = config.get("notification", {}).get("feishu", {})
            notifier = FeishuNotifier(
                webhook_url=notification.get("webhook_url", ""),
                secret=notification.get("secret", ""),
            )
        counts["pushed"] = process_pending_notifications(
            db, notifier, dry_run=dry_run, config=config
        )

        push_failed_count = len(db.get_articles_by_status(["push_failed"]))
        if push_failed_count:
            errors.append(f"飞书待重试 {push_failed_count} 条")

        if not dry_run and crawl_results:
            failure_threshold = int(
                config.get("health", {}).get("failure_alert_threshold", 3)
            )
            alert_sources = [
                result.source
                for result in crawl_results
                if db.get_consecutive_source_failures(result.source) == failure_threshold
            ]
            all_failed = all(result.status == "failed" for result in crawl_results)
            if all_failed:
                notifier.notify_alert("本次所有信息源抓取失败，请检查日志。")
            elif alert_sources:
                notifier.notify_alert(
                    f"以下渠道已连续失败{failure_threshold}次：" + "、".join(alert_sources)
                )

        elapsed = time.monotonic() - started_at
        status = "completed_with_errors" if errors else "completed"
        db.finish_run(
            run_id,
            status=status,
            errors=errors,
            duration_seconds=elapsed,
            **counts,
        )
        logger.info(
            f"运行结束，耗时{elapsed:.1f}秒 | 总计{counts['total_articles']} → "
            f"新增{counts['new_articles']} → 初筛{counts['keyword_passed']} → "
            f"AI确认{counts['ai_confirmed']} → 推送{counts['pushed']} | 状态={status}"
        )
        return {"run_id": run_id, "status": status, "errors": errors, **counts}
    except Exception as exc:
        elapsed = time.monotonic() - started_at
        errors.append(f"主流程异常 {type(exc).__name__}: {exc}")
        db.finish_run(
            run_id,
            status="failed",
            errors=errors,
            duration_seconds=elapsed,
            **counts,
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="报奖信息监测系统")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="使用数据库副本且禁止真实通知")
    parser.add_argument("--source", help="只运行指定渠道名称或类型")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = resolve_config_paths(load_config(args.config), Path(args.config).resolve().parent)
        validate_config(config, dry_run=args.dry_run)
    except (OSError, yaml.YAMLError, ConfigError) as exc:
        print(f"配置加载失败: {exc}", file=sys.stderr)
        return 2

    log_config = config.get("logging", {})
    setup_logger(
        log_dir=log_config.get("dir", str(PROJECT_ROOT / "logs")),
        level=log_config.get("level", "INFO"),
        max_days=log_config.get("max_days", 30),
    )
    logger = get_logger()

    if args.dry_run:
        config, dry_db = prepare_dry_run_config(config)
        logger.info(f"dry-run 数据库副本: {dry_db}")

    lock_name = ".dry-run.lock" if args.dry_run else ".run.lock"
    lock = ProcessLock(PROJECT_ROOT / "data" / lock_name)
    if not lock.acquire():
        logger.error("上一次运行尚未结束，跳过本次执行")
        return 3

    try:
        logger.info("=" * 50)
        logger.info("开始运行报奖信息监测" + (" [dry-run]" if args.dry_run else ""))
        result = run_pipeline(
            config,
            dry_run=args.dry_run,
            source_filter=args.source,
        )
        logger.info("=" * 50)
        if result["status"] == "completed":
            return 0
        if result["status"] == "completed_with_errors":
            return 4
        return 1
    except ConfigError as exc:
        logger.error(f"参数或配置错误: {exc}")
        return 2
    except Exception:
        logger.exception("报奖信息监测运行失败")
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
