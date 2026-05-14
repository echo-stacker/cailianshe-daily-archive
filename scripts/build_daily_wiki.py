#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_REGISTRY: dict[str, Any] = {
    "schema_version": "1.0",
    "sources": {
        "cls": {
            "name": "财联社",
            "enabled": True,
            "data_glob": "sources/cls/data/{year}/{date}.jsonl",
            "license_note": "Public web roll feed; normalized archive for research use.",
        },
        "reuters": {
            "name": "Reuters",
            "enabled": False,
            "data_glob": "sources/reuters/data/{year}/{date}.jsonl",
            "license_note": "Extension slot only. Store metadata/links/short excerpts unless a licensed feed is configured.",
        },
    },
}

KEYWORD_WEIGHTS: list[tuple[str, int, str]] = [
    ("政策|发改委|国常会|国务院|证监会|央行|财政部|工信部|监管|规则|试点", 18, "政策/监管变量"),
    ("涨价|提价|价格上调|供给中断|减产|停产|库存下降|短缺|涨幅", 18, "涨价/供需变化"),
    ("订单|中标|合同|签订|采购|交付|量产|投产|扩产", 14, "订单/产能兑现"),
    ("业绩|净利润|营收|预增|增长|扭亏|分红|回购", 12, "业绩或股东回报"),
    ("并购|重组|收购|股权转让|控股权|资产注入", 14, "并购重组"),
    ("出口|关税|制裁|禁令|海外|美国|欧盟|日本|韩国|路透", 10, "外部约束/海外变量"),
    ("AI|算力|芯片|半导体|机器人|低空经济|固态电池|核电|电力|军工", 8, "高关注产业主题"),
]


def clean_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").replace("\u3000", " ").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def safe_slug(value: str) -> str:
    slug = re.sub(r"[\\/:*?\"<>|#\[\]]+", "-", clean_text(value, 80)).strip(" .-")
    return slug or "未分类"


def wiki_link(path: str, label: str | None = None) -> str:
    label = label or path
    return f"[[{path}|{label}]]"


