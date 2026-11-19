"""tests/test_corpus_v1.py — 코퍼스 v1 화면 구성·링크 자동 탐색·설정 변환 검사.

실행:  python -m tests.test_corpus_v1   (외부 접속 없음: 브라우저 요청을 가로채 로컬 HTML 로 응답)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from km.collect import AUTH_LINK_JS, classify_status, same_page, v1_views  # noqa: E402
from km.corpus_import import home_for, locale_for  # noqa: E402

# (설명, 페이지 URL, HTML, 기대 {키: URL 또는 None})
LINK_CASES = [
    ("문구로 로그인·가입·요금제 찾기", "https://app.test/",
     '<a href="/about">About</a><a href="https://id.test/login?x=1">Log in</a>'
     '<a href="/join">Get started</a><a href="/pricing">Pricing</a>',
     {"login": "https://id.test/login?x=1", "signup": "https://app.test/join", "pricing": "https://app.test/pricing"}),
    ("로그아웃 링크는 로그인으로 잡지 않음", "https://app.test/",
     '<a href="/logout">Sign out</a>', {"login": None}),
    ("한국어·중국어 문구", "https://kr.test/",
     '<a href="/member/login">로그인</a><a href="/member/join">회원가입</a><a href="/hot">인기</a>',
     {"login": "https://kr.test/member/login", "signup": "https://kr.test/member/join",
      "explore": "https://kr.test/hot"}),
    ("문구가 없으면 href 경로로", "https://app.test/",
     '<a href="/en/plans/">See offers</a><a href="/accounts/register/">x</a>',
     {"pricing": "https://app.test/en/plans/", "signup": "https://app.test/accounts/register/"}),
    ("같은 사이트의 게시물만 content", "https://www.social.test/",
     '<a href="https://other.test/p/1">o</a><a href="/r/pics/comments/abc/t/">x</a>',
     {"content": "https://www.social.test/r/pics/comments/abc/t/"}),
    ("@프로필도 content", "https://blog.test/", '<a href="/@writer">w</a>', {"content": "https://blog.test/@writer"}),
    ("긴 문장 속 join 은 가입 링크가 아님", "https://app.test/",
     '<a href="https://community.test/c/20">Join the community of IT administrators</a>', {"signup": None}),
    ("같은 페이지 # 링크는 제외", "https://app.test/", '<a href="#">Get started here</a>', {"signup": None}),
    ("문구 + 짧은 꼬리는 인정", "https://app.test/", '<a href="/s">Sign up for free</a>', {"signup": "https://app.test/s"}),
    ("숨은 링크(크기 0)는 문구 탐색에서 제외", "https://app.test/",
     '<a href="https://app.test/x" style="display:none">Log in</a>', {"login": None}),
    ("가입 주소로 가는 링크는 explore 아님", "https://flickr.test/",
     '<a href="https://flickr.test/signup?utm=1">BEST. NEIGHBORS. EVER.</a>', {"explore": None}),
    ("게시글 제목은 explore 아님", "https://dc.test/",
     '<a href="https://dc.test/board/view/?no=9">실시간 아무개 근황 사진.jpg</a><a href="https://dc.test/hot">실시간 베스트</a>',
     {"explore": "https://dc.test/hot"}),
    ("게시판 목록은 content 아님", "https://dc.test/",
     '<a href="https://dc.test/board/lists/?id=best">x</a><a href="https://dc.test/board/view/?id=best&no=1">y</a>',
     {"content": "https://dc.test/board/view/?id=best&no=1"}),
]


def main():
    fails = 0

    def check(desc, ok, got=""):
        nonlocal fails
        fails += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}{(': ' + str(got)) if not ok else ''}")

    check("social 화면 구성", v1_views("social") == ["entry", "home", "login", "signup", "explore", "content"])
    check("cloud 화면 구성", v1_views("cloud") == ["entry", "home", "login", "signup", "pricing", "features"])
    check("www·끝 슬래시는 같은 페이지", same_page("https://www.notion.so/", "https://notion.so"))
    check("경로가 다르면 다른 페이지", not same_page("https://discord.com/app", "https://discord.com/"))
    check("콘솔 진입 → 공개 소개 페이지", home_for("https://console.aws.amazon.com/") == "https://aws.amazon.com/")
    check("진입 경로가 있으면 호스트 첫 페이지", home_for("https://discord.com/app") == "https://discord.com/")
    check("locale: .ru", locale_for("pikabu.ru", "https://pikabu.ru/") == "ru-RU")
    check("locale: 한국 커뮤니티", locale_for("dcinside.com", "https://dcinside.com/") == "ko-KR")
    check("locale: 기본 en-US", locale_for("github.com", "https://github.com/") == "en-US")
    u = "https://dribbble.com/shots/popular"
    check("405 + 'Let's confirm you are human' 은 차단",
          classify_status("explore", u, u, 405, "Let's confirm you are human", "")[0] == "blocked_suspected")
    check("그 밖의 4xx 는 ok 아님", classify_status("login", u, u, 404, "Not found", "")[0] == "http_error")
    check("200 정상 화면은 ok", classify_status("explore", u, u, 200, "Popular shots", "designs")[0] == "ok")

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        current = {}
        pg.route("**/*", lambda r: r.fulfill(body=current.get(r.request.url, ""), content_type="text/html"))
        for desc, url, html, exp in LINK_CASES:
            current.clear()
            current[url] = html
            pg.goto(url)
            got = pg.evaluate(AUTH_LINK_JS)
            ok = all(got.get(k) == v for k, v in exp.items())
            check(f"링크 탐색: {desc}", ok, {k: got.get(k) for k in exp})
        b.close()
    total = 12 + len(LINK_CASES)
    print(f"\n{total - fails}/{total} checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
