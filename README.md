# Cailian Press Daily Archive

Daily archive of public 财联社电报 / 快讯 data, maintained by a local Hermes scheduled job.

## Layout

```text
data/
  YYYY/
    YYYY-MM-DD.jsonl   # one normalized item per line
    YYYY-MM-DD.md      # human-readable daily digest
manifests/
  daily_index.json     # append/update summary for archived dates
scripts/
  archive_cls_daily.py # crawler + commit/push entrypoint
```

## Data policy

- Source: public 财联社 web roll API.
- Frequency: once per day near end of day, Asia/Shanghai time.
- Stored format is intentionally simple so MemoWeaver can later ingest each daily Markdown/JSONL file as raw source material.
- Raw API secrets are not used or stored.

## Future MemoWeaver flow

```bash
memoweaver ingest data/YYYY/YYYY-MM-DD.md --wiki ./wiki --title "财联社快讯 YYYY-MM-DD"
memoweaver extract ./wiki/raw/articles/<source>.md --wiki ./wiki --model gpt-5.5
memoweaver write-pages --wiki ./wiki --source-id <source> --model gpt-5.5 --resolve
```