def load_source_registry(repo: Path) -> dict[str, dict[str, Any]]:
    registry_path = repo / "manifests" / "source_registry.json"
    if not registry_path.exists():
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(DEFAULT_REGISTRY, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    return dict(payload.get("sources") or {})


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_daily_items(repo: Path, target_date: str, registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    year = target_date[:4]
    items: list[dict[str, Any]] = []
    for source_id, source in registry.items():
        if source.get("enabled") is False:
            continue
        pattern = source.get("data_glob") or "sources/{source_id}/data/{year}/{date}.jsonl"
        rel = pattern.format(source_id=source_id, year=year, date=target_date)
        for row in iter_jsonl(repo / rel):
            normalized = dict(row)
            normalized.setdefault("source", source_id)
            normalized.setdefault("source_name", source.get("name") or source_id)
            normalized.setdefault("date", target_date)
            items.append(normalized)
    return items


def score_item(item: dict[str, Any]) -> dict[str, Any]:
    title = clean_text(item.get("title"), 300)
    content = clean_text(item.get("content") or item.get("brief"), 1200)
    text = f"{title} {content}"
    score = 10
    reasons: list[str] = []

    level = str(item.get("level") or "").upper()
    if level in {"A", "B", "重要"}:
        score += 12
        reasons.append(f"快讯等级较高：{level}")

    for pattern, weight, reason in KEYWORD_WEIGHTS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            score += weight
            reasons.append(reason)

    subjects = [clean_text(v, 60) for v in item.get("subjects") or [] if clean_text(v, 60)]
    if subjects:
        score += min(12, len(subjects) * 4)
        reasons.append("主题标签明确：" + "、".join(subjects[:3]))

    stocks = [s for s in item.get("stocks") or [] if isinstance(s, dict) and (s.get("name") or s.get("code"))]
    if stocks:
        score += min(16, 6 + len(stocks) * 3)
        reasons.append(f"关联股票 {len(stocks)} 只")

    if len(content) >= 120:
        score += 5
        reasons.append("正文信息量较高")

    # Penalize very generic headlines that have neither entities nor topics.
    if not subjects and not stocks and len(title) < 16:
        score -= 5

    deduped_reasons = []
    seen = set()
    for reason in reasons:
        if reason not in seen:
            deduped_reasons.append(reason)
            seen.add(reason)

    return {"score": max(score, 0), "reasons": deduped_reasons[:8]}


def enrich_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    seen_titles: Counter[str] = Counter(clean_text(item.get("title"), 120) for item in items)
    for item in items:
        scored = score_item(item)
        title = clean_text(item.get("title"), 120)
        if seen_titles[title] > 1:
            scored["score"] = max(0, scored["score"] - 6)
            scored["reasons"] = [*scored["reasons"], "同标题重复，已降权"]
        row = dict(item)
        row["information_score"] = scored["score"]
        row["information_reasons"] = scored["reasons"]
        enriched.append(row)
    return sorted(enriched, key=lambda row: (row.get("information_score", 0), row.get("time", "")), reverse=True)


def item_ref(item: dict[str, Any]) -> str:
    source = item.get("source") or "unknown"
    ident = item.get("id") or clean_text(item.get("title"), 80)
    return f"{source}:{ident}"


def compact_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": item_ref(item),
        "source": item.get("source"),
        "source_name": item.get("source_name"),
        "id": item.get("id"),
        "date": item.get("date"),
        "time": item.get("time"),
        "title": clean_text(item.get("title"), 300),
        "content": clean_text(item.get("content") or item.get("brief"), 1000),
        "subjects": item.get("subjects") or [],
        "stocks": item.get("stocks") or [],
        "url": item.get("shareurl") or item.get("url"),
        "information_score": item.get("information_score", 0),
        "information_reasons": item.get("information_reasons", []),
    }


def render_daily_page(target_date: str, items: list[dict[str, Any]], top_items: list[dict[str, Any]]) -> str:
    subject_counts = Counter(subject for item in items for subject in (item.get("subjects") or []))
    source_counts = Counter(item.get("source_name") or item.get("source") or "unknown" for item in items)
    lines = [
        f"# {target_date} 市场信息 Wiki 日报",
        "",
        f"- total_items: {len(items)}",
        "- sources: " + "，".join(f"{name} {count}" for name, count in source_counts.most_common()) if source_counts else "- sources: -",
        f"- generated_at: {datetime.now(SH_TZ).isoformat(timespec='seconds')}",
        "",
        "## 今日最具信息量消息",
        "",
    ]
    if not top_items:
        lines.append("暂无数据。")
    for idx, item in enumerate(top_items, 1):
        title = clean_text(item.get("title"), 260) or "无标题"
        reasons = "；".join(item.get("information_reasons") or []) or "基础信息匹配"
        url = item.get("shareurl") or item.get("url") or ""
        subjects = [wiki_link(f"topics/{safe_slug(s)}", s) for s in item.get("subjects") or []]
        stocks = "，".join(f"{s.get('name')}({s.get('code')})" for s in item.get("stocks") or [] if isinstance(s, dict))
        lines.extend(
            [
                f"### {idx}. {item.get('time') or '--:--:--'}｜{title}",
                "",
                f"- 来源: {item.get('source_name') or item.get('source')} / `{item_ref(item)}`",
                f"- 信息量评分: {item.get('information_score')}",
                f"- 入选理由: {reasons}",
            ]
        )
        if subjects:
            lines.append("- 主题: " + "，".join(subjects))
        if stocks:
            lines.append("- 股票: " + stocks)
        if url:
            lines.append(f"- url: {url}")
        lines.extend(["", clean_text(item.get("content") or item.get("brief"), 600), ""])

    lines.extend(["## 主题热度", ""])
    if subject_counts:
        for subject, count in subject_counts.most_common(30):
            lines.append(f"- {wiki_link(f'topics/{safe_slug(subject)}', subject)}：{count}")
    else:
        lines.append("- 暂无主题标签。")
    return "\n".join(lines).rstrip() + "\n"


def render_topic_pages(target_date: str, items: list[dict[str, Any]]) -> dict[str, str]:
    by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        for subject in item.get("subjects") or []:
            by_subject[clean_text(subject, 80)].append(item)
    pages: dict[str, str] = {}
    for subject, rows in by_subject.items():
        rows = sorted(rows, key=lambda row: (row.get("information_score", 0), row.get("time", "")), reverse=True)[:50]
        lines = [f"# {subject}", "", "## 相关日期", "", f"- {wiki_link(f'daily/{target_date}', target_date)}", "", "## 高信息量消息", ""]
        for item in rows:
            lines.extend(
                [
                    f"- **{item.get('time') or ''}｜{clean_text(item.get('title'), 180)}**",
                    f"  - score: {item.get('information_score')}；source: {item.get('source_name') or item.get('source')}；ref: `{item_ref(item)}`",
                ]
            )
        pages[subject] = "\n".join(lines).rstrip() + "\n"
    return pages


def render_index(repo: Path) -> str:
    daily_dir = repo / "wiki" / "daily"
    pages = sorted(daily_dir.glob("*.md"), reverse=True) if daily_dir.exists() else []
    lines = ["# Multi-Source Market Intelligence Wiki", "", "## Daily pages", ""]
    for page in pages:
        date = page.stem
        lines.append(f"- {wiki_link(f'daily/{date}', date)}")
    lines.extend(["", "## Source extensibility", "", "新增 Reuters、交易所公告、公司公告等源时，只需：", "", "1. 在 `manifests/source_registry.json` 增加 source 配置；", "2. 将标准化 JSONL 写入该 source 的 `data_glob` 路径；", "3. 运行 `scripts/build_daily_wiki.py --date YYYY-MM-DD`。", ""])
    return "\n".join(lines)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_daily_wiki(repo: Path, target_date: str, *, top_n: int = 20) -> list[Path]:
    registry = load_source_registry(repo)
    items = enrich_items(load_daily_items(repo, target_date, registry))
    top_items = items[:top_n]
    outputs: list[Path] = []

    insight_path = repo / "insights" / target_date[:4] / f"{target_date}.json"
    write_json(
        insight_path,
        {
            "schema_version": "1.0",
            "date": target_date,
            "generated_at": datetime.now(SH_TZ).isoformat(timespec="seconds"),
            "item_count": len(items),
            "top_items": [compact_item(item) for item in top_items],
        },
    )
    outputs.append(insight_path)

    daily_path = repo / "wiki" / "daily" / f"{target_date}.md"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path.write_text(render_daily_page(target_date, items, top_items), encoding="utf-8")
    outputs.append(daily_path)

    topic_dir = repo / "wiki" / "topics"
    for subject, content in render_topic_pages(target_date, items).items():
        path = topic_dir / f"{safe_slug(subject)}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        outputs.append(path)

    index_path = repo / "wiki" / "index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(render_index(repo), encoding="utf-8")
    outputs.append(index_path)
    return outputs


def run_git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def commit_and_push(repo: Path, target_date: str, files: list[Path]) -> tuple[bool, str]:
    run_git(repo, ["add", *[path.relative_to(repo).as_posix() for path in files]])
    status = run_git(repo, ["status", "--short"])
    if not status:
        return False, "no changes"
    run_git(repo, ["commit", "-m", f"wiki: build daily intelligence {target_date}"])
    run_git(repo, ["push", "origin", "main"])
    return True, run_git(repo, ["rev-parse", "--short", "HEAD"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build extensible multi-source daily intelligence wiki pages.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--date", default=datetime.now(SH_TZ).strftime("%Y-%m-%d"))
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    outputs = build_daily_wiki(repo, args.date, top_n=args.top_n)
    if args.no_push:
        print(f"Wiki构建 {args.date}: 写入 {len(outputs)} 个文件，未推送。")
        return 0
    changed, detail = commit_and_push(repo, args.date, outputs)
    if changed:
        print(f"Wiki构建 {args.date}: 写入 {len(outputs)} 个文件，已推送 GitHub commit {detail}。")
    else:
        print(f"Wiki构建 {args.date}: 无新增变更。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
