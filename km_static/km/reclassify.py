"""km/reclassify.py — 이미 수집한 raw 결과의 화면 상태를 현재 판정 규칙(collect.classify_status)으로 다시 매긴다.

사이트에 다시 접속하지 않는다. 판정 규칙을 고친 뒤 기존 수집분에 소급 적용할 때 쓴다.

실행:
    python -m km.reclassify --data data_run1

동작:
  - data/raw/{site}/{view}.json 의 meta.status / meta.block_reasons 를 갱신 (이전 값은 meta.status_prev 에 보존)
  - 가장 최근 coverage CSV를 복사해 status 를 갱신한 coverage_{run}_reclass.csv 를 추가로 쓴다
    (summarize 는 파일명 순으로 나중 파일을 우선하므로 이 파일이 적용된다)
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .collect import classify_status, same_page

RECLASSIFIABLE = {"ok", "blocked_suspected", "login_redirect", "interstitial", "redirected_home"}


def body_of(raw: dict) -> str:
    m = raw.get("meta", {})
    if m.get("body_probe"):
        return m["body_probe"]
    top = next((f for f in raw.get("frames", []) if f.get("is_top")), {})
    return (top.get("dom") or {}).get("body_text_sample", "")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=Path("data"), type=Path)
    args = ap.parse_args(argv)
    data = args.data

    changed = {}
    for fp in sorted((data / "raw").glob("*/*.json")):
        raw = json.loads(fp.read_text(encoding="utf-8"))
        m = raw.get("meta", {})
        key = (m.get("site"), m.get("view", fp.stem))
        # coverage 는 항상 raw 의 현재 상태를 따른다 (재판정하지 않는 화면 포함)
        changed[key] = m.get("status")
        if m.get("status") not in RECLASSIFIABLE:
            continue
        # 본문 전체(body_probe)가 없는 옛 수집분은 1500자 샘플만 남아 있어, 처음 판정 때 본 차단 문구가 빠져 있을 수 있다.
        # 이 경우 blocked_suspected 를 풀지 않는다 (차단을 정상으로 잘못 되돌리지 않기 위해).
        # body_probe 도 앞 3000자로 잘려 있어 (수집 시 판정은 30000자 기준) 뒤쪽 캡차 문구(예: walmart PX 'Robot or human')가 빠진다.
        # 따라서 재판정은 blocked_suspected 를 절대 풀지 않는다 — 다른 상태를 차단으로 올리는 방향만 허용.
        if m.get("status") == "blocked_suspected":
            continue
        st, reasons = classify_status(m.get("view", fp.stem), m.get("requested_url", ""), m.get("final_url", ""),
                                      m.get("http_status"), m.get("title", ""), body_of(raw))
        if st != m.get("status"):
            print(f"  {key[0]}/{key[1]}: {m.get('status')} -> {st} {reasons}")
            m.setdefault("status_prev", m.get("status"))
        m["status"] = st
        m["block_reasons"] = reasons
        changed[key] = st
        fp.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8", errors="replace")

    # 같은 사이트에서 앞 화면과 같은 페이지에 도착한 ok 화면은 서로 다른 화면이 아니다 → duplicate_page
    dup_order = ["entry", "home", "search", "product", "cart", "login", "signup", "explore", "pricing", "content",
                 "features"]
    finals: dict = {}
    for fp in sorted((data / "raw").glob("*/*.json")):
        raw = json.loads(fp.read_text(encoding="utf-8"))
        m = raw.get("meta", {})
        key = (m.get("site"), m.get("view", fp.stem))
        if changed.get(key) == "ok":
            finals[key] = (m.get("final_url", ""), fp)
    for site in sorted({k[0] for k in finals}):
        seen_ok = []
        for view in sorted((v for s_, v in finals if s_ == site),
                           key=lambda v: dup_order.index(v) if v in dup_order else 99):
            fin, fp = finals[(site, view)]
            dup = next((v for v, u in seen_ok if same_page(u, fin)), None)
            if dup:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                m = raw["meta"]
                m.setdefault("status_prev", m.get("status"))
                m["status"], m["block_reasons"] = "duplicate_page", [f"same_final_url_as:{dup}"]
                fp.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8", errors="replace")
                changed[(site, view)] = "duplicate_page"
                print(f"  {site}/{view}: ok -> duplicate_page (same final URL as {dup})")
            else:
                seen_ok.append((view, fin))

    covs = sorted(p for p in (data / "coverage").glob("coverage_*.csv") if not p.stem.endswith("_reclass"))
    if not covs:
        print("[reclassify] coverage 파일 없음")
        return
    # 부분 재수집(일부 사이트·화면만 다시 실행)을 고려해 모든 실행의 coverage 를 시간 순으로 병합한다 (나중 실행 우선)
    src = covs[-1]
    by_site_view: dict = {}
    for fp in covs:
        with fp.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                by_site_view[(r["site"], r["view"])] = r
    rows = list(by_site_view.values())
    for (site, view), st in changed.items():
        r = by_site_view.get((site, view))
        if r:
            r["status"] = st
    # 검색 화면이 ok 가 아니게 됐으면 상품 화면의 not_found 는 skipped_search_failed 로 맞춘다
    for r in rows:
        if r["view"] == "product" and r["status"] == "not_found":
            s = by_site_view.get((r["site"], "search"))
            if s and s["status"] != "ok":
                r["status"] = "skipped_search_failed"
        # 코퍼스 v1: entry 화면이 수집 실패로 바뀌었으면 login·signup 의 not_found 도 skipped_entry_failed 로 맞춘다
        if r["view"] in ("login", "signup") and r["status"] == "not_found":
            e = by_site_view.get((r["site"], "entry"))
            if e and e["status"] not in ("ok", "login_redirect"):
                r["status"] = "skipped_entry_failed"
    out = src.with_name(src.stem + "_reclass.csv")
    fields = list(dict.fromkeys(k for r in rows for k in r))  # 실행마다 칸이 다를 수 있음 (예: url_source 추가 전 실행분)
    with out.open("w", encoding="utf-8-sig", errors="replace", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"[reclassify] {len(changed)} views checked -> {out}")


if __name__ == "__main__":
    main()
