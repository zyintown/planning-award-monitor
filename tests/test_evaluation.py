import unittest

from storage.evaluate_labels import calculate_metrics
from storage.export_evaluation_review import build_review_rows
from storage.label_event_time_review import adjudicate, label_rows
from storage.prepare_event_time_evaluation import determine_event_time, prepare_rows


class EvaluationTests(unittest.TestCase):
    def test_priority_queue_contains_all_ai_rows_and_stratified_keyword_sample(self):
        rows = [
            {"id": 1, "title": "正例", "url": "https://e/1", "source": "甲", "publish_date": "", "status": "pushed", "pipeline_version": 1},
            {"id": 2, "title": "AI负例", "url": "https://e/2", "source": "甲", "publish_date": "", "status": "ai_rejected", "pipeline_version": 1},
        ]
        rows.extend(
            {"id": index, "title": f"关键词负例{index}", "url": f"https://e/{index}", "source": "甲" if index < 8 else "乙", "publish_date": "", "status": "new", "pipeline_version": 1}
            for index in range(3, 13)
        )

        full, priority = build_review_rows(rows, keyword_sample_size=4, seed=7)

        self.assertEqual(len(full), 12)
        self.assertEqual(len(priority), 6)
        self.assertTrue(all(float(row["sampling_weight"]) == 1 for row in full))
        self.assertEqual(sum(row["decision_stage"] == "keyword_negative" for row in priority), 4)
        self.assertEqual({row["source"] for row in priority if row["decision_stage"] == "keyword_negative"}, {"甲", "乙"})
        self.assertTrue(all(float(row["sampling_weight"]) >= 1 for row in priority))

    def test_weighted_metrics_detect_false_positive_and_false_negative(self):
        rows = [
            {"id": "1", "predicted_should_push": "是", "人工审核结论": "是", "sampling_weight": "1", "sampling_stratum": "ai_positive", "stratum_population": "2", "stratum_sample_size": "2"},
            {"id": "2", "predicted_should_push": "是", "人工审核结论": "否", "sampling_weight": "1", "sampling_stratum": "ai_positive", "stratum_population": "2", "stratum_sample_size": "2"},
            {"id": "3", "predicted_should_push": "否", "人工审核结论": "是", "sampling_weight": "4", "sampling_stratum": "keyword_negative|甲", "stratum_population": "8", "stratum_sample_size": "2"},
            {"id": "4", "predicted_should_push": "否", "人工审核结论": "否", "sampling_weight": "4", "sampling_stratum": "keyword_negative|甲", "stratum_population": "8", "stratum_sample_size": "2"},
        ]

        result = calculate_metrics(rows, iterations=20)

        self.assertEqual(result["confusion"], {"tp": 1.0, "fp": 1.0, "tn": 4.0, "fn": 4.0})
        self.assertAlmostEqual(result["metrics"]["precision"], 0.5)
        self.assertAlmostEqual(result["metrics"]["recall"], 0.2)
        self.assertEqual(len(result["false_positives"]), 1)
        self.assertEqual(len(result["false_negatives"]), 1)

    def test_event_time_review_uses_publication_then_first_seen_and_preserves_snapshot(self):
        rows = [
            {"id": "1", "publish_date": "2026/7/1", "url": "https://e/1", "人工审核结论": "否", "审核备注": "快照日已过期"},
            {"id": "2", "publish_date": "", "url": "https://e/2", "人工审核结论": "不确定", "审核备注": ""},
        ]

        prepared = prepare_rows(rows, {"2": "2026-07-03 09:00:00"})

        self.assertEqual(prepared[0]["event_date"], "2026-07-01")
        self.assertEqual(prepared[0]["event_time_basis"], "published_at")
        self.assertEqual(prepared[0]["快照口径审核结论"], "否")
        self.assertEqual(prepared[0]["事件时点评估结论"], "")
        self.assertEqual(prepared[1]["event_date"], "2026-07-03")
        self.assertEqual(prepared[1]["event_time_basis"], "first_seen_at")
        self.assertEqual(determine_event_time("", "无日期"), ("", "unknown"))

    def test_event_time_metrics_exclude_unknown_time_and_count_uncertain(self):
        rows = [
            {"id": "1", "predicted_should_push": "是", "sampling_weight": "1", "sampling_stratum": "ai_positive", "stratum_population": "1", "stratum_sample_size": "1", "event_time_basis": "published_at", "事件时点评估结论": "否"},
            {"id": "2", "predicted_should_push": "否", "sampling_weight": "4", "sampling_stratum": "keyword_negative|甲", "stratum_population": "8", "stratum_sample_size": "2", "event_time_basis": "first_seen_at", "事件时点评估结论": "不确定"},
            {"id": "3", "predicted_should_push": "否", "sampling_weight": "4", "sampling_stratum": "keyword_negative|甲", "stratum_population": "8", "stratum_sample_size": "2", "event_time_basis": "unknown", "事件时点评估结论": "是"},
        ]

        result = calculate_metrics(rows, iterations=0)

        self.assertTrue(result["event_time_mode"])
        self.assertEqual(result["confusion"], {"tp": 0.0, "fp": 1.0, "tn": 0.0, "fn": 0.0})
        self.assertEqual(result["uncertain_rows"], 1)
        self.assertEqual(result["uncertain_weight"], 4.0)
        self.assertEqual(result["time_unassessable_rows"], 1)
        self.assertEqual(result["time_unassessable_weight"], 4.0)

    def test_title_review_excludes_results_and_keeps_ambiguous_wechat_uncertain(self):
        result = adjudicate({"id": "1036", "source": "微信公众号:E20水网固废网", "title": "24个环保技术获奖!住建部公布华夏建设科学技术奖项目名单"})
        self.assertEqual(result["内容类型"], "名单公示")
        self.assertEqual(result["事件时点评估结论"], "否")

        rows = label_rows([
            {"id": "181", "source": "微信公众号:华夏建设科学技术奖", "title": "华夏建设科学技术奖+1", "事件时点评估结论": ""},
            {"id": "436", "source": "广东省自然资源厅", "title": "开展第一届全国优秀国土空间规划奖评选工作的通知", "事件时点评估结论": ""},
        ], "test", "2026-07-10")
        self.assertEqual(rows[0]["事件时点评估结论"], "不确定")
        self.assertEqual(rows[1]["事件时点评估结论"], "是")

        stale = adjudicate({"id": "619", "source": "中国城市规划学会", "title": "关于开展2026年度中国城市规划学会科技进步奖推荐工作的通知"})
        self.assertEqual(stale["事件时点可行动"], "否")
        self.assertEqual(stale["事件时点评估结论"], "否")


if __name__ == "__main__":
    unittest.main()
