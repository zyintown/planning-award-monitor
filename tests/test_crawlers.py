import unittest
from datetime import date

from crawlers.base import Article, BaseCrawler, enrich_article
from crawlers.sogo_wechat import SogoWechatCrawler, filter_backfill_window


class EmptyRequestCrawler(BaseCrawler):
    def _request_page(self, url=None):
        return None

    def _parse(self, html):
        return []


class EmptyParseCrawler(BaseCrawler):
    def _request_page(self, url=None):
        return "<html><body>正常空页面</body></html>"

    def _parse(self, html):
        return []


class CrawlerTests(unittest.TestCase):
    def test_request_failure_is_not_reported_as_legitimate_empty(self):
        result = EmptyRequestCrawler("失败源", "https://example.com", {}).fetch_result()
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.error)

    def test_empty_parser_result_has_distinct_status(self):
        result = EmptyParseCrawler("空源", "https://example.com", {}).fetch_result()
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.articles, [])

    def test_detail_enrichment_extracts_readable_text(self):
        article = Article("申报通知", "https://example.com/detail", "测试源")
        html = """
        <html><body><nav>导航内容</nav><article>
        <h1>申报通知</h1><p>申报截止时间为2026年8月1日。</p>
        <script>ignore()</script></article></body></html>
        """
        enriched, error = enrich_article(
            article,
            {"crawler": {"detail": {"max_content_length": 5000}}},
            fetcher=lambda **kwargs: html,
        )
        self.assertEqual(error, "")
        self.assertIn("申报截止时间", enriched.raw_content)
        self.assertNotIn("导航内容", enriched.raw_content)
        self.assertNotIn("ignore", enriched.raw_content)

    def test_detail_enrichment_prefers_content_detail_over_list_summary(self):
        article = Article(
            "奖项申报通知",
            "https://example.com/detail",
            "测试源",
            summary="列表页导航和截断摘要",
        )
        html = """
        <html><body><nav>列表页导航</nav>
        <div class="contentDetail">
          <h1>奖项申报通知</h1>
          <p>推荐单位和省级学会可以申报。</p>
        </div></body></html>
        """

        enriched, error = enrich_article(
            article,
            {"crawler": {"detail": {"max_content_length": 5000}}},
            fetcher=lambda **kwargs: html,
        )

        self.assertEqual(error, "")
        self.assertIn("推荐单位和省级学会可以申报", enriched.raw_content)
        self.assertTrue(enriched.summary.startswith("奖项申报通知"))
        self.assertNotIn("列表页导航", enriched.summary)

    def test_wechat_backfill_keeps_recent_and_unknown_dates(self):
        articles = [
            Article("今天", "https://a", "公众号", "2026-07-10"),
            Article("前天", "https://b", "公众号", "2026-07-08"),
            Article("过期", "https://c", "公众号", "2026-07-07"),
            Article("日期未知", "https://d", "公众号", ""),
        ]
        kept = filter_backfill_window(articles, days=3, today=date(2026, 7, 10))
        self.assertEqual([a.title for a in kept], ["今天", "前天", "日期未知"])

    def test_wechat_topic_search_fetches_two_pages_and_accepts_alias(self):
        crawler = SogoWechatCrawler(
            name="微信公众号主题检索",
            keyword="主题词",
            config={"crawler": {}},
            account_aliases=["中国自然资源学会信息平台"],
            search_queries=["主题词"],
            max_pages=2,
        )

        def fake_request(query=None, page=1, **kwargs):
            return f"""
            <div class='txt-box'>
              <h3><a href='https://example.com/{page}'>2026年自然资源科学技术奖申报通知{page}</a></h3>
              <p class='txt-info'>摘要</p>
              <span class='all-time-y2'>中国自然资源学会信息平台</span>
              <span class='s2'>2026-07-30</span>
            </div>
            """

        crawler._request_page = fake_request
        result = crawler.fetch_result()

        self.assertEqual(result.status, "success")
        self.assertEqual(result.pages_fetched, 2)
        self.assertEqual(len(result.articles), 2)
        self.assertTrue(all("中国自然资源学会信息平台" in a.source for a in result.articles))
        self.assertEqual(result.metrics["raw_result_count"], 2)
        self.assertEqual(result.metrics["account_matched_count"], 2)
        self.assertEqual(result.metrics["window_kept_count"], 2)

    def test_wechat_topic_search_rejects_unidentified_account(self):
        crawler = SogoWechatCrawler(
            name="微信公众号主题检索",
            keyword="主题词",
            config={"crawler": {}},
            account_aliases=["目标公众号"],
            require_account_match=True,
        )
        crawler._request_page = lambda query=None, page=1, **kwargs: """
            <div class='txt-box'>
              <h3><a href='https://example.com/matched'>匹配</a></h3>
              <span class='all-time-y2'>目标公众号</span>
            </div>
            <div class='txt-box'>
              <h3><a href='https://example.com/unknown'>未知账号</a></h3>
            </div>
            <div class='txt-box'>
              <h3><a href='https://example.com/wrong'>错误账号</a></h3>
              <span class='all-time-y2'>其他公众号</span>
            </div>
        """

        result = crawler.fetch_result()

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.articles), 1)
        self.assertEqual(result.metrics["raw_result_count"], 3)
        self.assertEqual(result.metrics["account_matched_count"], 1)
        self.assertEqual(result.metrics["window_kept_count"], 1)


if __name__ == "__main__":
    unittest.main()
