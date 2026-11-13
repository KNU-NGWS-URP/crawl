"""km/keyparse.py — 리스너 소스에서 비교 대상 키 상수를 추출한다 (static-handler 보조 근거).

한계(보고서에 명시할 것):
- 리스너가 다른 함수로 위임하면(난독화·번들링) 키 비교가 소스 일부에 나타나지 않아 누락된다.
- 문자열 'Enter'가 있다고 해서 해당 키로 액션이 실행된다는 뜻은 아니다 (원칙 8).
"""
from __future__ import annotations

import re

KEYCODE_NAMES = {
    8: "Backspace", 9: "Tab", 13: "Enter", 27: "Escape", 32: "Space", 33: "PageUp", 34: "PageDown",
    35: "End", 36: "Home", 37: "ArrowLeft", 38: "ArrowUp", 39: "ArrowRight", 40: "ArrowDown",
    46: "Delete", 191: "/", 186: ";", 187: "=", 188: ",", 189: "-", 190: ".", 219: "[", 221: "]",
}
KEYCODE_NAMES.update({c: chr(c) for c in range(65, 91)})          # A-Z
KEYCODE_NAMES.update({c: str(c - 48) for c in range(48, 58)})     # 0-9
KEYCODE_NAMES.update({111 + i: f"F{i}" for i in range(1, 13)})    # F1-F12

KEY_NAMES = {"Enter", "Escape", "Esc", "Tab", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Down", "Up",
             "Left", "Right", "Home", "End", "PageUp", "PageDown", "Backspace", "Delete", "Del", " ", "Spacebar"}
NORMALIZE = {"Esc": "Escape", "Down": "ArrowDown", "Up": "ArrowUp", "Left": "ArrowLeft", "Right": "ArrowRight",
             "Del": "Delete", " ": "Space", "Spacebar": "Space"}

# 정의서 1장: 입력 물리 키 후보 (참고용 표시. 도달성은 출력 쪽 기준)
K_IN = {"Escape", "Tab", "ArrowDown", "Home", "End"}

_RX_KEY_CMP = re.compile(r"""\.(key|code)\s*[!=]==?\s*(["'])([^"'\\]{1,20})\2""")
_RX_KEY_CMP_REV = re.compile(r"""(["'])([^"'\\]{1,20})\1\s*[!=]==?\s*[\w$.]*\.(key|code)\b""")
_RX_KEYCODE = re.compile(r"""\.(keyCode|which|charCode)\s*[!=]==?\s*(\d{1,3})\b""")
_RX_KEYCODE_REV = re.compile(r"""\b(\d{1,3})\s*[!=]==?\s*[\w$.]*\.(keyCode|which)\b""")
_RX_CASE = re.compile(r"""case\s*(["'])([^"'\\]{1,20})\1\s*:""")
_RX_CASE_NUM = re.compile(r"""case\s*(\d{1,3})\s*:""")
_RX_MODS = re.compile(r"""\.(ctrlKey|metaKey|altKey|shiftKey)\b""")
_RX_SWITCH_KEY = re.compile(r"""switch\s*\(\s*[\w$.]*\.(key|keyCode|which|code)\s*\)""")
_RX_PREVENT = re.compile(r"""preventDefault\s*\(""")
_RX_SUBMIT_CALL = re.compile(r"""\.(submit|requestSubmit|click)\s*\(\s*\)""")


def _norm_key(k: str) -> str | None:
    if k in NORMALIZE:
        return NORMALIZE[k]
    if k in KEY_NAMES:
        return k
    if k.startswith("Key") and len(k) == 4:      # event.code 'KeyS'
        return k[3:].upper()
    if k.startswith("Digit") and len(k) == 6:
        return k[5:]
    if re.fullmatch(r"F([1-9]|1[0-2])", k):
        return k
    if len(k) == 1:                              # 'a', '/', '?'
        return k.upper() if k.isalpha() else k
    return None


def extract_keys(src: str | None) -> dict:
    """리스너 소스 문자열에서 키 비교 상수·수식어·관련 호출을 추출한다."""
    if not src:
        return {"keys": [], "modifiers": [], "has_switch_on_key": False, "prevent_default": False,
                "calls_submit_like": False, "source_available": False}
    keys: set[str] = set()
    for m in _RX_KEY_CMP.finditer(src):
        k = _norm_key(m.group(3))
        if k:
            keys.add(k)
    for m in _RX_KEY_CMP_REV.finditer(src):
        k = _norm_key(m.group(2))
        if k:
            keys.add(k)
    for rx, gi in ((_RX_KEYCODE, 2), (_RX_KEYCODE_REV, 1)):
        for m in rx.finditer(src):
            n = int(m.group(gi))
            if n in KEYCODE_NAMES:
                keys.add(KEYCODE_NAMES[n])
    has_switch = bool(_RX_SWITCH_KEY.search(src))
    if has_switch:  # switch(e.key) 문맥에서만 case 라벨을 키로 인정 (오탐 억제)
        for m in _RX_CASE.finditer(src):
            k = _norm_key(m.group(2))
            if k:
                keys.add(k)
        for m in _RX_CASE_NUM.finditer(src):
            n = int(m.group(1))
            if n in KEYCODE_NAMES:
                keys.add(KEYCODE_NAMES[n])
    mods = sorted({m.group(1) for m in _RX_MODS.finditer(src)})
    return {
        "keys": sorted(keys),
        "modifiers": mods,
        "has_switch_on_key": has_switch,
        "prevent_default": bool(_RX_PREVENT.search(src)),
        "calls_submit_like": bool(_RX_SUBMIT_CALL.search(src)),
        "source_available": True,
    }
