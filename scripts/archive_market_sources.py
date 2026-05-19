#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}

SOURCE_CONFIGS: dict[str, dict[str, str]] = {
    "eastmoney": {
        "name": "东方财富",
        "source_type": "news_flash",
        "license_note": "Public Eastmoney web fast-news feed; normalized archive for local research use.",
    },
    "ths": {
        "name": "同花顺",
        "source_type": "news_flash",
        "license_note": "Public 10jqka web news feed; normalized archive for local research use.",
    },
}


def clean_text(value: Any, limit: int = 10_000) -> str:
    text = " ".join(str(value or "").replace("\u3000", " ").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def request_json(url: str, *, referer: str) -> dict[str, Any]:
    headers = dict(HEADERS)
    headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=SH_TZ)
        except ValueError:
            pass
    return None


def normalize_em_stock(raw: Any) -> dict[str, str] | None:
    if not raw:
        return None
    text = clean_text(raw, 80)
    parts = text.split(".", 1)
    if len(parts) == 2:
        market, code = parts
        suffix = ""
        if market == "1":
            suffix = ".SH"
        elif market == "0":
            suffix = ".SZ"
        elif market == "105":
            suffix = ".US"
        return {"name": "", "code": f"{code}{suffix}"}
    return {"name": "", "code": text}


def normalize_ths_stock(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    code = clean_text(raw.get("code") or raw.get("stockcode") or raw.get("symbol"), 32)
    name = clean_text(raw.get("name") or raw.get("stockname") or raw.get("shortname"), 80)
    if not code and not name:
        return None
    return {"name": name, "code": code}


def fetch_eastmoney_day(target_date: str, *, page_size: int = 100, max_pages: int = 80, pause: float = 0.2) -> list[dict[str, Any]]:
    day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=SH_TZ)
    start = day
    end = day + timedelta(days=1)
    sort_end = ""
    by_id: dict[str, dict[str, Any]] = {}

    for _ in range(max_pages):
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": sort_end,
            "pageSize": str(page_size),
            "req_trace": str(int(time.time() * 1000)),
        }
        url = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList?" + urllib.parse.urlencode(params)
        payload = request_json(url, referer="https://kuaixun.eastmoney.com/")
        if str(payload.get("code")) not in {"1", "0"}:
            raise RuntimeError(f"Eastmoney API code={payload.get('code')} message={payload.get('message')}")
        data = payload.get("data") or {}
        rows = data.get("fastNewsList") or []
        if not rows:
            break
        min_dt: datetime | None = None
        for row in rows:
            dt = parse_dt(clean_text(row.get("showTime"), 32))
            if dt is None:
                continue
            min_dt = dt if min_dt is None or dt < min_dt else min_dt
            if start <= dt < end:
                stocks = [s for s in (normalize_em_stock(v) for v in row.get("stockList") or []) if s]
                item = {
                    "source": "eastmoney",
                    "source_name": "东方财富",
                    "id": clean_text(row.get("code") or row.get("realSort"), 80),
                    "ctime": int(dt.timestamp()),
                    "datetime": dt.isoformat(),
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": dt.strftime("%H:%M:%S"),
                    "level": "",
                    "title": clean_text(row.get("title"), 300),
                    "content": clean_text(row.get("summary"), 5000),
                    "brief": clean_text(row.get("summary"), 1000),
                    "category": "快讯",
                    "subjects": [],
                    "stocks": stocks,
                    "url": "",
                    "shareurl": "",
                    "realSort": clean_text(row.get("realSort"), 80),
                    "raw": row,
                }
                if item["id"]:
                    by_id[item["id"]] = item
        next_sort_end = clean_text(data.get("sortEnd"), 80)
        if min_dt is not None and min_dt < start:
            break
        if not next_sort_end or next_sort_end == sort_end:
            break
        sort_end = next_sort_end
        time.sleep(pause)
    return sorted(by_id.values(), key=lambda item: (int(item.get("ctime") or 0), item.get("id", "")))


