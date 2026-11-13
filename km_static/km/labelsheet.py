"""km/labelsheet.py — ③ 수동 라벨링: 결과 등급(C)·확인 등급(V) 입력 시트 생성과 반영.

Tier 1 레코드는 정의서대로 C/V가 모두 unknown 이다. 이 모듈은 사람이 근거를 보고 판정을 입력할 수 있도록
후보 액션만 추린 엑셀 시트를 만들고, 입력값을 정의서 V2 규칙으로 계산해 레코드에 반영한다.

실행:
    python -m km.labelsheet export --data data_run1                       # → data_run1/labels/label_sheet.xlsx
    python -m km.labelsheet export --data data_run1 --prev 이전시트.xlsx   # 이전 입력값 이어받기
    python -m km.labelsheet apply  --data data_run1 --sheet data_run1/labels/label_sheet.xlsx
    python -m km.labelsheet agree  --a 라벨러A.xlsx --b 라벨러B.xlsx       # 라벨러 간 일치도 (Cohen's kappa)

판정 규칙 (정의서 V2):
  - 술어 P1~P4, S, R1~R3, E 는 Y(근거 있음: 참) / N(근거 있음: 거짓) / 빈칸(근거 부족)으로 입력한다.
    빈칸을 N 으로 간주하지 않는다 (¬K(φ) ⇏ K(¬φ)).
  - C(a): 위에서 아래로 첫 번째로 충족한 절
        C3  any(P = Y)
        C2  all(P = N) ∧ S = Y ∧ all(R = Y)
        C1  all(P = N) ∧ S = N ∧ E = Y
        C0  all(P = N) ∧ S = N ∧ E = N
        unknown  그 외
  - V(a): 직접 입력 (V0 / V1 / V2 / unknown)
  - danger_status (본문 4장):
        excluded    도달 불가 확인, C ∈ {C0, C1, C2}, 또는 V = V2  (조건 하나 이상 불충족 확인)
        confirmed   reachable ∧ C3 ∧ V ∈ {V0, V1} ∧ 최종 검토 완료(Y)
        candidate   위 세 조건을 만족하지만 최종 검토 전, 또는 불충족이 없고 C3 근거가 있음
        unresolved  그 외 (근거 부족) — 자료 부족만으로 제외하지 않는다
  - is_danger_cell: confirmed → true, excluded → false, 그 외 → null
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

YN = ["Y", "N"]
KEYS_IN = ["Escape", "Tab", "ArrowDown", "Home", "End"]

# (열 이름, 설명, 허용값 또는 None, 너비)
PREFILLED = [
    ("row_key", "시트 행 식별자 (수정 금지)", None, 14),
    ("site", "사이트", None, 14),
    ("views", "관측 화면 (여러 화면에서 같은 요소면 모두 표기)", None, 14),
    ("reach_path", "도달 경로 (Tier 1)", None, 13),
    ("output_sequence", "출력 키 (Tier 1 추정)", None, 16),
    ("action_name", "요소 텍스트·설명 (Tier 1)", None, 40),
    ("action_label_t1", "action_label 1차 추정 (키워드)", None, 13),
    ("focus_premise", "포커스·로그인 전제 (Tier 1)", None, 30),
    ("evidence_ref_t1", "Tier 1 근거 위치 (data 폴더 기준)", None, 30),
    ("snapshot", "스냅샷 경로", None, 24),
]
INPUTS = [
    ("action_label", "대표 액션 분류 (Appendix E). 1차 추정이 틀리면 고침. 해당 없음이면 none",
     ["financial", "destructive", "transmission", "publication", "auth_change", "execution", "none"], 13),
    ("reachability", "명시한 조건에서 출력 키로 발동 가능한가", ["reachable", "unreachable", "unknown"], 12),
    ("physical_trigger_key", "입력 물리 키 후보 (미선정이면 빈칸)", KEYS_IN, 12),
    ("P1", "P₁ 실질 자산 이동 (금전·크레딧·포인트가 제3자 권한으로 이동)", YN, 5),
    ("P2", "P₂ 외부 데이터 노출 (사용자 권한 밖 외부 수신자)", YN, 5),
    ("P3", "P₃ 영구 삭제 (표준 절차로 복원 불가)", YN, 5),
    ("P4", "P₄ 동일 조건 복원 불가 (프로모션·크레딧 소멸 등)", YN, 5),
    ("S", "S 측정 대상의 지속적 데이터·설정 변경 (화면 변화·세션 변화 제외)", YN, 5),
    ("R1", "R₁ 표준 UI 로 복원 가능", YN, 5),
    ("R2", "R₂ 사용자 권한으로 복원 가능 (운영자 요청 불필요)", YN, 5),
    ("R3", "R₃ 복원 전 확정되는 부수 피해 없음", YN, 5),
    ("E", "E 관측 가능한 효과 (메뉴 열기·화면 이동 포함)", YN, 5),
    ("C_auto", "결과 등급 — 자동 계산 (수정 금지). 빈칸은 근거 부족으로 처리", None, 9),
    ("confirmation_tier", "확인 등급 V0 없음 / V1 같은 화면 1회 / V2 재인증·2FA / unknown (Appendix C)",
     ["V0", "V1", "V2", "unknown"], 11),
    ("confirm_steps", "관측한 추가 확인 단계 수", ["0", "1", "2+"], 8),
    ("strong_auth", "비밀번호·2FA·SMS 등 강한 인증 요구", YN, 8),
    ("final_review", "최종 검토 완료 (confirmed 확정에 필요)", YN, 9),
    ("danger_auto", "위험 셀 판정 — 자동 계산 (수정 금지)", None, 11),
    ("evidence_source", "판정에 쓴 대표 근거 (빈칸이면 Tier 1 값 유지)",
     ["manual-doc", "tier2-verified", "static-accesskey", "static-form", "static-handler", "static-bundle",
      "static-dom"], 14),
    ("evidence_ref", "근거 URL·스크린샷·로그 위치 또는 관측 내용", None, 30),
    ("confidence", "판정 근거의 충분성", ["high", "medium", "low"], 9),
    ("tier2_verified", "Tier 2 에서 확인했으면 Y", YN, 8),
    ("protection_note", "관측한 보호장치 (확인 모달, 재인증 등)", None, 24),
    ("reviewer", "라벨러", None, 10),
    ("notes", "메모", None, 30),
]
COLS = PREFILLED + INPUTS
IDX = {name: i + 1 for i, (name, *_rest) in enumerate(COLS)}


# ------------------------------------------------------------------ 판정 규칙
def _v(x) -> str:
    return str(x).strip().upper() if x not in (None, "") else ""


def consequence_tier(row: dict) -> str:
    P = [_v(row.get(f"P{i}")) for i in range(1, 5)]
    R = [_v(row.get(f"R{i}")) for i in range(1, 4)]
    S, E = _v(row.get("S")), _v(row.get("E"))
    if any(p == "Y" for p in P):
        return "C3"
    if not all(p == "N" for p in P):
        return "unknown"
    if S == "Y" and all(r == "Y" for r in R):
        return "C2"
    if S == "N" and E == "Y":
        return "C1"
    if S == "N" and E == "N":
        return "C0"
    return "unknown"


def danger_status(reach: str, c: str, v: str, review: str) -> str:
    reach = (reach or "unknown").strip()
    v = (v or "unknown").strip()
    if reach == "unreachable" or c in ("C0", "C1", "C2") or v == "V2":
        return "excluded"
    all_ok = reach == "reachable" and c == "C3" and v in ("V0", "V1")
    if all_ok and _v(review) == "Y":
        return "confirmed"
    if all_ok or c == "C3":
        return "candidate"
    return "unresolved"


def is_danger_cell(status: str):
    return {"confirmed": True, "excluded": False}.get(status)


# ------------------------------------------------------------------ 자동 사전 라벨 (뻔한 C1)
# 정적 근거만으로 '페이지 이동·포커스 이동'(정의서 B.4 → C1)이라고 볼 수 있는 경우에만 채운다.
# C3·V 등급은 자동으로 정하지 않는다 (정의서 2장·Appendix C: 이름·정적 신호만으로 확정하지 않음).
# 자동 라벨은 reviewer=auto-prelabel 로 표시하며, 사람이 표본 검토한다.
import re  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

PRELABEL_REVIEWER = "auto-prelabel"
DRAFT_REVIEWER = "claude-draft"  # 행별 근거를 보고 쓴 초안 — 사람 검토 전에는 확정 라벨이 아님
# URL 경로·쿼리에 이 단어가 있으면 GET 이라도 부수효과가 있을 수 있으므로 자동 라벨하지 않는다
SIDE_EFFECT_URL_RX = re.compile(
    r"(?<![a-z])(log-?out|sign-?out|log-?in|sign-?in|sign-?up|register|auth|account|identity|passport|"
    r"delete|remove|cancel|unsubscribe|subscribe|checkout|buy|purchase|order|orders|pay|payment|"
    r"add-?to-?cart|add_to_cart|addtocart|cart|basket|bag|koszyk|confirm|transfer|send|share|"
    r"coupon|claim|redeem|vote|follow|wishlist|favorite)(?![a-z])", re.IGNORECASE)
# 텍스트가 이런 동작이면 링크라도 클릭 핸들러가 기본 동작을 막고 실행할 수 있으므로 자동 라벨하지 않는다
STRONG_ACTION_TEXT_RX = re.compile(
    r"add to (cart|bag|basket)|buy now|buy it now|place (your )?order|check ?out|proceed to|pay now|"
    r"delete|remove|unsubscribe|cancel order|sign out|log ?out|"
    r"장바구니 ?담기|바로 ?구매|구매하기|결제하기|주문하기|삭제|로그아웃|"
    r"カートに入れる|購入|加入购物车|立即购买|в корзину|купить|do koszyka|kup teraz", re.IGNORECASE)
SEARCH_URL_RX = re.compile(r"(?<![a-z])(search|searchpage|sch|s|find|query|wholesale)(?![a-z])", re.IGNORECASE)


def _url_rest(u: str) -> str:
    p = urlparse(u or "")
    return f"{p.path}?{p.query}"


def prelabel(r: dict) -> dict | None:
    """레코드 하나에 대해 자동 C1 사전 라벨을 반환한다. 해당 없으면 None."""
    aux = r.get("aux") or {}
    rp = r.get("reach_path")
    text = " ".join(str(x) for x in (r.get("action_name"), aux.get("default_button_text")) if x)
    if STRONG_ACTION_TEXT_RX.search(text or ""):
        return None
    base = {"P1": "N", "P2": "N", "P3": "N", "P4": "N", "S": "N", "E": "Y", "reviewer": PRELABEL_REVIEWER}

    if rp == "enter-submit":
        method = (aux.get("method") or "get").lower()
        action = aux.get("action") or ""
        if method != "get" or SIDE_EFFECT_URL_RX.search(_url_rest(action)):
            return None
        if not aux.get("js_submit_handler_present"):
            return {**base, "evidence_source": "static-form", "confidence": "medium", "evidence_ref": action,
                    "notes": "[R-form] GET 폼, JS submit 핸들러 없음 → 제출은 페이지 이동 (정의서 B.4 C1)"}
        if SEARCH_URL_RX.search(urlparse(action).path):
            return {**base, "evidence_source": "static-form", "confidence": "low", "evidence_ref": action,
                    "notes": "[R-search] GET 검색 폼 (JS submit 핸들러 있음) → 검색 결과 이동으로 추정 (C1, 핸들러 동작 미확인)"}
        return None

    if rp == "focused-control" and aux.get("tag") == "a":
        href = aux.get("href") or ""
        p = urlparse(href)
        if p.scheme not in ("http", "https") or aux.get("onclick_attr"):
            return None
        if not p.path.strip("/") and not p.query and p.fragment:
            return None  # 같은 페이지 앵커(#...)는 JS 처리일 수 있음
        if SIDE_EFFECT_URL_RX.search(_url_rest(href)):
            return None
        return {**base, "evidence_source": "static-dom", "confidence": "medium", "evidence_ref": href,
                "notes": "[R-link] 일반 링크(href, 인라인 onclick 없음, 부수효과 URL·문구 없음) → 페이지 이동 (C1). "
                         "addEventListener 클릭 핸들러는 정적으로 확인 못 함"}

    if rp == "accesskey" and (aux.get("activation") or "").startswith("focus"):
        return {**base, "evidence_source": "static-accesskey", "confidence": "medium",
                "evidence_ref": f"accesskey={aux.get('accesskey')}",
                "notes": "[R-akey] 입력칸으로 포커스만 이동하는 accesskey → 화면상 포커스 변화 (C1)"}
    return None


# 엑셀에서 입력 즉시 보이도록 같은 규칙을 수식으로도 넣는다 (최종 값은 apply 가 Python 규칙으로 다시 계산)
def _c_formula(r: int) -> str:
    c = lambda n: f"{get_column_letter(IDX[n])}{r}"  # noqa: E731
    P = [c(f"P{i}") for i in range(1, 5)]
    R = [c(f"R{i}") for i in range(1, 4)]
    any_p = "OR(" + ",".join(f'{p}="Y"' for p in P) + ")"
    all_pn = "AND(" + ",".join(f'{p}="N"' for p in P) + ")"
    c2 = f'AND({c("S")}="Y",' + ",".join(f'{x}="Y"' for x in R) + ")"
    return (f'=IF({any_p},"C3",IF({all_pn},IF({c2},"C2",IF({c("S")}="N",'
            f'IF({c("E")}="Y","C1",IF({c("E")}="N","C0","unknown")),"unknown")),"unknown"))')


def _d_formula(r: int) -> str:
    c = lambda n: f"{get_column_letter(IDX[n])}{r}"  # noqa: E731
    reach, C, V, rev = c("reachability"), c("C_auto"), c("confirmation_tier"), c("final_review")
    fail = f'OR({reach}="unreachable",{C}="C0",{C}="C1",{C}="C2",{V}="V2")'
    ok = f'AND({reach}="reachable",{C}="C3",OR({V}="V0",{V}="V1"))'
    return (f'=IF({fail},"excluded",IF(AND({ok},{rev}="Y"),"confirmed",'
            f'IF(OR({ok},{C}="C3"),"candidate","unresolved")))')


# ------------------------------------------------------------------ 데이터 읽기
def ok_views(data: Path) -> dict:
    st = {}
    for fp in sorted((data / "coverage").glob("coverage_*.csv")):
        with fp.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                st[(r["site"], r["view"])] = r["status"]
    return st


def load_records(data: Path) -> list[dict]:
    p = data / "records" / "records.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def row_key(r: dict) -> str:
    return f'{r["site"]}|{r["action_id"]}|{r["reach_path"]}|{r["output_sequence"] or ""}'


def selected(r: dict) -> bool:
    """C/V 판정을 입력할 가치가 있는 후보만 시트에 넣는다."""
    if r["reach_path"] in ("enter-submit", "accesskey"):
        return True
    if r["reach_path"] in ("focused-control", "hotkey") and r.get("action_label"):
        return True
    return bool((r.get("aux") or {}).get("needs_manual_review"))


# ------------------------------------------------------------------ export
def read_sheet(path: Path) -> list[dict]:
    wb = load_workbook(path, data_only=False)
    ws = wb["labels"]
    header = [c.value for c in ws[1]]
    rows = []
    for vals in ws.iter_rows(min_row=2, values_only=True):
        if not vals or not vals[0]:
            continue
        rows.append({h: v for h, v in zip(header, vals) if h})
    return rows


def cmd_export(args):
    data = args.data
    st = ok_views(data)
    recs = [r for r in load_records(data) if st.get((r["site"], r["view"])) == "ok"
            and not (r.get("aux") or {}).get("blocked_suspected") and selected(r)]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        groups[row_key(r)].append(r)
    prev = {}
    if args.prev:
        prev = {str(r["row_key"]): r for r in read_sheet(args.prev)}

    order = {"enter-submit": 0, "accesskey": 1, "hotkey": 2, "focused-control": 3, "unknown": 4}
    lab_order = {"financial": 0, "destructive": 1, "transmission": 2, "publication": 3, "execution": 4,
                 "auth_change": 5, None: 6}
    keys = sorted(groups, key=lambda k: (groups[k][0]["site"], order.get(groups[k][0]["reach_path"], 9),
                                         lab_order.get(groups[k][0].get("action_label"), 9), k))

    wb = Workbook()
    ws = wb.active
    ws.title = "labels"
    grey = PatternFill("solid", fgColor="EDEDED")
    yellow = PatternFill("solid", fgColor="FFF7D6")
    auto = PatternFill("solid", fgColor="DDEBF7")
    for i, (name, desc, allowed, width) in enumerate(COLS, start=1):
        cell = ws.cell(row=1, column=i, value=name)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        note = desc + (f"\n허용값: {', '.join(allowed)} / 빈칸" if allowed else "")
        cell.comment = Comment(note, "km")
        cell.fill = grey if (name, desc, allowed, width) in PREFILLED else (
            auto if name in ("C_auto", "danger_auto") else yellow)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = ws.cell(row=2, column=IDX["action_name"] + 1)

    n_carried = 0
    n_pre = 0
    rules: Counter = Counter()
    for ri, k in enumerate(keys, start=2):
        g = groups[k]
        r0 = g[0]
        views = sorted({r["view"] for r in g})
        vals = {
            "row_key": k, "site": r0["site"], "views": ",".join(views), "reach_path": r0["reach_path"],
            "output_sequence": r0["output_sequence"], "action_name": (r0["action_name"] or "")[:300],
            "action_label_t1": r0.get("action_label") or "", "focus_premise": r0.get("focus_premise") or "",
            "evidence_ref_t1": r0.get("evidence_ref") or "",
            "snapshot": f'snapshots/{r0["site"]}/{views[0]}.png',
            "action_label": r0.get("action_label") or "",
        }
        if k in prev and touched(prev[k]):
            # 이전 시트 입력이 있으면 그대로 이어받는다 (사람이 고친 값이 자동 라벨보다 우선)
            n_carried += 1
            for name, *_ in INPUTS:
                if name in ("C_auto", "danger_auto"):
                    continue
                if prev[k].get(name) not in (None, ""):
                    vals[name] = prev[k][name]
        elif not args.no_prelabel:
            pl = prelabel(r0)
            if pl:
                n_pre += 1
                rules[pl["notes"].split("]")[0].lstrip("[")] += 1
                vals.update(pl)
        for name, *_ in COLS:
            if name in vals:
                ws.cell(row=ri, column=IDX[name], value=vals[name])
        ws.cell(row=ri, column=IDX["C_auto"], value=_c_formula(ri))
        ws.cell(row=ri, column=IDX["danger_auto"], value=_d_formula(ri))
        for name, *_ in PREFILLED:
            ws.cell(row=ri, column=IDX[name]).fill = grey
        for name in ("C_auto", "danger_auto"):
            ws.cell(row=ri, column=IDX[name]).fill = auto

    last = max(2, len(keys) + 1)
    for name, _desc, allowed, _w in INPUTS:
        if not allowed:
            continue
        col = get_column_letter(IDX[name])
        dv = DataValidation(type="list", formula1='"' + ",".join(allowed) + '"', allow_blank=True,
                            showErrorMessage=True, errorTitle="허용값 아님",
                            error="목록의 값 또는 빈칸만 입력하세요")
        dv.add(f"{col}2:{col}{last}")
        ws.add_data_validation(dv)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS))}{last}"

    g = wb.create_sheet("guide")
    guide = [
        ["입력 방법"],
        ["1. 노란 열만 입력한다. 회색 열은 Tier 1 수집값, 파란 열(C_auto, danger_auto)은 자동 계산이다."],
        ["2. P1~P4, S, R1~R3, E 는 Y(근거 있음: 참) / N(근거 있음: 거짓) / 빈칸(근거 부족)."],
        ["   확인하지 못한 조건은 N 이 아니라 빈칸으로 둔다 (정의서 Appendix B.1: ¬K(φ) ⇏ K(¬φ))."],
        ["3. 결과 등급 C 는 P/S/R/E 입력으로 자동 계산된다. 직접 C 를 적지 않는다."],
        ["   C3 = P 중 하나라도 Y / C2 = P 모두 N, S=Y, R 모두 Y / C1 = P 모두 N, S=N, E=Y / C0 = P 모두 N, S=N, E=N / 그 외 unknown"],
        ["4. 확인 등급 V 는 직접 고른다. 실행 취소 토스트는 V0, 확인 모달 1회는 V1, 재인증·2FA 는 V2."],
        ["   확인 절차를 관측하지 못했으면 unknown (정적 신호가 없다고 V0 로 두지 않는다)."],
        ["5. danger_auto: excluded = 도달 불가·C0~C2·V2 중 하나 확인 / confirmed = reachable·C3·V0~V1 + final_review=Y"],
        ["   candidate = 세 조건 충족이나 최종 검토 전, 또는 불충족 없이 C3 근거 있음 / unresolved = 그 외"],
        ["6. 근거가 Tier 1 정적 신호뿐이면 confidence 는 low. 실제 동작은 Tier 2(본인 계정·샌드박스)에서만 확인한다."],
        ["7. 이 시트는 사이트별 키–액션 매핑이므로 공개물·저장소에 올리지 않는다."],
        [],
        ["행 선택 기준: 정상(ok) 화면의 enter-submit·accesskey 전부, action_label 이 붙은 focused-control·hotkey,"],
        ["수동 검토 표시(needs_manual_review)된 레코드. 같은 사이트·action_id·경로·출력 키는 화면이 달라도 한 행으로 묶었다."],
    ]
    for row in guide:
        g.append(row)
    g.column_dimensions["A"].width = 140

    out = args.out or (data / "labels" / "label_sheet.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.force:
        raise SystemExit(f"[labelsheet] {out} 가 이미 있음. 입력값을 잃지 않도록 --prev {out} --out 새파일 로 "
                         f"이어받거나 --force 로 덮어쓴다.")
    wb.save(out)
    c = Counter(groups[k][0]["reach_path"] for k in keys)
    print(f"[labelsheet] {len(keys)} rows ({dict(c)}), carried over {n_carried}, "
          f"auto C1 prelabel {n_pre} {dict(rules)} -> {out}")


# ------------------------------------------------------------------ apply
def _clean(v):
    return "" if v is None else str(v).strip()


def touched(row: dict) -> bool:
    """라벨러가 판정 입력을 한 행인가 (action_label 은 1차 추정으로 미리 채워져 있으므로 제외)."""
    return any(_clean(row.get(n)) for n, *_ in INPUTS if n not in ("C_auto", "danger_auto", "action_label"))


def evaluate(row: dict) -> dict:
    c = consequence_tier(row)
    v = _clean(row.get("confirmation_tier")) or "unknown"
    reach = _clean(row.get("reachability")) or "unknown"
    ds = danger_status(reach, c, v, _clean(row.get("final_review")))
    return {"consequence_tier": c, "confirmation_tier": v, "reachability": reach,
            "danger_status": ds, "is_danger_cell": is_danger_cell(ds)}


def cmd_apply(args):
    data = args.data
    sheet = read_sheet(args.sheet)
    by_key = {}
    for row in sheet:
        ev = evaluate(row)
        upd = dict(ev)
        lab = _clean(row.get("action_label"))
        if lab:
            upd["action_label"] = None if lab == "none" else lab
        if _clean(row.get("physical_trigger_key")):
            upd["physical_trigger_key"] = _clean(row["physical_trigger_key"])
        for f in ("evidence_source", "evidence_ref", "confidence", "protection_note"):
            if _clean(row.get(f)):
                upd[f] = _clean(row[f])
        upd["tier2_verified"] = _v(row.get("tier2_verified")) == "Y"
        upd["_label"] = {k: _clean(row.get(k)) for k in
                         ("P1", "P2", "P3", "P4", "S", "R1", "R2", "R3", "E", "confirm_steps", "strong_auth",
                          "final_review", "reviewer", "notes")}
        upd["_touched"] = touched(row)
        by_key[str(row["row_key"])] = upd

    recs = load_records(data)
    n_upd = 0
    for r in recs:
        u = by_key.get(row_key(r))
        if not u or not u["_touched"]:
            continue
        n_upd += 1
        for k, val in u.items():
            if k.startswith("_"):
                continue
            r[k] = val
        r.setdefault("aux", {})["manual_label"] = u["_label"]

    out_dir = data / "labels"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "records_labeled.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    fields = [k for k in recs[0].keys() if k != "aux"] + ["aux"] if recs else []
    with (out_dir / "records_labeled.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in recs:
            w.writerow({**r, "aux": json.dumps(r.get("aux", {}), ensure_ascii=False)})

    write_summary(data, sheet, by_key, out_dir)
    print(f"[labelsheet] sheet rows={len(sheet)} labeled={sum(u['_touched'] for u in by_key.values())} "
          f"records updated={n_upd} -> {out_dir}")


def write_summary(data: Path, sheet: list[dict], by_key: dict, out_dir: Path):
    rows = [(str(r["row_key"]), r) for r in sheet]
    labeled = [(k, r) for k, r in rows if by_key[k]["_touched"]]
    C = ["C3", "C2", "C1", "C0", "unknown"]
    V = ["V0", "V1", "V2", "unknown"]

    def source(k, r):
        if not by_key[k]["_touched"]:
            return "미입력"
        rv = _clean(r.get("reviewer"))
        return "자동" if rv == PRELABEL_REVIEWER else "초안" if rv == DRAFT_REVIEWER else "사람"

    # 표는 시트 전체 행을 센다. 미입력 행은 판정 근거가 없으므로 C=unknown, V=unknown, unresolved 로 집계된다.
    xt = Counter((by_key[k]["consequence_tier"], by_key[k]["confirmation_tier"]) for k, _ in rows)
    c_src = Counter((by_key[k]["consequence_tier"], source(k, r)) for k, r in rows)
    ds = Counter(by_key[k]["danger_status"] for k, _ in rows)
    ds_src = Counter((by_key[k]["danger_status"], source(k, r)) for k, r in rows)
    site_ds = defaultdict(Counter)
    for k, r in rows:
        site_ds[r["site"]][by_key[k]["danger_status"]] += 1

    st = ok_views(data)
    sites = sorted({s for s, _ in st})
    incomplete = {s for (s, v), x in st.items() if x != "ok"}
    lines = [
        "# ③ 수동 라벨 요약 (내부 자료 — 사이트별 수치 포함, 공개 금지)", "",
        f"- 시트 행(후보 액션): {len(rows)} / 라벨 입력된 행: {len(labeled)} / 미입력: {len(rows) - len(labeled)}",
        f"- 라벨 출처: 자동 사전 라벨(reviewer={PRELABEL_REVIEWER}) "
        f"{sum(1 for k, r in rows if source(k, r) == '자동')} / 초안(reviewer={DRAFT_REVIEWER}) "
        f"{sum(1 for k, r in rows if source(k, r) == '초안')} / 사람 입력 "
        f"{sum(1 for k, r in rows if source(k, r) == '사람')}"
        " — 자동·초안 라벨은 검토 후 reviewer 를 사람 이름으로 바꾼다",
        "- 집계 단위: 시트 행 = 사이트·action_id·도달 경로·출력 키 조합 (정의서 5장 레코드 단위)",
        "- C 와 V 는 독립된 두 축이다. 각 축의 unknown 은 그 축의 판정 근거가 없다는 뜻이다 "
        "(C: 실제 효과 미확인, V: 확인 절차 미관측 — Tier 1 은 동작을 실행하지 않으므로 V 는 원칙적으로 unknown).", "",
        f"## 결과 등급 × 확인 등급 (시트 전체 {len(rows)}행, 미입력 행 포함)", "",
        "| C \\ V | " + " | ".join(V) + " | 계 |", "|" + "---|" * (len(V) + 2),
    ]
    for c in C:
        cnt = [xt.get((c, v), 0) for v in V]
        lines.append(f"| {c} | " + " | ".join(map(str, cnt)) + f" | {sum(cnt)} |")
    srcs = ["자동", "초안", "사람", "미입력"]
    lines += ["", "## 결과 등급 × 판정 출처", "",
              "| C | 자동 사전 라벨 | 초안 | 사람 입력 | 미입력 | 계 |", "|---|---|---|---|---|---|"]
    for c in C:
        cnt = [c_src.get((c, s), 0) for s in srcs]
        lines.append(f"| {c} | " + " | ".join(map(str, cnt)) + f" | {sum(cnt)} |")
    lines += ["", "## 위험 셀 판정 상태 (시트 전체)", "",
              "| danger_status | 자동 | 초안 | 사람 | 미입력 | 계 |", "|---|---|---|---|---|---|"]
    for k in ("confirmed", "candidate", "excluded", "unresolved"):
        cnt = [ds_src.get((k, s), 0) for s in srcs]
        lines.append(f"| {k} | " + " | ".join(map(str, cnt)) + f" | {ds.get(k, 0)} |")
    lines += ["", "## 사이트별 (분모 포함)", "",
              "| site | confirmed | candidate | excluded | unresolved | 미입력 행 | 미수집 화면 있음 | 판단 |",
              "|---|---|---|---|---|---|---|---|"]
    unl = Counter(r["site"] for k, r in rows if not by_key[k]["_touched"])
    for s in sites:
        d = site_ds[s]
        if d["confirmed"]:
            verdict = "위험 셀 보유"
        elif s in incomplete or d["candidate"] or d["unresolved"] or unl[s]:
            verdict = "판단 불가 (미수집·후보·미확정·미입력 남음)"
        else:
            verdict = "관측 범위 내 위험 셀 없음"
        lines.append(f"| {s} | {d['confirmed']} | {d['candidate']} | {d['excluded']} | {d['unresolved']} | "
                     f"{unl[s]} | {'Y' if s in incomplete else 'N'} | {verdict} |")
    n_have = sum(1 for s in sites if site_ds[s]["confirmed"])
    lines += ["", f"- 대상 사이트 {len(sites)} 중 위험 셀 확정 보유 {n_have}. "
              "'위험 셀 없음'은 관측 범위가 모두 수집되고 그 범위 항목이 모두 excluded 일 때만 쓴다 (정의서 5장).", ""]
    (out_dir / "labels_summary.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ agree
def kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def cmd_agree(args):
    A = {str(r["row_key"]): r for r in read_sheet(args.a)}
    B = {str(r["row_key"]): r for r in read_sheet(args.b)}
    both = [k for k in A if k in B and touched(A[k]) and touched(B[k])]
    print(f"[agree] 두 라벨러가 모두 입력한 행: {len(both)}")
    for name, fn in [("action_label", lambda r: _clean(r.get("action_label")) or "none"),
                     ("consequence_tier", lambda r: evaluate(r)["consequence_tier"]),
                     ("confirmation_tier", lambda r: evaluate(r)["confirmation_tier"]),
                     ("danger_status", lambda r: evaluate(r)["danger_status"])]:
        pairs = [(fn(A[k]), fn(B[k])) for k in both]
        agree = sum(a == b for a, b in pairs)
        kp = kappa(pairs)
        print(f"  {name:18s} 일치 {agree}/{len(pairs)}  kappa={'-' if kp is None else f'{kp:.3f}'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="③ 수동 라벨 시트 (C/V 등급)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--data", default=Path("data"), type=Path)
    e.add_argument("--out", type=Path)
    e.add_argument("--prev", type=Path, help="이전 시트의 입력값을 row_key 기준으로 이어받음")
    e.add_argument("--force", action="store_true")
    e.add_argument("--no-prelabel", action="store_true", help="뻔한 C1 자동 사전 라벨을 채우지 않음")
    a = sub.add_parser("apply")
    a.add_argument("--data", default=Path("data"), type=Path)
    a.add_argument("--sheet", required=True, type=Path)
    g = sub.add_parser("agree")
    g.add_argument("--a", required=True, type=Path)
    g.add_argument("--b", required=True, type=Path)
    args = ap.parse_args(argv)
    {"export": cmd_export, "apply": cmd_apply, "agree": cmd_agree}[args.cmd](args)


if __name__ == "__main__":
    main()
