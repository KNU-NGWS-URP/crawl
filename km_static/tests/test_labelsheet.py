"""tests/test_labelsheet.py — 정의서 V2 판정 규칙(C 등급·위험 셀 상태)이 의도대로 계산되는지 검사한다.

실행:  python -m tests.test_labelsheet   (외부 접속 없음)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from km.labelsheet import consequence_tier, danger_status, is_danger_cell  # noqa: E402

N4 = {"P1": "N", "P2": "N", "P3": "N", "P4": "N"}
R_Y = {"R1": "Y", "R2": "Y", "R3": "Y"}

# (설명, 입력, 기대 C)
C_CASES = [
    ("1-click 구매: P1 근거", {"P1": "Y"}, "C3"),
    ("메일 전송: P2 근거 (다른 칸 빈칸이어도 C3)", {"P2": "Y", "S": "Y"}, "C3"),
    ("장바구니 담기: 변경 + 복원 가능", {**N4, "S": "Y", **R_Y, "E": "Y"}, "C2"),
    ("페이지 이동: 지속 변경 없음 + 효과 있음", {**N4, "S": "N", "E": "Y"}, "C1"),
    ("효과 없는 키 입력", {**N4, "S": "N", "E": "N"}, "C0"),
    ("빈 행 → unknown (빈칸을 N 으로 보지 않음)", {}, "unknown"),
    ("'삭제' 버튼만 발견: P 일부만 확인", {"P1": "N", "P2": "N", "S": "Y"}, "unknown"),
    ("데이터 변경 확인, 복원 여부 미확인", {**N4, "S": "Y"}, "unknown"),
    ("C2 재검토: 복원 불가(R=N)만으로 C3 로 올리지 않음", {**N4, "S": "Y", "R1": "N", "R2": "Y", "R3": "Y"},
     "unknown"),
    ("P 모두 N, S=N, E 미확인", {**N4, "S": "N"}, "unknown"),
    ("소문자 입력 허용", {"p1": "y", "P1": "y"}, "C3"),
]

# (설명, reach, C, V, final_review, 기대 status)
D_CASES = [
    ("세 조건 충족 + 최종 검토", "reachable", "C3", "V0", "Y", "confirmed"),
    ("V1 도 위험 셀", "reachable", "C3", "V1", "Y", "confirmed"),
    ("세 조건 충족, 검토 전", "reachable", "C3", "V0", "", "candidate"),
    ("C3 근거만 있고 도달성·확인 미확인", "unknown", "C3", "unknown", "", "candidate"),
    ("V2 → 제외", "reachable", "C3", "V2", "Y", "excluded"),
    ("C2 → 제외", "reachable", "C2", "V0", "Y", "excluded"),
    ("도달 불가 → 제외", "unreachable", "C3", "V0", "Y", "excluded"),
    ("전부 미확인 → 미확정 (자료 부족만으로 제외하지 않음)", "unknown", "unknown", "unknown", "", "unresolved"),
    ("도달 가능·V0 지만 C 미확인 → 미확정", "reachable", "unknown", "V0", "Y", "unresolved"),
]


def main():
    fails = 0
    for desc, row, exp in C_CASES:
        got = consequence_tier(row)
        ok = got == exp
        fails += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] C  {desc}: {got} (expect {exp})")
    for desc, reach, c, v, rev, exp in D_CASES:
        got = danger_status(reach, c, v, rev)
        ok = got == exp
        fails += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] D  {desc}: {got} (expect {exp})")
    for st, exp in (("confirmed", True), ("excluded", False), ("candidate", None), ("unresolved", None)):
        ok = is_danger_cell(st) is exp
        fails += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] is_danger_cell({st}) = {is_danger_cell(st)}")
    total = len(C_CASES) + len(D_CASES) + 4
    print(f"\n{total - fails}/{total} checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