def fetch_ths_day(target_date: str, *, page_size: int = 100, max_pages: int = 80, pause: float = 0.2) -> list[dict[str, Any]]:
    day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=SH_TZ)
    start_ts = int(day.timestamp())
    end_ts = int((day + timedelta(days=1)).timestamp())
    by_id: dict[str, dict[str, Any]] = {}

    for page in range(1, max_pages + 1):
        params = {"page": str(page), "tag": "", "track": "website", "pagesize": str(page_size)}
        url = "https://news.10jqka.com.cn/tapp/news/push/stock/?" + urllib.parse.urlencode(params)
        payload = request_json(url, referer="https://news.10jqka.com.cn/realtimenews.html")
        if str(payload.get("code")) != "200":
            raise RuntimeError(f"THS API code={payload.get('code')} msg={payload.get('msg')}")
        rows = ((payload.get("data") or {}).get("list")) or []
        if not rows:
            break
        min_ctime: int | None = None
        for row in rows:
            try:
                ctime = int(row.get("ctime") or row.get("rtime") or 0)
            except (TypeError, ValueError):
                continue
            if ctime <= 0:
                continue
            min_ctime = ctime if min_ctime is None or ctime < min_ctime else min_ctime
            if start_ts <= ctime < end_ts:
                dt = datetime.fromtimestamp(ctime, tz=SH_TZ)
                stocks = [s for s in (normalize_ths_stock(v) for v in row.get("stock") or []) if s]
                subjects = []
                for tag in (row.get("tags") or []) + (row.get("tagInfo") or []) + (row.get("field") or []):
                    if isinstance(tag, dict):
                        name = clean_text(tag.get("name"), 80)
                        if name and name not in subjects:
                            subjects.append(name)
                    elif tag:
                        name = clean_text(tag, 80)
                        if name and name not in subjects:
                            subjects.append(name)
                item = {
                    "source": "ths",
                    "source_name": "同花顺",
                    "id": clean_text(row.get("seq") or row.get("id"), 80),
                    "ctime": ctime,
                    "datetime": dt.isoformat(),
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": dt.strftime("%H:%M:%S"),
                    "level": "重要" if str(row.get("import") or "0") == "1" else "",
                    "title": clean_text(row.get("title"), 300),
                    "content": clean_text(row.get("digest") or row.get("short"), 5000),
                    "brief": clean_text(row.get("short") or row.get("digest"), 1000),
                    "category": clean_text(row.get("tag"), 80) or "快讯",
                    "subjects": subjects,
                    "stocks": stocks,
                    "url": clean_text(row.get("url") or row.get("shareUrl") or row.get("appUrl"), 500),
                    "shareurl": clean_text(row.get("shareUrl") or row.get("url"), 500),
                    "raw": row,
                }
                if item["id"]:
                    by_id[item["id"]] = item
        if min_ctime is not None and min_ctime < start_ts:
            break
        time.sleep(pause)
    return sorted(by_id.values(), key=lambda item: (int(item.get("ctime") or 0), item.get("id", "")))


def write_source_outputs(repo: Path, target_date: str, source_id: str, source_name: str, items: list[dict[str, Any]]) -> list[Path]:
    year = target_date[:4]
    data_dir = repo / "sources" / source_id / "data" / year
    data_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = data_dir / f"{target_date}.jsonl"
    md_path = data_dir / f"{target_date}.md"

    jsonl_path.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items), encoding="utf-8")
    lines = [f"# {source_name}资讯归档 {target_date}", "", f"- item_count: {len(items)}", f"- source: {source_name} public web feed", ""]
    for item in items:
        title = item.get("title") or "无标题"
        content = item.get("content") or item.get("brief") or ""
        stocks = item.get("stocks") or []
        stock_text = "，".join(f"{s.get('name') or ''}({s.get('code') or ''})" for s in stocks if s.get("name") or s.get("code"))
        lines.extend([f"## {item.get('time')}｜{title}", "", f"- id: `{item.get('id')}`"])
        if item.get("category"):
            lines.append(f"- category: {item.get('category')}")
        if stock_text:
            lines.append(f"- stocks: {stock_text}")
        if item.get("subjects"):
            lines.append("- subjects: " + "，".join(item.get("subjects") or []))
        if item.get("url") or item.get("shareurl"):
            lines.append(f"- url: {item.get('url') or item.get('shareurl')}")
        lines.extend(["", str(content), ""])
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return [jsonl_path, md_path]


