"""km/bundle_scan.py — JS 번들에서 단축키 라이브러리 시그니처와 키 비교 패턴을 탐지한다 (static-bundle).

라이브러리 존재 = 핫키 존재 가능성의 신호. 특정 키→액션 매핑을 뜻하지 않는다.
시그니처는 '강(strong)'과 '약(weak)'으로 구분하며, 약 시그니처만 일치하면 confidence=low로 처리한다.
"""
from __future__ import annotations

import re

SIGNATURES: dict[str, dict[str, list[str]]] = {
    "mousetrap": {"strong": [r"\bMousetrap\b", r"_directMap"], "weak": [r"stopCallback"]},
    "hotkeys-js": {"strong": [r"hotkeys-js", r"getPressedKeyCodes", r"\bhotkeys\.setScope\b"],
                   "weak": [r"\bhotkeys\.unbind\b", r"\bhotkeys\.filter\b"]},
    "tinykeys": {"strong": [r"\btinykeys\b", r"createKeybindingsHandler"], "weak": []},
    "github-hotkey": {"strong": [r"@github/hotkey", r"\binstallHotkey\b"], "weak": [r"data-hotkey"]},
    "keymaster": {"strong": [r"\bkeymaster\b"], "weak": [r"\bkey\.setScope\b"]},
    "react-hotkeys-hook": {"strong": [r"react-hotkeys-hook", r"\buseHotkeys\b"], "weak": []},
    "react-hotkeys": {"strong": [r"\bGlobalHotKeys\b", r"react-hotkeys(?!-hook)"], "weak": [r"\bHotKeys\b"]},
    "combokeys": {"strong": [r"\bCombokeys\b"], "weak": []},
    "jquery-hotkeys": {"strong": [r"jquery\.hotkeys", r"hotkeys\.specialKeys"], "weak": []},
    "keyboardjs": {"strong": [r"\bkeyboardJS\b", r"keyboardjs"], "weak": []},
    "hotkeys-generic": {"strong": [], "weak": [r"registerShortcut", r"shortcutKey", r"keyboardShortcut", r"KeyboardShortcuts?"]},
}

_COMPILED = {lib: {lvl: [re.compile(p) for p in pats] for lvl, pats in d.items()} for lib, d in SIGNATURES.items()}

GENERIC = {
    "key_compare": re.compile(r"""\.key\s*===?\s*["'](Enter|Escape|Tab|ArrowDown|Home|End|[A-Za-z/?])["']"""),
    "keycode_compare": re.compile(r"""\.(keyCode|which)\s*===?\s*(13|27|9|40|36|35)\b"""),
    "add_key_listener": re.compile(r"""addEventListener\(\s*["'](keydown|keyup|keypress)["']"""),
}


def scan(text: str) -> dict:
    libs = {}
    for lib, lv in _COMPILED.items():
        strong = sorted({p.pattern for p in lv["strong"] if p.search(text)})
        weak = sorted({p.pattern for p in lv["weak"] if p.search(text)})
        if strong or weak:
            libs[lib] = {"strong": strong, "weak": weak, "level": "strong" if strong else "weak"}
    generic = {name: len(rx.findall(text)) for name, rx in GENERIC.items()}
    return {"libraries": libs, "generic_counts": generic, "size": len(text)}
