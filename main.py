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
            notifier.notify_alert(
                "⚠️ 本次抓取全部失败，请检查程序和网站状态。\n错误: " + "; ".join(errors)
            )
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
