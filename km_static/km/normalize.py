"""km/normalize.py — raw 수집 결과를 정의서 V1 Appendix D 라벨 스키마 레코드로 변환한다.

실행:
    python -m km.normalize --data data

산출:
    data/records/records.jsonl   Appendix D 필드 + aux(분석 보조 필드)
    data/records/records.csv     위와 동일 (aux는 JSON 문자열)
    data/records/site_signals.csv  사이트 단위 신호 (번들 라이브러리 시그니처 등, 액션 레코드 아님)

Tier 1 기본값 (정적 근거만 있으므로 확정하지 않음):
    physical_trigger_key=null, reachability=unknown, consequence_tier=unknown,
    confirmation_tier=unknown, danger_status=unresolved, is_danger_cell=null, tier2_verified=false
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from .keyparse import K_IN, extract_keys
from .labels import classify

SCHEMA_FIELDS = [
    "site", "category", "action_id", "action_name", "action_label", "physical_trigger_key", "output_sequence",
    "reach_path", "reachability", "consequence_tier", "confirmation_tier", "danger_status", "is_danger_cell",
    "focus_premise", "evidence_source", "evidence_ref", "confidence", "tier2_verified", "protection_note",
    "snapshot_date",
]
EXTRA_FIELDS = ["record_id", "view", "aux"]


def h(*parts) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def url_path(u: str | None) -> str:
    if not u:
        return ""
    try:
        p = urlparse(u)
        return f"{p.netloc}{p.path}"
    except Exception:
        return u


def elem_signature(d: dict) -> str:
    """뷰가 달라도 같은 요소(예: 공통 헤더 검색창)면 같은 값이 되도록 하는 서명."""
    return "|".join(str(x or "") for x in (
        d.get("tag"), d.get("id"), d.get("name"), d.get("type"), (d.get("text") or "")[:60],
        d.get("aria_label"), url_path(d.get("href")), url_path(d.get("form_action")),
    ))


def accesskey_output(key: str, plat: str) -> str:
    """Chromium 기준 accesskey 조합키. 플랫폼별로 다르며 사이트·브라우저 단축키와 충돌 시 달라질 수 있음."""
    k = (key or "").strip()
    if plat == "Darwin":
        return f"Control+Option+{k}"
    return f"Alt+{k}"  # Windows / Linux Chromium (충돌 시 Alt+Shift+{k} 가능)


def target_desc(t: dict | None) -> str:
    if not t:
        return "unknown"
    if t.get("kind") in ("window", "document"):
        return t["kind"]
    if t.get("kind") == "element":
        s = t.get("tag", "?")
        if t.get("id"):
            s += f"#{t['id']}"
        elif t.get("name"):
            s += f"[name={t['name']}]"
        return s
    return t.get("kind", "unknown")


def is_global_target(t: dict | None) -> bool:
    if not t:
        return False
    return t.get("kind") in ("window", "document") or bool(t.get("is_body_or_html"))


def dispatcher_guess(src: str | None) -> str | None:
    if not src:
        return None
    if "dispatchDiscreteEvent" in src or "dispatchEvent" in src and "__reactContainer" in src:
        return "react-root"
    if ".event.dispatch.apply" in src or "jQuery" in src and "dispatch" in src:
        return "jquery-dispatcher"
    if "_vts" in src or "invoker" in src and "attached" in src:
        return "vue-invoker"
    if "_handleKeyEvent" in src or "Mousetrap" in src:
        return "mousetrap"
    if src.rstrip().endswith("[native code] }") or "[native code]" in src[-40:]:
        return "bound-or-native(source unavailable)"
    return None


class Builder:
    def __init__(self, site, category, view, snapshot_date, plat, browser, raw_ref):
        self.site, self.category, self.view = site, category, view
        self.snapshot_date, self.plat, self.browser, self.raw_ref = snapshot_date, plat, browser, raw_ref
        self.records: list[dict] = []

    def premise(self, frame_is_top: bool, focus: str) -> str:
        return (f"logged-out; view={self.view}; focus={focus}; frame={'top' if frame_is_top else 'iframe'}; "
                f"Chromium {self.browser} on {self.plat}")

    def add(self, *, action_key, action_name, label_texts, output_sequence, reach_path, evidence_source,
            evidence_ptr, confidence, focus, frame_is_top, aux):
        lab = classify(*label_texts)
        action_id = h(self.site, action_key)
        rec = {
            "site": self.site, "category": self.category, "action_id": action_id,
            "action_name": (action_name or "")[:160], "action_label": lab["action_label"],
            "physical_trigger_key": None, "output_sequence": output_sequence, "reach_path": reach_path,
            "reachability": "unknown", "consequence_tier": "unknown", "confirmation_tier": "unknown",
            "danger_status": "unresolved", "is_danger_cell": None,
            "focus_premise": self.premise(frame_is_top, focus), "evidence_source": evidence_source,
            "evidence_ref": f"{self.raw_ref}#{evidence_ptr}", "confidence": confidence, "tier2_verified": False,
            "protection_note": "", "snapshot_date": self.snapshot_date,
            "record_id": h(action_id, self.view, reach_path, output_sequence, evidence_source, evidence_ptr),
            "view": self.view,
            "aux": {**aux, "matched_labels": lab["matched_labels"], "needs_manual_review": lab["needs_manual_review"]},
        }
        self.records.append(rec)
        return rec


def process_view(raw_path: Path, data_dir: Path, category: str) -> tuple[list[dict], dict]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    meta = raw.get("meta", {})
    site, view = meta.get("site"), meta.get("view")
    stats = {"site": site, "view": view, "status": meta.get("status"), "forms_total": 0, "forms_enter_candidate": 0,
             "wrapped_key_listeners": 0, "removed_key_listeners": 0, "cdp_key_listeners": None, "submit_listeners": 0}
    if meta.get("status") not in ("ok", "blocked_suspected"):
        return [], stats
    snapshot_date = (meta.get("started") or "")[:10]
    plat = meta.get("platform") or "unknown"
    b = Builder(site, category, view, snapshot_date, plat, meta.get("browser_version"),
                str(raw_path.relative_to(data_dir)).replace("\\", "/"))
    blocked_note = meta.get("status") == "blocked_suspected"

    for fi, fr in enumerate(raw.get("frames", [])):
        top = bool(fr.get("is_top"))
        dom = fr.get("dom") or {}

        # --- A. accesskey
        for i, a in enumerate(dom.get("accesskeys", [])):
            name = a.get("text") or a.get("aria_label") or a.get("title") or a.get("tag")
            conf = "medium" if a.get("visible") and not a.get("disabled") else "low"
            b.add(action_key=("el", elem_signature(a)), action_name=name,
                  label_texts=[a.get("text"), a.get("aria_label"), a.get("title"), url_path(a.get("href"))],
                  output_sequence=accesskey_output(a.get("accesskey"), plat), reach_path="accesskey",
                  evidence_source="static-accesskey", evidence_ptr=f"frames[{fi}].dom.accesskeys[{i}]",
                  confidence=conf, focus="document (accesskey is page-global)", frame_is_top=top,
                  aux={"accesskey": a.get("accesskey"), "activation": a.get("activation_semantics"),
                       "tag": a.get("tag"), "visible": a.get("visible"), "disabled": a.get("disabled"),
                       "css_path": a.get("css_path"), "blocked_suspected": blocked_note})

        # --- B. Enter 암묵적 제출 폼 (submit 리스너 정보는 C 단계 후 병합)
        handlers = fr.get("handlers") or {}
        targets = handlers.get("targets") or {}
        submit_paths = set()
        for r in handlers.get("records", []):
            if r.get("type") in ("submit", "click") and not r.get("removed"):
                t = targets.get(str(r.get("target_id"))) or {}
                if t.get("css_path"):
                    submit_paths.add(t["css_path"])
        for i, f in enumerate(dom.get("forms", [])):
            stats["forms_total"] += 1
            if f.get("implicit_submission") not in ("default_button", "no_button_single_field"):
                continue
            stats["forms_enter_candidate"] += 1
            db = f.get("default_button") or {}
            fields = f.get("blocking_fields") or []
            field0 = fields[0] if fields else {}
            name = db.get("text") or db.get("aria_label") or (f"form → {url_path(f.get('action'))}")
            conf = "medium" if f.get("n_visible_blocking_fields", 0) > 0 else "low"
            js_submit = bool(f.get("has_onsubmit_attr")) or f.get("css_path") in submit_paths or \
                (db.get("css_path") in submit_paths if db else False)
            b.add(action_key=("form", f.get("method"), url_path(f.get("action")), f.get("id"), f.get("name"),
                              db.get("text") if db else None),
                  action_name=name,
                  label_texts=[db.get("text"), db.get("aria_label"), db.get("title"), url_path(f.get("action"))],
                  output_sequence="Enter", reach_path="enter-submit", evidence_source="static-form",
                  evidence_ptr=f"frames[{fi}].dom.forms[{i}]", confidence=conf,
                  focus=f"text field '{field0.get('name') or field0.get('id') or field0.get('type') or '?'}' in form {f.get('css_path')}",
                  frame_is_top=top,
                  aux={"implicit_submission": f.get("implicit_submission"), "method": f.get("method"),
                       "action": f.get("action"), "n_visible_blocking_fields": f.get("n_visible_blocking_fields"),
                       "js_submit_handler_present": js_submit, "autofocus": bool(f.get("autofocus")),
                       "form_visible": f.get("visible"), "blocked_suspected": blocked_note})

        # --- C. 래핑으로 수집한 네이티브 키 리스너
        groups: dict = {}
        for r in handlers.get("records", []):
            if r.get("type") == "submit":
                stats["submit_listeners"] += 1
            if r.get("type") not in ("keydown", "keyup", "keypress"):
                continue
            stats["wrapped_key_listeners"] += 1
            t = targets.get(str(r.get("target_id"))) or {}
            site_frame = (r.get("stack") or [""])[0]
            key = (r.get("type"), target_desc(t), t.get("css_path"), site_frame, h(r.get("listener_src")))
            groups.setdefault(key, []).append(r)
        for gi, (key, rs) in enumerate(groups.items()):
            if all(x.get("removed") for x in rs):
                stats["removed_key_listeners"] = stats.get("removed_key_listeners", 0) + len(rs)
                continue  # 수집 시점에 이미 제거된 리스너는 레코드로 만들지 않음 (통계에만 반영)
            r = rs[0]
            t = targets.get(str(r.get("target_id"))) or {}
            kp = extract_keys(r.get("listener_src"))
            disp = dispatcher_guess(r.get("listener_src"))
            keys = kp["keys"]
            reach = "hotkey" if keys else "unknown"
            out_seq = " | ".join(keys) if keys else None
            glob = is_global_target(t)
            conf = "medium" if keys and glob else "low"
            tname = target_desc(t)
            b.add(action_key=("handler", r.get("type"), tname, t.get("css_path"), key[3], key[4]),
                  action_name=f"{r.get('type')} listener on {tname}" + (f" ({t.get('text')[:40]})" if t.get("text") else ""),
                  label_texts=[t.get("text"), t.get("aria_label"), t.get("title")],
                  output_sequence=out_seq, reach_path=reach, evidence_source="static-handler",
                  evidence_ptr=f"frames[{fi}].handlers.records[seq={r.get('seq')}]", confidence=conf,
                  focus=("any (global listener)" if glob else f"element {tname} or its descendants"),
                  frame_is_top=top,
                  aux={"event_type": r.get("type"), "via": r.get("via"), "n_registrations": len(rs),
                       "global_scope": glob, "target_connected": t.get("connected"), "callsite": key[3],
                       "listener_kind": r.get("listener_kind"), "listener_len": r.get("listener_len"),
                       "keys_extracted": keys, "keys_in_K_in": sorted(set(keys) & K_IN),
                       "modifiers": kp["modifiers"], "prevent_default": kp["prevent_default"],
                       "calls_submit_like": kp["calls_submit_like"], "dispatcher_guess": disp,
                       "removed": all(x.get("removed") for x in rs), "t_ms": r.get("t_ms"),
                       "blocked_suspected": blocked_note})

        # --- D. 인라인 핸들러 속성
        for i, ih in enumerate(dom.get("inline_handlers", [])):
            if ih.get("attr") == "onsubmit":
                continue  # 폼 레코드 aux에 반영됨
            kp = extract_keys(ih.get("code"))
            tname = ih.get("tag", "?") + (f"#{ih['id']}" if ih.get("id") else "")
            b.add(action_key=("inline", ih.get("attr"), elem_signature(ih)),
                  action_name=f"{ih.get('attr')} attribute on {tname}",
                  label_texts=[ih.get("text"), ih.get("aria_label"), ih.get("title")],
                  output_sequence=" | ".join(kp["keys"]) or None,
                  reach_path="hotkey" if kp["keys"] else "unknown", evidence_source="static-handler",
                  evidence_ptr=f"frames[{fi}].dom.inline_handlers[{i}]", confidence="low",
                  focus=f"element {tname}", frame_is_top=top,
                  aux={"via": "inline-attribute", "keys_extracted": kp["keys"], "modifiers": kp["modifiers"],
                       "css_path": ih.get("css_path"), "blocked_suspected": blocked_note})

        # --- E. 프레임워크 위임 핸들러 (React/Vue/jQuery)
        for i, fh in enumerate(dom.get("framework_handlers", [])):
            if fh.get("event") in ("onSubmit", "submit") or str(fh.get("event", "")).lower().startswith("onsubmit"):
                continue
            kp = extract_keys(fh.get("src"))
            tname = fh.get("tag", "?") + (f"#{fh['id']}" if fh.get("id") else "")
            glob = fh.get("tag") in ("window", "document", "body", "html")
            b.add(action_key=("fw", fh.get("framework"), fh.get("event"), elem_signature(fh), fh.get("delegate_selector"),
                              h(fh.get("src"))),
                  action_name=f"{fh.get('framework')} {fh.get('event')} on {tname}" +
                              (f" (delegate: {fh.get('delegate_selector')})" if fh.get("delegate_selector") else ""),
                  label_texts=[fh.get("text"), fh.get("aria_label"), fh.get("title")],
                  output_sequence=" | ".join(kp["keys"]) or None,
                  reach_path="hotkey" if kp["keys"] else "unknown", evidence_source="static-handler",
                  evidence_ptr=f"frames[{fi}].dom.framework_handlers[{i}]",
                  confidence="medium" if kp["keys"] and glob else "low",
                  focus=("any (global)" if glob else f"element {tname} or descendants"), frame_is_top=top,
                  aux={"via": f"framework:{fh.get('framework')}", "keys_extracted": kp["keys"],
                       "keys_in_K_in": sorted(set(kp["keys"]) & K_IN), "modifiers": kp["modifiers"],
                       "calls_submit_like": kp["calls_submit_like"], "css_path": fh.get("css_path"),
                       "blocked_suspected": blocked_note})

        # --- F. 선언형 단축키 속성 (data-hotkey, aria-keyshortcuts 등)
        for i, d in enumerate(dom.get("declarative_shortcuts", [])):
            name = d.get("text") or d.get("aria_label") or d.get("title") or d.get("tag")
            b.add(action_key=("el", elem_signature(d)), action_name=name,
                  label_texts=[d.get("text"), d.get("aria_label"), d.get("title"), url_path(d.get("href"))],
                  output_sequence=d.get("value"), reach_path="hotkey", evidence_source="static-dom",
                  evidence_ptr=f"frames[{fi}].dom.declarative_shortcuts[{i}]",
                  confidence="medium" if d.get("attr") in ("data-hotkey", "aria-keyshortcuts") else "low",
                  focus="unknown (depends on library scope)", frame_is_top=top,
                  aux={"attr": d.get("attr"), "css_path": d.get("css_path"), "blocked_suspected": blocked_note})

        # --- G. focused-control 후보 (액션 키워드 라벨이 붙은 컨트롤)
        for i, c in enumerate(dom.get("focused_controls", [])):
            lab = classify(c.get("text"), c.get("aria_label"), c.get("title"))
            if not lab["action_label"] and not lab["needs_manual_review"]:
                continue  # JS 사전 필터의 부분일치 오탐 제거
            if c.get("disabled") or c.get("visible") is False:
                continue
            seq = "Enter" if c.get("tag") == "a" else "Enter | Space"
            name = c.get("text") or c.get("aria_label") or c.get("title")
            b.add(action_key=("el", elem_signature(c)), action_name=name,
                  label_texts=[c.get("text"), c.get("aria_label"), c.get("title"), url_path(c.get("href"))],
                  output_sequence=seq, reach_path="focused-control", evidence_source="static-dom",
                  evidence_ptr=f"frames[{fi}].dom.focused_controls[{i}]", confidence="low",
                  focus=f"keyboard focus on control {c.get('css_path')} (Tab order estimated={c.get('tab_focusable_estimated')})",
                  frame_is_top=top,
                  aux={"tab_focusable_estimated": c.get("tab_focusable_estimated"), "within_form": c.get("within_form"),
                       "tag": c.get("tag"), "role": c.get("role"), "type": c.get("type"), "href": c.get("href"),
                       "link_target": c.get("link_target"), "onclick_attr": c.get("onclick_attr"),
                       "css_path": c.get("css_path"), "blocked_suspected": blocked_note})

    cdp = raw.get("cdp_listeners") or {}
    if cdp.get("ok"):
        stats["cdp_key_listeners"] = sum(1 for l in cdp.get("document", []) + cdp.get("window", [])
                                         if l.get("type") in ("keydown", "keyup", "keypress"))
    return b.records, stats


def site_signals(raw_path: Path) -> list[dict]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    meta = raw.get("meta", {})
    rows = []
    for s in raw.get("scripts", []) or []:
        for lib, info in (s.get("libraries") or {}).items():
            rows.append({"site": meta.get("site"), "view": meta.get("view"), "signal": f"library:{lib}",
                         "level": info.get("level"), "source": s.get("url"), "evidence_source": "static-bundle"})
    for fr in raw.get("frames", []) or []:
        dom = fr.get("dom") or {}
        for sc in dom.get("inline_script_scan", []) or []:
            for lib, info in (sc.get("libraries") or {}).items():
                rows.append({"site": meta.get("site"), "view": meta.get("view"), "signal": f"library:{lib}",
                             "level": info.get("level"), "source": f"inline:{sc.get('sha256', '')[:12]}",
                             "evidence_source": "static-bundle"})
        fw = dom.get("frameworks") or {}
        for k, v in fw.items():
            if v:
                rows.append({"site": meta.get("site"), "view": meta.get("view"), "signal": f"framework:{k}",
                             "level": str(v), "source": fr.get("frame_url"), "evidence_source": "static-dom"})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=Path("data"), type=Path)
    ap.add_argument("--corpus", default=None, type=Path, help="카테고리 정보 (없으면 'commerce')")
    args = ap.parse_args(argv)
    data = args.data
    cats = {}
    if args.corpus and args.corpus.exists():
        with args.corpus.open(encoding="utf-8-sig") as f:
            cats = {r["site"]: r.get("category") or "" for r in csv.DictReader(f)}

    all_recs, all_stats, signals = [], [], []
    for raw_path in sorted((data / "raw").glob("*/*.json")):
        try:
            recs, st = process_view(raw_path, data, cats.get(raw_path.parent.name, "commerce"))
        except Exception as e:  # 손상된 raw는 건너뛰고 기록
            print(f"[normalize] skip {raw_path}: {type(e).__name__}: {e}")
            continue
        all_recs += recs
        all_stats.append(st)
        signals += site_signals(raw_path)

    outd = data / "records"
    outd.mkdir(parents=True, exist_ok=True)
    with (outd / "records.jsonl").open("w", encoding="utf-8") as f:
        for r in all_recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (outd / "records.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCHEMA_FIELDS + EXTRA_FIELDS)
        w.writeheader()
        for r in all_recs:
            row = dict(r)
            row["aux"] = json.dumps(r["aux"], ensure_ascii=False)
            w.writerow(row)
    with (outd / "view_stats.csv").open("w", encoding="utf-8-sig", newline="") as f:
        if all_stats:
            w = csv.DictWriter(f, fieldnames=list(all_stats[0].keys()))
            w.writeheader()
            w.writerows(all_stats)
    with (outd / "site_signals.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["site", "view", "signal", "level", "source", "evidence_source"])
        w.writeheader()
        w.writerows(signals)
    print(f"[normalize] records={len(all_recs)} views={len(all_stats)} signals={len(signals)} -> {outd}")


if __name__ == "__main__":
    main()
