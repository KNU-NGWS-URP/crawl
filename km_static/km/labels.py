"""km/labels.py — 정의서 V1 Appendix E 기반 action_label 1차 추정.

- 키워드 매칭은 1차 추정일 뿐이며 결과 등급·확인 등급을 확정하지 않는다 (Appendix E.2).
- 'post', 'submit'처럼 텍스트만으로 유형 구분이 어려운 표현은 자동 분류를 보류(None)하고
  needs_manual_review=True로 표시한다.
- 여러 분류가 일치하면 대표 라벨 우선순위를 적용한다:
  financial ≻ destructive ≻ transmission ≻ publication ≻ execution ≻ auth_change
"""
from __future__ import annotations

import re

PRIORITY = ["financial", "destructive", "transmission", "publication", "execution", "auth_change"]

# Appendix E.1 매핑 키워드 예시 + 커머스 파일럿용 최소 보강(보강 항목은 주석으로 구분)
KEYWORDS: dict[str, list[str]] = {
    "financial": ["buy", "pay", "purchase", "checkout", "order", "결제", "구매", "주문",
                  # 보강: 커머스 화면의 흔한 변형
                  "place order", "buy now", "바로구매", "바로 구매", "구매하기", "결제하기", "주문하기"],
    "destructive": ["delete", "remove", "clear", "destroy", "삭제", "제거"],
    "transmission": ["send", "transfer", "forward", "전송", "보내기", "이체"],
    "publication": ["publish", "share", "broadcast", "게시", "공유", "발행"],
    "execution": ["run", "deploy", "execute", "confirm", "merge", "실행", "배포", "승인"],
    "auth_change": ["login", "log in", "sign in", "sign up", "logout", "log out", "로그인", "가입", "로그아웃"],
}
# 자동 분류 보류 대상 (Appendix E.2)
AMBIGUOUS = ["post", "submit"]


def _compile(words: list[str]) -> re.Pattern:
    parts = []
    for w in words:
        if re.search(r"[A-Za-z]", w):
            parts.append(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])")
        else:  # 한글: 단어 경계 개념이 약하므로 부분 일치
            parts.append(re.escape(w))
    return re.compile("|".join(parts), re.IGNORECASE)


_RX = {k: _compile(v) for k, v in KEYWORDS.items()}
_AMBIG = _compile(AMBIGUOUS)

# collect_dom.js의 focused-control 후보 필터로 전달할 JS 정규식 (모든 키워드 합집합)
ALL_KEYWORDS_JS = "|".join(
    re.escape(w).replace("\\ ", " ") for ws in KEYWORDS.values() for w in ws
)


def classify(*texts: str | None) -> dict:
    """여러 텍스트(보이는 텍스트, aria-label, title 등)를 합쳐 1차 라벨을 추정한다."""
    blob = " ".join(t for t in texts if t)
    matched = [k for k in PRIORITY if _RX[k].search(blob)]
    ambiguous = bool(_AMBIG.search(blob))
    if matched:
        return {"action_label": matched[0], "matched_labels": matched,
                "needs_manual_review": len(matched) > 1 or ambiguous}
    if ambiguous:
        return {"action_label": None, "matched_labels": [], "needs_manual_review": True}
    return {"action_label": None, "matched_labels": [], "needs_manual_review": False}
