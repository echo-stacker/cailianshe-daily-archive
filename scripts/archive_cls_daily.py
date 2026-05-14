#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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
API_URL = "https://www.cls.cn/v1/roll/get_roll_list"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.cls.cn/telegraph",
}


def cls_sign(params: dict[str, Any]) -> str:
    """財联社 Web roll API signature: md5(sha1(sorted_query_string))."""

    parts = []
    for key in sorted(params, key=lambda value: str(value).upper()):
        value = params[key]
        if value is None:
            value = ""
        parts.append(f"{key}={value}")
    query = "&".join(parts)
    sha1 = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1.encode("utf-8")).hexdigest()


def fetch_page(last_time: int, rn: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "app": "CailianpressWeb",
        "category": "",
        "last_time": last_time,
        "os": "web",
        "refresh_type": 1,
        "rn": rn,
        "sv": "8.4.6",
    }
    params["sign"] = cls_sign(params)
    url = API_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("errno") not in (0, "0"):
        raise RuntimeError(f"CLS API errno={payload.get('errno')} msg={payload.get('msg')}")
    rows = (payload.get("data") or {}).get("roll_data") or []
    return [row for row in rows if not row.get("is_ad")]


def clean_text(value: Any, limit: int = 10_000) -> str:
    text = " ".join(str(value or "").replace("\u3000", " ").split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def normalize_item(row: dict[str, Any]) -> dict[str, Any]:
    ctime = int(row.get("ctime") or 0)
    dt = datetime.fromtimestamp(ctime, tz=SH_TZ) if ctime else None
    title = clean_text(row.get("title"), 300)
    content = clean_text(row.get("content") or row.get("brief"), 5000)
    stock_list = []
    for stock in row.get("stock_list") or []:
        if not isinstance(stock, dict):
            continue
        stock_list.append(
            {
                "name": clean_text(stock.get("name"), 80),
                "code": clean_text(stock.get("code") or stock.get("symbol"), 32),
            }
        )
    subjects = []
    for subject in row.get("subjects") or []:
        if isinstance(subject, dict) and subject.get("subject_name"):
            subjects.append(clean_text(subject.get("subject_name"), 120))
    return {
        "source": "cls",
        "source_name": "财联社",
        "id": str(row.get("id") or ""),
        "ctime": ctime,
        "datetime": dt.isoformat() if dt else "",
        "date": dt.strftime("%Y-%m-%d") if dt else "",
        "time": dt.strftime("%H:%M:%S") if dt else "",
        "level": clean_text(row.get("level"), 20),
        "title": title,
        "content": content,
        "brief": clean_text(row.get("brief"), 1000),
        "category": clean_text(row.get("category"), 80),
        "subjects": subjects,
        "stocks": stock_list,
        "shareurl": clean_text(row.get("shareurl"), 500),
        "raw": row,
    }


def fetch_day(target_date: str, *, rn: int = 50, max_pages: int = 120, pause: float = 0.25) -> list[dict[str, Any]]:
    day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=SH_TZ)
    start_ts = int(day.timestamp())
    end_ts = int((day + timedelta(days=1)).timestamp())
    last_time = end_ts
    by_id: dict[str, dict[str, Any]] = {}

    for _ in range(max_pages):
        rows = fetch_page(last_time=last_time, rn=rn)
        if not rows:
            break
        min_ctime = min(int(row.get("ctime") or 0) for row in rows)
        for row in rows:
            ctime = int(row.get("ctime") or 0)
            if start_ts <= ctime < end_ts:
                item = normalize_item(row)
                if item["id"]:
                    by_id[item["id"]] = item
        if min_ctime < start_ts:
            break
        if min_ctime >= last_time:
            break
        last_time = min_ctime
        time.sleep(pause)

    return sorted(by_id.values(), key=lambda item: (int(item.get("ctime") or 0), item.get("id", "")))


def write_outputs(repo: Path, target_date: str, items: list[dict[str, Any]]) -> list[Path]:
    year = target_date[:4]
    data_dir = repo / "sources" / "cls" / "data" / year
    data_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = data_dir / f"{target_date}.jsonl"
    md_path = data_dir / f"{target_date}.md"
    manifest_path = repo / "manifests" / "daily_index.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    jsonl_path.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items), encoding="utf-8")
    lines = [f"# 财联社快讯归档 {target_date}", "", f"- item_count: {len(items)}", "- source: 财联社 Web roll API", ""]
    for item in items:
        title = item.get("title") or "无标题"
        content = item.get("content") or item.get("brief") or ""
        stocks = item.get("stocks") or []
        stock_text = "，".join(f"{s.get('name')}({s.get('code')})" for s in stocks if s.get("name") or s.get("code"))
        lines.extend([f"## {item.get('time')}｜{title}", "", f"- id: `{item.get('id')}`", f"- level: {item.get('level') or '-'}"])
        if stock_text:
            lines.append(f"- stocks: {stock_text}")
        if item.get("subjects"):
            lines.append("- subjects: " + "，".join(item.get("subjects") or []))
        if item.get("shareurl"):
            lines.append(f"- url: {item.get('shareurl')}")
        lines.extend(["", str(content), ""])
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    manifest = {"dates": {}}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {"dates": {}}
    manifest.setdefault("dates", {})[target_date] = {
        "item_count": len(items),
        "sources": {
            "cls": {
                "item_count": len(items),
                "jsonl": jsonl_path.relative_to(repo).as_posix(),
                "markdown": md_path.relative_to(repo).as_posix(),
            }
        },
        "jsonl": jsonl_path.relative_to(repo).as_posix(),
        "markdown": md_path.relative_to(repo).as_posix(),
        "updated_at": datetime.now(SH_TZ).isoformat(timespec="seconds"),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return [jsonl_path, md_path, manifest_path]


def run_git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def commit_and_push(repo: Path, target_date: str, files: list[Path]) -> tuple[bool, str]:
    run_git(repo, ["add", *[path.relative_to(repo).as_posix() for path in files]])
    status = run_git(repo, ["status", "--short"])
    if not status:
        return False, "no changes"
    run_git(repo, ["commit", "-m", f"data(cls): archive {target_date}"])
    run_git(repo, ["push", "origin", "main"])
    sha = run_git(repo, ["rev-parse", "--short", "HEAD"])
    return True, sha


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive one day of public 财联社快讯 data and push to GitHub.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--date", default=datetime.now(SH_TZ).strftime("%Y-%m-%d"))
    parser.add_argument("--rn", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    items = fetch_day(args.date, rn=args.rn, max_pages=args.max_pages)
    files = write_outputs(repo, args.date, items)
    if args.no_push:
        print(f"财联社归档 {args.date}: {len(items)} 条，已写入本地，未推送。")
        return 0
    changed, detail = commit_and_push(repo, args.date, files)
    if changed:
        print(f"财联社归档 {args.date}: {len(items)} 条，已推送 GitHub commit {detail}。")
    else:
        print(f"财联社归档 {args.date}: {len(items)} 条，无新增变更。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
