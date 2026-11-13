"""km/summarize.py — 파일럿 결과 요약 (차주 보고용 표) + 수동 점검 시트 생성.

실행:
    python -m km.summarize --data data --manual-sites 5

산출 (data/report/):
    summary.md               커버리지·수집기별 건수·교차검증·라이브러리 신호 요약
    coverage_matrix.csv      사이트 × 화면 수집 상태
    per_site_counts.csv      사이트별 reach_path × evidence_source 레코드 수 / 고유 action_id 수
    handler_crosscheck.csv   래핑 수집 vs CDP 조회 키 리스너 수 (화면별)
    manual_check.csv         수동 점검 시트 (DevTools 대조용, 리뷰어 기입 칸 포함)

주의: 이 단계의 수치는 '정적 근거 수집량'이며 위험 셀 수가 아니다.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

VIEWS = ["home", "search", "product", "cart", "login"]
REACH = ["accesskey", "enter-submit", "hotkey", "focused-control", "unknown"]


def read_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def latest_coverage(data: Path) -> list[dict]:
    files = sorted((data / "coverage").glob("coverage_*.csv"))
    rows: dict = {}
    for fp in files:  # 나중 실행이 앞 실행을 덮어씀 (site, view 기준)
        for r in read_csv(fp):
            rows[(r["site"], r["view"])] = r
    return list(rows.values())


def md_table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=Path("data"), type=Path)
    ap.add_argument("--manual-sites", type=int, default=5)
    ap.add_argument("--manual-per-site", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261112)
    args = ap.parse_args(argv)
    data = args.data
    rep = data / "report"
    rep.mkdir(parents=True, exist_ok=True)

    cov = latest_coverage(data)
    recs_all = [json.loads(l) for l in (data / "records" / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] \
        if (data / "records" / "records.jsonl").exists() else []
    # 차단·챌린지·중간 페이지 등 의도한 화면이 아닌 곳에서 나온 레코드는 집계에서 뺀다
    # (정의서 5장: 미수집으로 보고). records.jsonl 에는 그대로 남아 있다.
    view_status = {(r["site"], r["view"]): r["status"] for r in latest_coverage(data)}
    recs = [r for r in recs_all if view_status.get((r["site"], r["view"])) == "ok"
            and not (r.get("aux") or {}).get("blocked_suspected")]
    n_blocked_recs = len(recs_all) - len(recs)
    vstats = read_csv(data / "records" / "view_stats.csv")
    signals = read_csv(data / "records" / "site_signals.csv")
    sites = sorted({r["site"] for r in cov} | {r["site"] for r in recs})

    # 1) 커버리지 행렬
    cmat = {(r["site"], r["view"]): r["status"] for r in cov}
    with (rep / "coverage_matrix.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["site"] + VIEWS + ["n_ok_views"])
        for s in sites:
            st = [cmat.get((s, v), "-") for v in VIEWS]
            w.writerow([s] + st + [sum(x == "ok" for x in st)])
    status_counts = Counter(r["status"] for r in cov)

    # 2) 사이트별 reach_path × evidence_source
    per = defaultdict(Counter)
    uniq = defaultdict(lambda: defaultdict(set))
    for r in recs:
        per[r["site"]][(r["reach_path"], r["evidence_source"])] += 1
        uniq[r["site"]][r["reach_path"]].add(r["action_id"])
    combos = sorted({k for c in per.values() for k in c})
    with (rep / "per_site_counts.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["site"] + [f"{a}/{b}" for a, b in combos] + [f"uniq_actions:{rp}" for rp in REACH])
        for s in sites:
            w.writerow([s] + [per[s].get(k, 0) for k in combos] + [len(uniq[s][rp]) for rp in REACH])

    # 3) 래핑 vs CDP 교차검증
    with (rep / "handler_crosscheck.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["site", "view", "status", "wrapped_key_listeners", "cdp_key_listeners(top frame)", "note"])
        for v in vstats:
            wk = int(v.get("wrapped_key_listeners") or 0)
            ck = v.get("cdp_key_listeners")
            note = ""
            if ck not in (None, "", "None"):
                ck_i = int(ck)
                if ck_i > wk:
                    note = "CDP > wrapper: 래핑 누락 가능 (계측 이전 등록·특수 경로)"
                elif wk > ck_i:
                    note = "wrapper > CDP: iframe·제거된 리스너·분리된 노드 포함 가능"
            w.writerow([v["site"], v["view"], v["status"], wk, ck, note])

    # 4) 수동 점검 시트
    rng = random.Random(args.seed)
    ok_sites = [s for s in sites if any(cmat.get((s, v)) == "ok" for v in VIEWS)]
    pick = sorted(rng.sample(ok_sites, min(args.manual_sites, len(ok_sites))))
    manual = []
    for s in pick:
        srecs = [r for r in recs if r["site"] == s]
        by_rp = defaultdict(list)
        for r in srecs:
            by_rp[r["reach_path"]].append(r)
        chosen = []
        quota = max(1, args.manual_per_site // max(1, len(by_rp)))
        for rp, lst in by_rp.items():
            chosen += rng.sample(lst, min(quota, len(lst)))
        for r in chosen[: args.manual_per_site]:
            manual.append({
                "site": s, "view": r["view"], "record_id": r["record_id"], "reach_path": r["reach_path"],
                "evidence_source": r["evidence_source"], "action_name": r["action_name"],
                "output_sequence": r["output_sequence"], "focus_premise": r["focus_premise"],
                "evidence_ref": r["evidence_ref"],
                "reviewer": "", "exists_in_devtools(Y/N)": "", "label_correct(Y/N)": "",
                "missed_items_on_page": "", "notes": "",
            })
    if manual:
        with (rep / "manual_check.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(manual[0].keys()))
            w.writeheader()
            w.writerows(manual)

    # 5) summary.md
    lib_sites = defaultdict(set)
    for sg in signals:
        if sg["signal"].startswith("library:") and sg["level"] == "strong":
            lib_sites[sg["signal"][8:]].add(sg["site"])
    fw_sites = defaultdict(set)
    for sg in signals:
        if sg["signal"].startswith("framework:"):
            fw_sites[sg["signal"][10:]].add(sg["site"])
    rp_counts = Counter(r["reach_path"] for r in recs)
    rp_uniq = defaultdict(set)
    for r in recs:
        rp_uniq[r["reach_path"]].add((r["site"], r["action_id"]))
    label_counts = Counter(r["action_label"] or "(none)" for r in recs)
    sites_with = {rp: len({s for s, _ in rp_uniq[rp]}) for rp in REACH}

    lines = ["# Tier 1 커머스 파일럿 요약", "",
             "> 이 수치는 정적 근거 수집량이다. 모든 레코드는 reachability=unknown, consequence/confirmation=unknown, "
             "danger_status=unresolved 상태이며 위험 셀 판정이 아니다.", "",
             f"- 대상 사이트: {len(sites)} / 화면 시도: {len(cov)}",
             f"- ok 화면이 1개 이상인 사이트: {sum(any(cmat.get((s, v)) == 'ok' for v in VIEWS) for s in sites)}",
             "- 수집 상태: " + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())),
             f"- 집계 레코드: {len(recs)} (ok가 아닌 화면 — 차단·로그인 리다이렉트·중간 페이지 등 — 의 레코드 {n_blocked_recs}건은 미수집으로 보고 제외)", "",
             "## 도달 경로별", "",
             md_table(["reach_path", "레코드 수", "고유 action 수", "해당 사이트 수"],
                      [[rp, rp_counts.get(rp, 0), len(rp_uniq[rp]), sites_with[rp]] for rp in REACH]), "",
             "## action_label 1차 추정 분포 (키워드 기반, 확정 아님)", "",
             md_table(["label", "레코드 수"], [[k, v] for k, v in label_counts.most_common()]), "",
             "## 단축키 라이브러리 강 시그니처 (static-bundle)", "",
             md_table(["library", "사이트 수", "사이트"], [[k, len(v), ", ".join(sorted(v))] for k, v in sorted(lib_sites.items())])
             if lib_sites else "(탐지 없음)", "",
             "## 프레임워크 탐지", "",
             md_table(["framework", "사이트 수"], [[k, len(v)] for k, v in sorted(fw_sites.items())]) if fw_sites else "(탐지 없음)", "",
             "## 산출 파일", "",
             "- coverage_matrix.csv / per_site_counts.csv / handler_crosscheck.csv / manual_check.csv", ""]
    (rep / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[summarize] -> {rep}")


if __name__ == "__main__":
    main()
