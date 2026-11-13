"""km/tranco_rank.py — 고정한 Tranco 리스트로 코퍼스의 rank/rank_date를 채운다.

1) https://tranco-list.eu 에서 리스트 ID를 정해 CSV(top-1m.csv: "rank,domain")를 내려받는다.
2) python -m km.tranco_rank --corpus config/commerce_pilot.csv --tranco top-1m.csv --list-id XXXX --list-date 2026-11-01

순위를 추측해 넣지 않는다. 리스트에 없는 도메인은 rank를 'not_in_list'로 둔다.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--tranco", required=True, type=Path)
    ap.add_argument("--list-id", required=True)
    ap.add_argument("--list-date", required=True)
    a = ap.parse_args(argv)
    ranks = {}
    with a.tranco.open(encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].isdigit():
                ranks.setdefault(row[1].strip().lower(), int(row[0]))
    with a.corpus.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        fields, rows = rd.fieldnames, list(rd)
    for r in rows:
        r["rank"] = ranks.get(r["site"].lower(), "not_in_list")
        r["rank_source"] = f"tranco:{a.list_id}"
        r["rank_date"] = a.list_date
    with a.corpus.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"[tranco] updated {len(rows)} rows")


if __name__ == "__main__":
    main()
