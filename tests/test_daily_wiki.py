from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_daily_wiki import build_daily_wiki, load_source_registry, score_item  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_source_registry_includes_cls_and_reuters_extension_slot(tmp_path: Path) -> None:
    registry_path = tmp_path / "manifests" / "source_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "sources": {
                    "cls": {"name": "财联社", "enabled": True, "data_glob": "sources/cls/data/{year}/{date}.jsonl"},
                    "reuters": {"name": "Reuters", "enabled": False, "data_glob": "sources/reuters/data/{year}/{date}.jsonl"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    registry = load_source_registry(tmp_path)

    assert registry["cls"]["name"] == "财联社"
    assert registry["reuters"]["enabled"] is False
    assert "{date}" in registry["reuters"]["data_glob"]


def test_score_item_prioritizes_policy_price_and_stock_rich_news() -> None:
    important = {
        "title": "国家发改委发布新政策，半导体材料价格上调，多家公司签订重大订单",
        "content": "涉及涨价、产业政策、订单落地和业绩弹性。",
        "level": "B",
        "subjects": ["半导体", "材料"],
        "stocks": [{"name": "公司A", "code": "000001"}, {"name": "公司B", "code": "000002"}],
    }
    ordinary = {"title": "公司召开投资者交流会", "content": "常规活动。", "level": "", "subjects": [], "stocks": []}

    high = score_item(important)
    low = score_item(ordinary)

    assert high["score"] > low["score"]
    assert any("政策" in reason for reason in high["reasons"])
    assert any("涨价" in reason for reason in high["reasons"])
    assert any("关联股票" in reason for reason in high["reasons"])


def test_build_daily_wiki_writes_insight_and_topic_pages(tmp_path: Path) -> None:
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "source_registry.json").write_text(
        json.dumps(
            {
                "sources": {
                    "cls": {"name": "财联社", "enabled": True, "data_glob": "sources/cls/data/{year}/{date}.jsonl"},
                    "reuters": {"name": "Reuters", "enabled": False, "data_glob": "sources/reuters/data/{year}/{date}.jsonl"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = [
        {
            "id": "1",
            "date": "2026-05-14",
            "time": "09:30:00",
            "title": "半导体材料涨价 相关公司订单增长",
            "content": "上游材料价格持续上行。",
            "subjects": ["半导体材料"],
            "stocks": [{"name": "材料A", "code": "000001"}],
        },
        {
            "id": "2",
            "date": "2026-05-14",
            "time": "10:00:00",
            "title": "普通公告",
            "content": "一般新闻。",
            "subjects": ["公告"],
            "stocks": [],
        },
    ]
    write_jsonl(tmp_path / "sources" / "cls" / "data" / "2026" / "2026-05-14.jsonl", rows)

    outputs = build_daily_wiki(tmp_path, "2026-05-14", top_n=1)

    insight = json.loads((tmp_path / "insights" / "2026" / "2026-05-14.json").read_text(encoding="utf-8"))
    daily_md = (tmp_path / "wiki" / "daily" / "2026-05-14.md").read_text(encoding="utf-8")
    topic_md = (tmp_path / "wiki" / "topics" / "半导体材料.md").read_text(encoding="utf-8")
    index_md = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")

    assert outputs
    assert insight["top_items"][0]["title"] == "半导体材料涨价 相关公司订单增长"
    assert "## 今日最具信息量消息" in daily_md
    assert "信息量评分" in daily_md
    assert "[[topics/半导体材料|半导体材料]]" in daily_md
    assert "半导体材料涨价" in topic_md
    assert "[[daily/2026-05-14|2026-05-14]]" in index_md