def update_registry(repo: Path) -> Path:
    path = repo / "manifests" / "source_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"schema_version": "1.0", "sources": {}}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"schema_version": "1.0", "sources": {}}
    sources = payload.setdefault("sources", {})
    for source_id, cfg in SOURCE_CONFIGS.items():
        entry = sources.setdefault(source_id, {})
        entry.update(
            {
                "name": cfg["name"],
                "enabled": True,
                "data_glob": f"sources/{source_id}/data/{{year}}/{{date}}.jsonl",
                "markdown_glob": f"sources/{source_id}/data/{{year}}/{{date}}.md",
                "source_type": cfg["source_type"],
                "license_note": cfg["license_note"],
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def update_daily_index(repo: Path, target_date: str, source_results: dict[str, tuple[int, Path, Path]]) -> Path:
    path = repo / "manifests" / "daily_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"dates": {}}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"dates": {}}
    date_entry = payload.setdefault("dates", {}).setdefault(target_date, {"sources": {}})
    sources = date_entry.setdefault("sources", {})
    for source_id, (count, jsonl_path, md_path) in source_results.items():
        sources[source_id] = {
            "item_count": count,
            "jsonl": jsonl_path.relative_to(repo).as_posix(),
            "markdown": md_path.relative_to(repo).as_posix(),
        }
    date_entry["item_count"] = sum(int(source.get("item_count") or 0) for source in sources.values())
    date_entry["updated_at"] = datetime.now(SH_TZ).isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def commit_and_push(repo: Path, target_date: str, files: list[Path]) -> tuple[bool, str]:
    run_git(repo, ["add", *[path.relative_to(repo).as_posix() for path in files]])
    status = run_git(repo, ["status", "--short"])
    if not status:
        return False, "no changes"
    run_git(repo, ["commit", "-m", f"data(market-sources): archive {target_date}"])
    run_git(repo, ["push", "origin", "main"])
    return True, run_git(repo, ["rev-parse", "--short", "HEAD"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive one day of public 东方财富/同花顺资讯 data and push to GitHub.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--date", default=datetime.now(SH_TZ).strftime("%Y-%m-%d"))
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    files: list[Path] = [update_registry(repo)]
    source_results: dict[str, tuple[int, Path, Path]] = {}

    eastmoney_items = fetch_eastmoney_day(args.date, page_size=args.page_size, max_pages=args.max_pages)
    em_files = write_source_outputs(repo, args.date, "eastmoney", "东方财富", eastmoney_items)
    files.extend(em_files)
    source_results["eastmoney"] = (len(eastmoney_items), em_files[0], em_files[1])

    ths_items = fetch_ths_day(args.date, page_size=args.page_size, max_pages=args.max_pages)
    ths_files = write_source_outputs(repo, args.date, "ths", "同花顺", ths_items)
    files.extend(ths_files)
    source_results["ths"] = (len(ths_items), ths_files[0], ths_files[1])

    files.append(update_daily_index(repo, args.date, source_results))

    summary = f"东方财富 {len(eastmoney_items)} 条，同花顺 {len(ths_items)} 条"
    if args.no_push:
        print(f"多源资讯归档 {args.date}: {summary}，已写入本地，未推送。")
        return 0
    changed, detail = commit_and_push(repo, args.date, files)
    if changed:
        print(f"多源资讯归档 {args.date}: {summary}，已推送 GitHub commit {detail}。")
    else:
        print(f"多源资讯归档 {args.date}: {summary}，无新增变更。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
