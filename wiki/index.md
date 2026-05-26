# Multi-Source Market Intelligence Wiki

## Daily pages

- [[daily/2026-05-26|2026-05-26]]
- [[daily/2026-05-25|2026-05-25]]
- [[daily/2026-05-24|2026-05-24]]
- [[daily/2026-05-23|2026-05-23]]
- [[daily/2026-05-22|2026-05-22]]
- [[daily/2026-05-21|2026-05-21]]
- [[daily/2026-05-20|2026-05-20]]
- [[daily/2026-05-19|2026-05-19]]
- [[daily/2026-05-18|2026-05-18]]
- [[daily/2026-05-17|2026-05-17]]
- [[daily/2026-05-15|2026-05-15]]
- [[daily/2026-05-14|2026-05-14]]

## Source extensibility

新增 Reuters、交易所公告、公司公告等源时，只需：

1. 在 `manifests/source_registry.json` 增加 source 配置；
2. 将标准化 JSONL 写入该 source 的 `data_glob` 路径；
3. 运行 `scripts/build_daily_wiki.py --date YYYY-MM-DD`。
