"""km/collect.py — Tier 1 Static Analyzer 수집기 (커머스 파일럿용 프로토타입).

실행 예 (연구실 PC, Windows PowerShell):
    python -m km.collect --corpus config/commerce_pilot.csv --out data --channel chrome
    python -m km.collect --corpus config/commerce_pilot.csv --out data --sites coupang.com,ebay.com

윤리 가드 (정의서 4장·계획서 6-가):
  - 키 이벤트를 합성/주입하지 않는다 (page.keyboard, dispatchEvent 사용 금지).
  - 요소를 클릭하지 않으며 폼을 제출하지 않는다. 페이지 이동은 page.goto 로만 한다.
  - 로그인하지 않는다. 로그인 뒤 화면은 '미수집'으로 남긴다.
  - 봇 차단이 감지되면 우회하지 않고 blocked_suspected 로 기록한다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import platform
import random
import re
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import quote, urlparse

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import __version__
from .bundle_scan import scan as bundle_scan
from .labels import ALL_KEYWORDS_JS

HERE = Path(__file__).resolve().parent
INSTRUMENT_JS = (HERE / "instrument.js").read_text(encoding="utf-8")
COLLECT_DOM_JS = (HERE / "collect_dom.js").read_text(encoding="utf-8")

VIEWS = ["home", "search", "product", "cart", "login"]          # commerce 파일럿 (config/commerce_pilot.csv)
# 코퍼스 v1 (config/corpus_v1_<category>.csv): 사이트당 서로 다른 화면 5개.
#   공통: entry(진입 URL) / home / login / signup
#   다섯 번째: social → explore (탐색·인기 피드), 그 밖 → pricing (요금제·구독 버튼)
#   여섯 번째 후보(EXTRA): home 이 entry 와 같은 페이지(same_as_entry)일 때만 대신 수집해 5개를 채운다.
#     social → content (공개 게시물·프로필 1개), 그 밖 → features (기능·제품 소개)
V1_VIEWS_BY_CATEGORY = {"social": ["entry", "home", "login", "signup", "explore", "content"]}
V1_VIEWS_DEFAULT = ["entry", "home", "login", "signup", "pricing", "features"]
EXTRA_VIEWS = ("content", "features")
DISCOVERED_VIEWS = ("login", "signup", "explore", "pricing", "content", "features")  # URL 이 비면 앞 화면 링크에서 찾음
LINK_SOURCE_VIEWS = ("entry", "home", "explore")


def v1_views(category: str) -> list[str]:
    return V1_VIEWS_BY_CATEGORY.get(category, V1_VIEWS_DEFAULT)


def same_page(a: str | None, b: str | None) -> bool:
    """www. 와 끝 '/' 차이는 같은 페이지로 본다 (home 이 entry 와 같으면 다시 접속하지 않기 위해)."""
    def norm(u):
        q = urlparse(u or "")
        return ((q.hostname or "").removeprefix("www."), q.path.rstrip("/"), q.query)
    return bool(a and b) and norm(a) == norm(b)
# entry 화면에서 로그인·가입 링크를 찾아 그 URL 로 이동한다 (클릭하지 않음). 짧은 링크 문구·aria-label·href 경로로 판단.
AUTH_LINK_JS = r"""
() => {
  const LOGIN_T = /^\s*(log\s?-?in|sign\s?-?in|로그인|登录|登入|登錄|ログイン|войти|вход|zaloguj( się)?|entrar|logga in|anmelden|connexion|se connecter|iniciar sesi[oó]n|accedi)\b/i;
  const SIGNUP_T = /^\s*(sign\s?-?up|register|create (an |your |a free )?account|join( now| free| us)?|get started( for free| free)?|try (it )?(for )?free|start (for )?free|회원\s?가입|가입하기|注册|註冊|新規(会員)?登録|会員登録|регистрация|зарегистрироваться|zarejestruj|cadastr|skapa konto|registrieren|s'inscrire|inscription|crear cuenta)/i;
  const LOGIN_H = /\/(login|log-in|signin|sign-in|sign_in|logon)([\/?#._-]|$)/i;
  const SIGNUP_H = /\/(signup|sign-up|sign_up|register|registration|join|create-account|createaccount)([\/?#._-]|$)/i;
  const PRICING_T = /^\s*(pricing|plans( (&|and) pricing)?|compare plans|see plans|view plans|prices|요금(제)?|가격|价格|定价|价目|料金(プラン)?|тарифы|цены|cennik|preços|planos|priser)\b/i;
  const EXPLORE_T = /^\s*(explore|trending|popular|discover|top|hot|best|탐색|둘러보기|인기|실시간|베스트|热门|发现|探索|热搜|話題|トレンド|ランキング|популярное|лучшее|обзор)/i;
  const PRICING_H = /\/(pricing|plans|prices|tariffs?|tarif)([\/?#._-]|$)/i;
  const EXPLORE_H = /\/(explore|trending|popular|discover|hot|best|top)([\/?#._-]|$)/i;
  const FEATURES_T = /^\s*(features|products?|solutions|platform|templates|why [\w.]+|기능|제품|솔루션|템플릿|产品|功能|解决方案|機能|製品|возможности|продукты|решения)\b/i;
  const FEATURES_H = /\/(features|products?|solutions|platform|templates)([\/?#._-]|$)/i;
  // 공개 게시물·프로필: 같은 사이트 안의 게시물형 경로 (클릭하지 않고 URL 로 이동)
  const CONTENT_H = /\/(status|statuses|comments|p|post|posts|video|videos|reel|reels|pin|story|stories|thread|threads|t|question|questions|answer|item|photos|shot|shots|gallery|works|artworks|book\/show|film|n|s|board|article|articles|topic|topics)\/[^\/?#]+|^\/@[\w.-]+\/?$/i;
  const BAD = /(log\s?-?out|sign\s?-?out|logoff|выход|로그아웃|退出)/i;
  const res = {login: null, signup: null, pricing: null, explore: null, features: null, content: null};
  const host = location.hostname.replace(/^www\./, '');
  const here = location.href.split('#')[0];
  // 같은 페이지로 돌아오는 링크(href="#", "#top")는 다른 화면이 아니므로 제외
  const samePage = (href) => href.split('#')[0] === here;
  // 문구는 '구절 + 짧은 꼬리(20자 이내)'만 인정: "Sign up for free" O, "Join the community of IT pros" X
  const fits = (re, t) => { const m = t.match(re); return !!m && (t.length - m[0].length) <= 20; };
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href || '';
    if (!/^https?:/i.test(href) || samePage(href)) continue;
    const r = a.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const t = ((a.innerText || '').trim() || a.getAttribute('aria-label') || a.title || '').replace(/\s+/g, ' ').slice(0, 60);
    if (BAD.test(t) || BAD.test(href)) continue;
    if (!res.login && fits(LOGIN_T, t)) { res.login = href; res.login_by = 'text:' + t; }
    if (!res.signup && fits(SIGNUP_T, t)) { res.signup = href; res.signup_by = 'text:' + t; }
    if (!res.pricing && fits(PRICING_T, t)) { res.pricing = href; res.pricing_by = 'text:' + t; }
    if (!res.explore && fits(EXPLORE_T, t)) { res.explore = href; res.explore_by = 'text:' + t; }
    if (!res.features && fits(FEATURES_T, t)) { res.features = href; res.features_by = 'text:' + t; }
  }
  for (const a of document.querySelectorAll('a[href]')) {   // 문구로 못 찾으면 href 경로로
    const href = a.href || '';
    if (!/^https?:/i.test(href) || BAD.test(href) || samePage(href)) continue;
    let path = ''; try { path = new URL(href).pathname; } catch (e) { continue; }
    if (!res.login && LOGIN_H.test(path)) { res.login = href; res.login_by = 'href'; }
    if (!res.signup && SIGNUP_H.test(path)) { res.signup = href; res.signup_by = 'href'; }
    if (!res.pricing && PRICING_H.test(path)) { res.pricing = href; res.pricing_by = 'href'; }
    if (!res.explore && EXPLORE_H.test(path)) { res.explore = href; res.explore_by = 'href'; }
    if (!res.features && FEATURES_H.test(path)) { res.features = href; res.features_by = 'href'; }
    let h2 = ''; try { h2 = new URL(href).hostname.replace(/^www\./, ''); } catch (e) {}
    if (!res.content && (h2 === host || h2.endsWith('.' + host) || host.endsWith('.' + h2)) && CONTENT_H.test(path)) {
      res.content = href; res.content_by = 'href';
    }
  }
  return res;
}
"""
KEY_EVENT_TYPES = {"keydown", "keyup", "keypress", "submit"}
BLOCK_RX = re.compile(
    r"captcha|access denied|are you a (robot|human)|robot or human|pardon our interruption|"
    r"unusual traffic|unusual (browser )?activity|confirm (that )?you('|’)?re (a )?human|verify (that )?you are (a )?human|"
    r"i am human|press (&|and) hold|request blocked|bot detection|click the button below to continue shopping|"
    r"보안문자|보안 문자|자동입력 방지|비정상적인 (접근|요청)|접근이 (차단|제한)",
    re.IGNORECASE,
)
# 챌린지·체크포인트로 리다이렉트된 경우 (예: amazon.com/checkpoint/continue, /errors/validateCaptcha)
BLOCK_URL_RX = re.compile(
    r"/(checkpoint|captcha|challenge|validatecaptcha|blocked|px-captcha|verify|[a-z_]*verification(\.html)?)\b",
    re.IGNORECASE)
# Akamai 등 CDN 차단 페이지는 본문이 'Reference #18.xxxx' 한 줄뿐인 경우가 있음
BLOCK_BODY_ONLY_RX = re.compile(r"^\s*(access denied.*)?reference\s*#\d+\.[0-9a-f]+\.\d+\.[0-9a-f]+\s*$",
                                re.IGNORECASE | re.S)
# 국가·지역 선택 등 의도한 화면 앞에 끼는 중간 페이지 (차단은 아니지만 의도한 화면도 아님)
INTERSTITIAL_RX = re.compile(r"choose a country|select your country|select your region|choose your (country|region)",
                             re.IGNORECASE)


def classify_status(view: str, requested_url: str, final_url: str, http_status, title: str, body: str):
    """화면 수집 상태 판정. (status, reasons) 반환. 판정만 하며 우회하지 않는다.

    status: blocked_suspected / login_redirect / interstitial / redirected_home / ok
    """
    title = title or ""
    body = body or ""
    req, fin = urlparse(requested_url or ""), urlparse(final_url or "")
    reasons = []
    if http_status in (401, 403, 429, 498, 503):
        reasons.append(f"http_{http_status}")
    if BLOCK_URL_RX.search(fin.path) and not BLOCK_URL_RX.search(req.path):
        reasons.append("challenge_url")
    m_txt = BLOCK_RX.search(title + " " + body)
    if m_txt:
        reasons.append(f"text:{m_txt.group(0)[:40]}")
    if BLOCK_BODY_ONLY_RX.match(body.strip()[:300]):
        reasons.append("cdn_reference_only")
    if reasons:
        return "blocked_suspected", reasons
    if view != "login" and LOGIN_RX.search(fin.netloc + fin.path) and not LOGIN_RX.search(req.netloc + req.path):
        return "login_redirect", ["login_url"]
    m_int = INTERSTITIAL_RX.search(title + " " + body[:3000])
    if m_int:
        return "interstitial", [f"text:{m_int.group(0)[:40]}"]
    if view != "home" and fin.path in ("", "/") and req.path not in ("", "/"):
        return "redirected_home", ["final_path=/"]
    return "ok", []
MAX_SCRIPT_BYTES = 15_000_000
LOGIN_RX = re.compile(r"(^|[./_-])(login|signin|sign-in|logowanie|passport|auth|sso)([./_-]|$)", re.IGNORECASE)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:120]


def load_corpus(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("site") and not r["site"].startswith("#")]
    return rows


# ---------------------------------------------------------------- CDP 교차검증
class CdpListenerProbe:
    """CDP DOMDebugger.getEventListeners 로 등록 리스너를 별도 경로로 조회 (래핑 결과 교차검증용)."""

    def __init__(self, context, page):
        self.ok = False
        self.script_urls: dict[str, str] = {}
        try:
            self.session = context.new_cdp_session(page)
            self.session.on("Debugger.scriptParsed", self._on_script)
            self.session.send("Debugger.enable")
            self.ok = True
        except Exception:
            self.session = None

    def _on_script(self, ev):
        try:
            self.script_urls[ev["scriptId"]] = ev.get("url") or ""
        except Exception:
            pass

    def listeners(self) -> dict:
        if not self.ok:
            return {"ok": False, "error": "cdp-unavailable"}
        out = {"ok": True, "document": [], "window": []}
        try:
            for name, expr in (("document", "document"), ("window", "window")):
                obj = self.session.send("Runtime.evaluate", {"expression": expr})
                oid = obj.get("result", {}).get("objectId")
                if not oid:
                    continue
                params = {"objectId": oid}
                if name == "document":
                    params.update({"depth": -1, "pierce": True})
                res = self.session.send("DOMDebugger.getEventListeners", params)
                for l in res.get("listeners", []):
                    if l.get("type") not in KEY_EVENT_TYPES:
                        continue
                    out[name].append({
                        "type": l.get("type"),
                        "useCapture": l.get("useCapture"),
                        "once": l.get("once"),
                        "backendNodeId": l.get("backendNodeId"),
                        "script_url": self.script_urls.get(l.get("scriptId", ""), None),
                        "lineNumber": l.get("lineNumber"),
                        "columnNumber": l.get("columnNumber"),
                    })
            try:
                self.session.send("Debugger.disable")
            except Exception:
                pass
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return out


# ---------------------------------------------------------------- 페이지 수집
def wait_and_settle(page, settle: float, idle_timeout: float):
    try:
        page.wait_for_load_state("networkidle", timeout=idle_timeout * 1000)
    except PWTimeout:
        pass
    page.wait_for_timeout(settle * 1000)
    # 지연 로딩 유도를 위한 스크롤 (JS scrollBy: 키·마우스 이벤트를 합성하지 않음)
    try:
        page.evaluate("""async () => {
            const h = Math.min(document.body ? document.body.scrollHeight : 0, 12000);
            for (let y = 0; y < h; y += 900) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 250)); }
            window.scrollTo(0, 0);
        }""")
    except PWError:
        pass
    page.wait_for_timeout(2000)


def collect_frames(page) -> list[dict]:
    frames = []
    for fr in page.frames:
        item = {"frame_url": fr.url, "frame_name": fr.name, "is_top": fr == page.main_frame}
        if not fr.url:
            item["skipped"] = "no-url"
            frames.append(item)
            continue
        try:
            item["handlers"] = fr.evaluate("() => (window.__km_dump__ ? window.__km_dump__() : null)")
        except PWError as e:
            item["handlers_error"] = str(e)[:300]
        try:
            dom = fr.evaluate(COLLECT_DOM_JS, {"actionKeywordRegex": ALL_KEYWORDS_JS})
            scripts = dom.pop("inline_scripts", [])
            inline_scan = []
            for s in scripts:
                r = bundle_scan(s)
                if r["libraries"] or any(r["generic_counts"].values()):
                    r["sha256"] = sha256(s.encode("utf-8", "ignore"))
                    inline_scan.append(r)
            dom["inline_script_count"] = len(scripts)
            dom["inline_script_scan"] = inline_scan
            item["dom"] = dom
        except PWError as e:
            item["dom_error"] = str(e)[:300]
        frames.append(item)
    return frames


def find_product_url(page, pattern: str) -> str | None:
    if not pattern:
        return None
    try:
        hrefs = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)")
    except PWError:
        return None
    rx = re.compile(pattern)
    for h in hrefs:
        if h.startswith("http") and rx.search(h):
            return h
    return None


def collect_view(context, site: str, view: str, url: str, out_dir: Path, scripts_dir: Path,
                 script_scan_cache: dict, args) -> dict:
    """한 화면을 열고 수집한다. 반환값은 coverage 행 + 다음 단계용 정보."""
    meta = {"site": site, "view": view, "requested_url": url, "started": now_iso(), "status": "error",
            "browser_version": getattr(args, "_browser_version", None), "locale": getattr(args, "_locale", None),
            "platform": platform.system(), "km_version": __version__}
    page = context.new_page()
    page.on("dialog", lambda d: d.dismiss())
    script_responses = []

    def on_response(resp):
        try:
            if resp.request.resource_type == "script" or "javascript" in (resp.headers.get("content-type") or ""):
                script_responses.append(resp)
        except Exception:
            pass

    page.on("response", on_response)
    probe = CdpListenerProbe(context, page) if not args.no_cdp else None
    result = {"meta": meta}
    product_url = None
    auth_links = {}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=args.nav_timeout * 1000)
        meta["http_status"] = resp.status if resp else None
        wait_and_settle(page, args.settle, args.idle_timeout)
        meta["final_url"] = page.url
        meta["title"] = page.title()

        frames = collect_frames(page)
        result["frames"] = frames
        result["cdp_listeners"] = probe.listeners() if probe else {"ok": False, "error": "disabled"}

        # 외부 스크립트 번들 스캔 (sha256으로 중복 제거)
        scans = []
        for r in script_responses:
            try:
                body = r.body()
            except Exception as e:
                scans.append({"url": r.url, "error": type(e).__name__})
                continue
            if len(body) > MAX_SCRIPT_BYTES:
                scans.append({"url": r.url, "error": "too-large", "size": len(body)})
                continue
            h = sha256(body)
            if h not in script_scan_cache:
                script_scan_cache[h] = bundle_scan(body.decode("utf-8", "ignore"))
                if args.save_scripts:
                    p = scripts_dir / f"{h}.js"
                    if not p.exists():
                        p.write_bytes(body)
            scans.append({"url": r.url, "sha256": h, "status": r.status, **script_scan_cache[h]})
        result["scripts"] = scans

        # 스냅샷
        snap_dir = out_dir / "snapshots" / safe_name(site)
        snap_dir.mkdir(parents=True, exist_ok=True)
        try:
            (snap_dir / f"{view}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(snap_dir / f"{view}.png"), full_page=False)
            meta["snapshot"] = str((snap_dir / f"{view}.html").relative_to(out_dir))
        except Exception as e:
            meta["snapshot_error"] = str(e)[:200]

        # 차단 의심 판정 (우회하지 않음)
        top = next((f for f in frames if f.get("is_top")), {})
        body_sample = (top.get("dom") or {}).get("body_text_sample", "")
        # 헤더 텍스트가 길면 1500자 샘플에 챌린지 문구가 안 들어감 → 차단 판정용으로 본문을 더 길게 읽음
        try:
            body_full = page.evaluate("() => document.body ? (document.body.innerText || '').slice(0, 30000) : ''")
        except PWError:
            body_full = body_sample
        status, reasons = classify_status(view, url, page.url, meta.get("http_status"), meta["title"], body_full)
        meta["status"] = status
        meta["block_reasons"] = reasons  # 차단 외 상태의 판정 근거도 여기에 기록
        meta["body_probe"] = body_full[:3000]  # 재판정(km.reclassify)용

        if view == "search" and status == "ok":
            product_url = find_product_url(page, args._product_pattern)
        if view in LINK_SOURCE_VIEWS and status in ("ok", "login_redirect"):
            try:
                auth_links = page.evaluate(AUTH_LINK_JS) or {}
            except PWError:
                auth_links = {}
            meta["auth_links"] = auth_links
    except PWTimeout as e:
        meta["status"] = "timeout"
        meta["error"] = str(e)[:300]
    except PWError as e:
        meta["status"] = "error"
        meta["error"] = str(e)[:300]
    except Exception as e:  # 예기치 못한 사이트 동작
        meta["status"] = "error"
        meta["error"] = f"{type(e).__name__}: {e}"[:300]
        meta["traceback"] = traceback.format_exc()[-1500:]
    finally:
        meta["finished"] = now_iso()
        try:
            page.close()
        except Exception:
            pass

    raw_dir = out_dir / "raw" / safe_name(site)
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{view}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"meta": meta, "product_url": product_url, "auth_links": auth_links}


def build_urls(row: dict, keyword: str) -> dict:
    kw = row.get("search_keyword") or keyword
    urls = {
        "home": row.get("home_url") or None,
        "search": (row.get("search_url") or "").replace("{q}", quote(kw)) or None,
        "cart": row.get("cart_url") or None,
        "login": row.get("login_url") or None,
        "product": row.get("product_url") or None,  # 비어 있으면 검색 결과에서 자동 탐색
        "entry": row.get("entry_url") or None,
        "signup": row.get("signup_url") or None,     # 코퍼스 v1: 비어 있으면 entry·home 화면에서 자동 탐색
        "explore": row.get("explore_url") or None,
        "pricing": row.get("pricing_url") or None,
        "content": row.get("content_url") or None,
        "features": row.get("features_url") or None,
    }
    return urls


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tier 1 Static Analyzer (observe-only)")
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--out", default=Path("data"), type=Path)
    ap.add_argument("--sites", default="", help="쉼표로 구분한 site 필터")
    ap.add_argument("--views", default=None,
                    help="쉼표 구분. 미지정 시 corpus 에 entry_url 칸이 있으면 코퍼스 v1 화면 "
                         "(social: entry,home,login,signup,explore[,content] / 그 밖: entry,home,login,signup,pricing[,features]; "
                         "괄호 안 화면은 home 이 entry 와 같을 때만), 없으면 commerce 화면")
    ap.add_argument("--headless", action="store_true", help="기본은 headed (봇 차단 완화·관찰 용이)")
    ap.add_argument("--channel", default=None, help="chrome / msedge (설치된 브라우저 사용). 미지정 시 Playwright Chromium")
    ap.add_argument("--keyword", default="이어폰", help="search_keyword가 비어 있을 때의 기본 검색어")
    ap.add_argument("--settle", type=float, default=5.0, help="networkidle 이후 추가 대기(초)")
    ap.add_argument("--idle-timeout", type=float, default=15.0)
    ap.add_argument("--nav-timeout", type=float, default=45.0)
    ap.add_argument("--delay", type=float, default=4.0, help="화면 간 대기(초)")
    ap.add_argument("--jitter", type=float, default=0.25, help="대기 시간 무작위 변동 비율 (0.25 → ±25%%)")
    ap.add_argument("--site-gap", type=float, default=0.0, help="사이트 사이 추가 대기(초)")
    ap.add_argument("--max-blocks-per-site", type=int, default=0,
                    help="한 사이트에서 차단 의심 화면이 이 수에 도달하면 남은 화면을 접속하지 않고 skipped_after_block 으로 기록 (0=끄기)")
    ap.add_argument("--resume", action="store_true",
                    help="--out 의 기존 coverage 에 모든 화면이 기록된 사이트는 건너뜀 (긴 실행이 중간에 멈췄을 때)")
    ap.add_argument("--no-cdp", action="store_true")
    ap.add_argument("--save-scripts", action="store_true", help="스크립트 본문을 data/scripts 에 저장 (용량 큼)")
    args = ap.parse_args(argv)
    # 로그를 파일로 돌리면 Windows 기본 인코딩(cp949)이 되어 제목의 특수문자에서 멈춘다 → UTF-8, 못 쓰는 문자는 대체
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    corpus = load_corpus(args.corpus)
    full_corpus = list(corpus)  # 화면 구성은 --sites 로 거르기 전 코퍼스 기준으로 정한다
    if args.sites:
        wanted = {s.strip() for s in args.sites.split(",") if s.strip()}
        corpus = [r for r in corpus if r["site"] in wanted]
    if args.views:
        views = [v.strip() for v in args.views.split(",") if v.strip()]
    else:
        views = (v1_views(full_corpus[0].get("category", ""))
                 if full_corpus and "entry_url" in full_corpus[0] else VIEWS)
    if args.resume:
        seen: dict = {}
        for fp in sorted((args.out / "coverage").glob("coverage_*.csv")):
            if fp.stem.endswith("_reclass"):
                continue
            with fp.open(encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    seen.setdefault(r["site"], set()).add(r["view"])
        done = {s for s, vs in seen.items() if set(views) <= vs}
        print(f"[km] resume: 이미 끝난 사이트 {len(done)}개 건너뜀")
        corpus = [r for r in corpus if r["site"] not in done]
    cats = sorted({r.get("category", "") for r in corpus})
    if len(cats) > 1:
        # 카테고리마다 결과 폴더를 나눈다: 같은 도메인이 여러 카테고리에 있으면 raw/{site}/ 가 서로 덮어쓰기 때문
        raise SystemExit(f"[km] corpus 에 카테고리가 여러 개({cats}) — 카테고리별 CSV 와 --out 으로 나눠 실행하세요 "
                         "(km.pipeline 이 자동으로 나눔)")
    out = args.out
    (out / "coverage").mkdir(parents=True, exist_ok=True)
    scripts_dir = out / "scripts"
    if args.save_scripts:
        scripts_dir.mkdir(parents=True, exist_ok=True)

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_meta = {"run_id": run_id, "started": now_iso(), "km_version": __version__, "python": sys.version,
                "platform": platform.platform(), "args": {k: str(v) for k, v in vars(args).items()},
                "corpus_file": str(args.corpus), "corpus_sha256": sha256(args.corpus.read_bytes())}
    coverage_rows = []
    script_scan_cache: dict = {}
    cov_path = out / "coverage" / f"coverage_{run_id}.csv"

    def pause(sec: float):
        if sec > 0:
            time.sleep(sec * random.uniform(1 - args.jitter, 1 + args.jitter))

    def flush_coverage():  # 긴 실행 중 중단돼도 그때까지의 커버리지를 남긴다
        if not coverage_rows:
            return
        with cov_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(coverage_rows[0].keys()))
            w.writeheader()
            w.writerows(coverage_rows)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, channel=args.channel)
        run_meta["browser_version"] = browser.version
        run_meta["playwright_version"] = getattr(p, "__version__", None)
        print(f"[km] browser={browser.version} sites={len(corpus)} views={views}")

        for si, row in enumerate(corpus):
            if si > 0:
                pause(args.site_gap)
            site = row["site"]
            n_blocked = 0
            locale = row.get("locale") or "ko-KR"
            tz = row.get("timezone") or ("Asia/Seoul" if locale.startswith("ko") else "America/New_York")
            context = browser.new_context(locale=locale, timezone_id=tz, viewport={"width": 1366, "height": 900},
                                          accept_downloads=False)
            context.add_init_script(script=INSTRUMENT_JS)
            args._browser_version = browser.version
            args._locale = locale
            urls = build_urls(row, args.keyword)
            url_src = {v: "config" for v, u in urls.items() if u}
            args._product_pattern = row.get("product_href_pattern") or ""
            for view in views:
                url = urls.get(view)
                if view in EXTRA_VIEWS:
                    home_st = next((c["status"] for c in coverage_rows if c["site"] == site and c["view"] == "home"), None)
                    if home_st != "same_as_entry":  # home 이 따로 있으면 이미 서로 다른 화면 5개 → 접속하지 않음
                        coverage_rows.append({"run_id": run_id, "site": site, "category": row.get("category", ""),
                                              "view": view, "status": "not_needed", "requested_url": "",
                                              "final_url": "", "http_status": "", "title": "", "error": "",
                                              "browser_version": browser.version,
                                              "snapshot_date": dt.date.today().isoformat(), "url_source": ""})
                        print(f"  - {site}/{view}: not_needed (home={home_st})")
                        continue
                if args.max_blocks_per_site and n_blocked >= args.max_blocks_per_site:
                    coverage_rows.append({"run_id": run_id, "site": site, "category": row.get("category", ""),
                                          "view": view, "status": "skipped_after_block", "requested_url": url or "",
                                          "final_url": "", "http_status": "", "title": "", "error": "",
                                          "browser_version": browser.version,
                                          "snapshot_date": dt.date.today().isoformat(),
                                          "url_source": url_src.get(view, "")})
                    print(f"  - {site}/{view}: skipped_after_block")
                    continue
                if not url:
                    if view == "product" and urls.get("search"):
                        st_search = next((c["status"] for c in coverage_rows
                                          if c["site"] == site and c["view"] == "search"), None)
                        status = "not_found" if st_search == "ok" else "skipped_search_failed"
                    elif view in DISCOVERED_VIEWS and urls.get("entry"):
                        # 링크를 찾을 화면(entry·home·explore) 중 하나라도 열렸으면 '못 찾음', 모두 실패했으면 '건너뜀'
                        st_src = {c["status"] for c in coverage_rows
                                  if c["site"] == site and c["view"] in LINK_SOURCE_VIEWS}
                        opened = st_src & {"ok", "login_redirect"}
                        status = "not_found" if opened else "skipped_entry_failed"
                    else:
                        status = "not_configured"
                    coverage_rows.append({"run_id": run_id, "site": site, "category": row.get("category", ""),
                                          "view": view, "status": status, "requested_url": "", "final_url": "",
                                          "http_status": "", "title": "", "error": "", "browser_version": browser.version,
                                          "snapshot_date": dt.date.today().isoformat(), "url_source": ""})
                    print(f"  - {site}/{view}: {status}")
                    continue
                if view == "home" and urls.get("entry"):
                    e_cov = next((c for c in coverage_rows if c["site"] == site and c["view"] == "entry"), {})
                    if same_page(url, urls["entry"]) or same_page(url, e_cov.get("final_url")):
                        coverage_rows.append({"run_id": run_id, "site": site, "category": row.get("category", ""),
                                              "view": view, "status": "same_as_entry", "requested_url": url,
                                              "final_url": e_cov.get("final_url", ""), "http_status": "", "title": "",
                                              "error": "", "browser_version": browser.version,
                                              "snapshot_date": dt.date.today().isoformat(),
                                              "url_source": url_src.get(view, "")})
                        print(f"  - {site}/{view}: same_as_entry")
                        continue
                r = collect_view(context, site, view, url, out, scripts_dir, script_scan_cache, args)
                m = r["meta"]
                if view == "search" and r["product_url"] and not urls.get("product"):
                    urls["product"] = r["product_url"]
                if view in LINK_SOURCE_VIEWS:
                    al = r.get("auth_links") or {}
                    # entry 가 로그인 화면으로 리다이렉트되면 그 URL 이 곧 login 화면
                    if m.get("status") == "login_redirect" and m.get("final_url") and not urls.get("login"):
                        urls["login"], url_src["login"] = m["final_url"], "entry_redirect"
                    for v in DISCOVERED_VIEWS:
                        if al.get(v) and not urls.get(v):
                            urls[v], url_src[v] = al[v], f"discovered({al.get(v + '_by')})"
                coverage_rows.append({"run_id": run_id, "site": site, "category": row.get("category", ""),
                                      "view": view, "status": m.get("status"), "requested_url": url,
                                      "final_url": m.get("final_url", ""), "http_status": m.get("http_status", ""),
                                      "title": (m.get("title") or "")[:120], "error": m.get("error", ""),
                                      "browser_version": browser.version,
                                      "snapshot_date": dt.date.today().isoformat(),
                                      "url_source": url_src.get(view, "")})
                print(f"  - {site}/{view}: {m.get('status')} {m.get('http_status', '')} {(m.get('title') or '')[:50]}",
                      flush=True)
                if m.get("status") == "blocked_suspected":
                    n_blocked += 1
                pause(args.delay)
            flush_coverage()
            try:
                context.close()
            except Exception:
                pass
        browser.close()

    run_meta["finished"] = now_iso()
    (out / "coverage" / f"run_{run_id}.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=1), encoding="utf-8")
    flush_coverage()
    print(f"[km] done. coverage -> {cov_path}")


if __name__ == "__main__":
    main()
