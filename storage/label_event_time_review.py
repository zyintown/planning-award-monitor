"""按已确认的事件时点规则完成 P2 队列的标题初审。

这是审核辅助脚本，不访问网络；只覆盖仍为空的事件时点评估列，防止覆盖人工复核。
"""

import argparse
import csv
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LABEL_FIELD = "事件时点评估结论"
REVIEW_FIELDS = ("内容类型", "行业相关", "事件时点可行动", LABEL_FIELD, "证据说明", "复核人", "复核时间")

# 标题、来源和事件日期已足以确认的正例。仅限规划/建设相关且存在申报、推荐或报名动作的奖项/竞赛。
POSITIVE_IDS = {
    "436", "675", "693", "694", "703", "898", "912", "921", "922", "923",
    "960", "980", "982", "993",
}

# 这些条目仅靠标题不能可靠判断正文性质或事件时点，不以猜测替代证据。
UNCERTAIN_IDS = {"181"}

# 标题年份与事件时点明显冲突，或已在上一轮通过原文确认截止时间早于首次发现时点。
STALE_APPLICATION_IDS = {"50", "619", "655", "735", "740"}

RESULT_KEYWORDS = ("获奖", "授奖", "名单", "公示", "评审结果", "评选结果", "喜报", "荣获", "拟奖", "公布")
COMPETITION_KEYWORDS = ("竞赛", "大赛", "比赛", "参赛")
APPLICATION_KEYWORDS = ("申报", "提名", "推荐", "参评", "评选", "报名")
TARGET_SOURCE_PARTS = ("城市规划", "风景园林", "自然资源", "国土空间", "华夏建设")
TARGET_TITLE_KEYWORDS = ("规划", "国土", "自然资源", "园林", "建设", "住建", "建筑", "城市", "设计", "测绘", "生态")


def _is_industry_related(row: dict) -> bool:
    source = row.get("source", "")
    title = row.get("title", "")
    return any(token in source for token in TARGET_SOURCE_PARTS) or any(
        token in title for token in TARGET_TITLE_KEYWORDS
    )


def _content_type(title: str, identifier: str) -> str:
    if any(token in title for token in RESULT_KEYWORDS):
        return "名单公示" if "名单" in title or "公示" in title else "获奖结果"
    if identifier in POSITIVE_IDS:
        return "竞赛报名" if any(token in title for token in COMPETITION_KEYWORDS) else "申报通知"
    if "培训" in title or "课程" in title or "会议" in title or "论坛" in title:
        return "培训会议"
    if any(token in title for token in COMPETITION_KEYWORDS):
        return "其他"
    if any(token in title for token in APPLICATION_KEYWORDS):
        return "申报通知"
    return "其他"


def adjudicate(row: dict) -> dict:
    """返回一条可审计的标题初审，不依赖既有预测值或快照标签。"""
    identifier = str(row["id"])
    title = row.get("title", "")
    related = _is_industry_related(row)
    content_type = _content_type(title, identifier)
    if identifier in UNCERTAIN_IDS:
        return {
            "内容类型": "其他",
            "行业相关": "不确定",
            "事件时点可行动": "不确定",
            LABEL_FIELD: "不确定",
            "证据说明": "标题为“华夏建设科学技术奖+1”，未说明申报、获奖或公示性质；搜狗跳转链接无法仅凭标题确认。",
        }
    if identifier in POSITIVE_IDS:
        return {
            "内容类型": content_type,
            "行业相关": "是",
            "事件时点可行动": "是",
            LABEL_FIELD: "是",
            "证据说明": "标题明确为规划/建设相关奖项或竞赛的申报、推荐或报名通知；以文章发布日作为事件时点。",
        }
    if identifier in STALE_APPLICATION_IDS:
        return {
            "内容类型": "申报通知",
            "行业相关": "是",
            "事件时点可行动": "否",
            LABEL_FIELD: "否",
            "证据说明": "标题所示奖项年度早于事件时点；其中 2026 年学会科技进步奖已在此前原文核验中确认截止早于首次发现日，因此不应推送。",
        }
    if any(token in title for token in RESULT_KEYWORDS):
        return {
            "内容类型": content_type,
            "行业相关": "是" if related else "否",
            "事件时点可行动": "否",
            LABEL_FIELD: "否",
            "证据说明": "标题明确属于获奖结果、名单、评审结果或公示；该类信息不属于应推送的申报机会。",
        }
    return {
        "内容类型": content_type,
        "行业相关": "是" if related else "否",
        "事件时点可行动": "否",
        LABEL_FIELD: "否",
        "证据说明": "标题未体现规划/建设行业奖项的申报、参评或报名机会。",
    }


def label_rows(rows: list[dict], reviewer: str, reviewed_at: str, force: bool = False) -> list[dict]:
    labeled = []
    for source_row in rows:
        row = dict(source_row)
        if row.get(LABEL_FIELD, "").strip() and not force:
            labeled.append(row)
            continue
        row.update(adjudicate(row))
        row["复核人"] = reviewer
        row["复核时间"] = reviewed_at
        labeled.append(row)
    return labeled


def main() -> int:
    parser = argparse.ArgumentParser(description="按标题完成 P2 事件时点评估初审")
    parser.add_argument("input", help="P2 事件时点评估优先队列 CSV")
    parser.add_argument("--output", help="默认原地更新输入文件")
    parser.add_argument("--reviewer", default="Codex（标题初审）")
    parser.add_argument("--reviewed-at", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true", help="覆盖已有事件时点结论")
    args = parser.parse_args()

    input_path = Path(args.input)
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = reader.fieldnames or []
    missing = [field for field in REVIEW_FIELDS if field not in headers]
    if missing:
        raise ValueError(f"输入文件缺少事件时点评估列: {', '.join(missing)}")

    output_path = Path(args.output) if args.output else input_path
    labeled = label_rows(rows, args.reviewer, args.reviewed_at, force=args.force)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="raise")
        writer.writeheader()
        writer.writerows(labeled)
    counts = {label: sum(row[LABEL_FIELD] == label for row in labeled) for label in ("是", "否", "不确定")}
    print(f"标题初审完成：是 {counts['是']}，否 {counts['否']}，不确定 {counts['不确定']} -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
