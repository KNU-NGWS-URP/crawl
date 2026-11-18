"""km/pipeline.py — 코퍼스 v1 카테고리별 실행기. 카테고리마다 결과 폴더를 따로 만든다.

  data_v1/<category>/raw, snapshots, coverage, records, report, labels

실행 예:
  # 담당 카테고리 3개를 차례로 수집 + 후처리 (카테고리당 약 2시간)
  python -m km.pipeline --categories social,productivity,cloud --channel chrome \
      --delay 30 --site-gap 60 --max-blocks-per-site 2

  # 일부 사이트만 시험
  python -m km.pipeline --categories social --sites reddit.com,medium.com --channel chrome

  # 중간에 멈췄으면 이어서 (coverage 에 모든 화면이 기록된 사이트는 건너뜀)
  python -m km.pipeline --categories social,productivity,cloud --channel chrome --resume ...

  # 이미 수집한 결과의 후처리만 다시 (재수집 없음)
  python -m km.pipeline --categories social --post-only

후처리 순서: reclassify → normalize → summarize → labelsheet export(자동 C1 사전 라벨) → labelsheet apply
라벨 시트가 이미 있으면 덮어쓰지 않고, 기존 입력을 이어받은 새 시트(label_sheet.new.xlsx)를 만든다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import collect, labelsheet, normalize, reclassify, summarize


def corpus_path(config_dir: Path, prefix: str, cat: str) -> Path:
    return config_dir / f"{prefix}_{cat}.csv"


def post_process(data: Path, corpus: Path):
    reclassify.main(["--data", str(data)])
    normalize.main(["--data", str(data), "--corpus", str(corpus)])
    summarize.main(["--data", str(data), "--manual-sites", "5"])
    sheet = data / "labels" / "label_sheet.xlsx"
    if sheet.exists():
        new = data / "labels" / "label_sheet.new.xlsx"
        labelsheet.main(["export", "--data", str(data), "--out", str(new), "--prev", str(sheet), "--force"])
        print(f"[pipeline] 기존 시트 유지, 이어받은 새 시트: {new}")
    else:
        labelsheet.main(["export", "--data", str(data)])
    labelsheet.main(["apply", "--data", str(data), "--sheet", str(sheet)])


def main(argv=None):
    ap = argparse.ArgumentParser(description="코퍼스 v1 카테고리별 수집·후처리 (결과는 <out>/<category>/)")
    ap.add_argument("--categories", required=True, help="쉼표 구분 (예: social,productivity,cloud)")
    ap.add_argument("--config-dir", default=Path("config"), type=Path)
    ap.add_argument("--prefix", default="corpus_v1")
    ap.add_argument("--out", default=Path("data_v1"), type=Path)
    ap.add_argument("--post-only", action="store_true", help="수집 없이 후처리만")
    ap.add_argument("--skip-post", action="store_true", help="수집만")
    args, collect_args = ap.parse_known_args(argv)  # 나머지 인자는 km.collect 로 그대로 전달
    # 로그를 파일로 돌리면 Windows 기본 인코딩(cp949)이 되어 제목의 특수문자에서 멈춘다 → UTF-8, 못 쓰는 문자는 대체
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    for cat in cats:
        corpus = corpus_path(args.config_dir, args.prefix, cat)
        if not corpus.exists():
            raise SystemExit(f"[pipeline] {corpus} 없음 — 먼저 python -m km.corpus_import 로 생성")
    for cat in cats:
        corpus = corpus_path(args.config_dir, args.prefix, cat)
        data = args.out / cat
        print(f"\n[pipeline] === {cat}: {corpus} -> {data} ===", flush=True)
        if not args.post_only:
            collect.main(["--corpus", str(corpus), "--out", str(data), *collect_args])
        if not args.skip_post:
            post_process(data, corpus)
    print(f"\n[pipeline] done: {', '.join(str(args.out / c) for c in cats)}")


if __name__ == "__main__":
    sys.exit(main())
