# Multi-Source Market Intelligence Archive

Daily archive and Wiki builder for market-moving news. The first enabled source is public 财联社电报 / 快讯 data; the repository is structured so Reuters, exchange announcements, company filings, and other feeds can be added without changing the Wiki builder.

## Layout

```text
sources/
  cls/
    data/YYYY/YYYY-MM-DD.jsonl   # one normalized 财联社 item per line
    data/YYYY/YYYY-MM-DD.md      # human-readable source digest
  reuters/
    data/YYYY/YYYY-MM-DD.jsonl   # reserved extension slot, disabled by default
manifests/
  source_registry.json           # source adapters, paths, license notes
  daily_index.json               # archived dates and per-source paths
insights/
  YYYY/YYYY-MM-DD.json           # machine-readable high-information ranking
wiki/
  index.md                       # Wiki entrypoint
  daily/YYYY-MM-DD.md            # daily intelligence page
  topics/<topic>.md              # topic pages generated from source tags
scripts/
  archive_cls_daily.py           # 财联社 crawler + commit/push entrypoint
  build_daily_wiki.py            # multi-source insight scorer + Wiki builder
```

Legacy `data/YYYY/*` files may exist from the initial bootstrap; new archives use the source-scoped `sources/<source_id>/data/` layout.

## Normalized item schema

Each JSONL row should use a stable, source-neutral subset:

```json
{
  "source": "cls",
  "source_name": "财联社",
  "id": "unique-source-id",
  "date": "YYYY-MM-DD",
  "time": "HH:MM:SS",
  "title": "headline",
  "content": "body or excerpt",
  "subjects": ["topic"],
  "stocks": [{"name": "公司", "code": "000001"}],
  "url": "optional canonical url"
}
```

Source-specific raw payloads can be kept under `raw`, but downstream Wiki/insight logic should depend on the normalized fields above.

## Daily automation

Hermes cron job `b89810a985b1` runs every day at 23:55 Asia/Shanghai via:

```bash
~/.hermes/scripts/cls_daily_archive_job.sh
```

The wrapper now performs two steps:

1. archive 财联社 data into `sources/cls/data/YYYY/` and push it;
2. build `insights/` and `wiki/` pages from every enabled source and push them.

## Information-value ranking

`scripts/build_daily_wiki.py` scores each item with deterministic features so it is auditable and does not require private API keys:

- policy / regulatory variables;
- price hikes, supply disruptions, inventory changes;
- orders, contracts, delivery, production capacity;
- earnings, buybacks, shareholder return;
- M&A / restructuring;
- overseas constraints such as tariffs, sanctions, export controls;
- explicit subjects and linked stocks;
- body length and duplicate-title penalty.

Outputs:

```bash
python3 scripts/build_daily_wiki.py --date YYYY-MM-DD --no-push
```

Important outputs:

- `insights/YYYY/YYYY-MM-DD.json`: top messages with score and reasons;
- `wiki/daily/YYYY-MM-DD.md`: human-readable daily Wiki page;
- `wiki/topics/*.md`: topic pages;
- `wiki/index.md`: Wiki entrypoint.

## Adding Reuters or another source

1. Add/enable a source in `manifests/source_registry.json`:

```json
"reuters": {
  "name": "Reuters",
  "enabled": true,
  "data_glob": "sources/reuters/data/{year}/{date}.jsonl",
  "markdown_glob": "sources/reuters/data/{year}/{date}.md",
  "source_type": "wire_news",
  "license_note": "Store metadata/links/short excerpts unless a licensed feed/export is configured."
}
```

2. Write normalized JSONL rows to that `data_glob` path.
3. Run:

```bash
python3 scripts/build_daily_wiki.py --date YYYY-MM-DD
```

Important: do not commit private feed credentials. For copyrighted feeds such as Reuters, keep the public GitHub archive limited to metadata, IDs, canonical links, tags, and short excerpts unless the account has redistribution rights.

## Future MemoWeaver flow

The generated daily Wiki pages can be used directly as MemoWeaver source material, or MemoWeaver can ingest the source-level Markdown/JSONL files:

```bash
memoweaver ingest wiki/daily/YYYY-MM-DD.md --wiki ./memoweaver-wiki --title "市场情报 YYYY-MM-DD"
memoweaver extract ./memoweaver-wiki/raw/articles/<source>.md --wiki ./memoweaver-wiki --model gpt-5.5
memoweaver write-pages --wiki ./memoweaver-wiki --source-id <source> --model gpt-5.5 --resolve
```
