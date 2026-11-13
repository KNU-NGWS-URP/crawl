"""tests/test_fixture.py — 로컬 픽스처(정답지 있음)로 수집기 전 경로를 검증한다.

실행:  python -m tests.test_fixture
- tests/fixture 를 127.0.0.1:8765 로 서빙하고 collect → normalize 를 돌린 뒤 기대값과 비교한다.
- 외부 사이트에 접속하지 않는다.
"""
from __future__ import annotations

import functools
import http.server
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from km import collect, normalize  # noqa: E402

# (reach_path, evidence_source, output_sequence 포함 문자열, action_name 포함 문자열)
EXPECT = [
    ("accesskey", "static-accesskey", "+c", "장바구니"),
    ("accesskey", "static-accesskey", "+b", "바로구매"),
    ("accesskey", "static-accesskey", "+s", "공유하기"),          # open shadow DOM
    ("enter-submit", "static-form", "Enter", "검색"),              # type 미지정 button → default_button
    ("enter-submit", "static-form", "Enter", "coupon/apply"),      # 버튼 없음 + 필드 1개
    ("enter-submit", "static-form", "Enter", "바로구매"),
    ("enter-submit", "static-form", "Enter", "결제하기"),          # iframe
    ("hotkey", "static-handler", "Escape", "on window"),           # addEventListener + key 비교
    ("hotkey", "static-handler", "Home", "keyup listener on document"),  # on-property + switch
    ("hotkey", "static-handler", "End", "keydown listener on document"), # 지연 등록
    ("hotkey", "static-handler", "Escape", "onkeydown attribute"),  # 인라인 속성
    ("hotkey", "static-handler", "ArrowDown", "react onKeyDown"),   # React props
    ("hotkey", "static-handler", "Delete", "jquery keydown"),       # jQuery 위임
    ("hotkey", "static-dom", "Shift+D", "위시리스트"),              # data-hotkey
    ("focused-control", "static-dom", "Enter", "장바구니 삭제"),
]
NOT_EXPECT = [
    ("enter-submit", "/login"),   # 필드 2개 + 버튼 없음 → 암묵적 제출 없음
    ("enter-submit", "/x"),       # 기본 버튼 disabled
    ("static-handler", "button#del"),  # 제거된 리스너
]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="km_fixture_"))
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):  # 요청 로그 출력 억제
            pass

    handler = functools.partial(QuietHandler, directory=str(ROOT / "tests" / "fixture"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        collect.main(["--corpus", str(ROOT / "config" / "fixture.csv"), "--out", str(tmp), "--headless",
                      "--views", "home", "--settle", "2", "--idle-timeout", "5", "--delay", "0"])
        normalize.main(["--data", str(tmp)])
        recs = [json.loads(l) for l in (tmp / "records" / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        fails = 0
        total = 0
        for rp, ev, seq, name in EXPECT:
            ok = any(r["reach_path"] == rp and r["evidence_source"] == ev and seq in (r["output_sequence"] or "")
                     and name in (r["action_name"] or "") for r in recs)
            print(f"[{'PASS' if ok else 'FAIL'}] expect {rp}/{ev} {seq!r} {name!r}")
            fails += not ok
            total += 1
        for a, b in NOT_EXPECT:
            bad = [r for r in recs if (r["reach_path"] == a or r["evidence_source"] == a)
                   and (b in (r["action_name"] or "") or b in json.dumps(r["aux"], ensure_ascii=False))]
            print(f"[{'PASS' if not bad else 'FAIL'}] not-expect {a} {b!r}")
            fails += bool(bad)
            total += 1
        # 스키마 기본값 확인
        ok = all(r["reachability"] == "unknown" and r["danger_status"] == "unresolved" and r["is_danger_cell"] is None
                 and r["physical_trigger_key"] is None and r["tier2_verified"] is False for r in recs)
        print(f"[{'PASS' if ok else 'FAIL'}] Tier 1 schema defaults")
        fails += not ok
        total += 1
        sig = (tmp / "records" / "site_signals.csv").read_text(encoding="utf-8-sig")
        ok = "library:mousetrap" in sig and "framework:react" in sig and "framework:jquery" in sig
        print(f"[{'PASS' if ok else 'FAIL'}] bundle/framework signals")
        fails += not ok
        total += 1
        print(f"\n{total - fails}/{total} checks passed (로컬 테스트 페이지 1개 기준, 실제 사이트 아님)")
        return 1 if fails else 0
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
