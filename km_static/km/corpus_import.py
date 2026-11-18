"""km/corpus_import.py — 코퍼스 v1 후보 엑셀(카테고리별 상위 50)을 카테고리별 수집 설정 CSV 로 변환한다.

실행 예:
  python -m km.corpus_import --xlsx "코퍼스 v1 후보 — 카테고리별 상위 50 (Tranco V38GN).xlsx" \
      --categories social,productivity,cloud --out-dir config

출력: config/corpus_v1_<category>.csv (카테고리마다 한 파일) + config/corpus_v1_source.json (원본 파일 해시·행 수)

- 엑셀의 '검증 결과' 가 '제외' 인 행은 건너뛴다 ('포함'·'보류'·'미검증'·빈칸은 포함).
- 화면(view)은 사이트당 5개다: entry(진입 URL) / home / login / signup / explore(social) 또는 pricing(그 밖).
  home_url 은 진입 URL 이 로그인 뒤 앱·콘솔(예: console.aws.amazon.com)이면 그 서비스의 공개 소개 페이지,
  아니면 진입 URL 호스트의 첫 페이지다. home 이 entry 와 같은 페이지면 수집 때 다시 접속하지 않는다(same_as_entry).
  login·signup·explore·pricing URL 은 비워 두며, 수집 때 entry·home 화면의 링크(또는 로그인 리다이렉트)에서 찾는다.
  직접 지정하려면 생성된 CSV 의 칸을 채우면 된다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import openpyxl

FIELDS = ["no", "site", "service", "type", "category", "rank", "rank_source", "confidence", "verified",
          "locale", "timezone", "entry_url", "home_url", "login_url", "signup_url", "explore_url", "pricing_url",
          "owner", "notes"]

# 진입 URL 이 로그인 뒤 앱·콘솔인 서비스의 공개 소개 페이지 (home 화면). 여기에 없으면 진입 URL 호스트의 첫 페이지.
SERVICE_HOME = {
    # social
    "web.whatsapp.com": "https://www.whatsapp.com/", "web.snapchat.com": "https://www.snapchat.com/",
    "web.telegram.org": "https://telegram.org/",
    # productivity
    "drive.google.com": "https://workspace.google.com/products/drive/", "cloud.mail.ru": "https://cloud.mail.ru/",
    "onedrive.live.com": "https://www.microsoft.com/en-us/microsoft-365/onedrive/online-cloud-storage",
    "acrobat.adobe.com": "https://www.adobe.com/acrobat.html", "pan.baidu.com": "https://pan.baidu.com/",
    "disk.yandex.ru": "https://360.yandex.ru/disk/", "app.slack.com": "https://slack.com/",
    "mybox.naver.com": "https://mybox.naver.com/", "app.box.com": "https://www.box.com/",
    "workdrive.zoho.com": "https://www.zoho.com/workdrive/", "drive.proton.me": "https://proton.me/drive",
    # cloud
    "console.cloud.google.com": "https://cloud.google.com/", "dash.cloudflare.com": "https://www.cloudflare.com/",
    "portal.azure.com": "https://azure.microsoft.com/", "console.aws.amazon.com": "https://aws.amazon.com/",
    "admin.gandi.net": "https://www.gandi.net/", "dcc.godaddy.com": "https://www.godaddy.com/",
    "dashboard.stripe.com": "https://stripe.com/", "cloud.oracle.com": "https://www.oracle.com/cloud/",
    "ap.www.namecheap.com": "https://www.namecheap.com/", "app.datadoghq.com": "https://www.datadoghq.com/",
    "cloud.ibm.com": "https://www.ibm.com/cloud", "home.console.aliyun.com": "https://www.aliyun.com/",
    "my.selectel.ru": "https://selectel.ru/", "cloud.digitalocean.com": "https://www.digitalocean.com/",
    "console.redhat.com": "https://www.redhat.com/", "cloud.linode.com": "https://www.linode.com/",
    "manage.auth0.com": "https://auth0.com/", "console.twilio.com": "https://www.twilio.com/",
    "www.ovh.com": "https://www.ovhcloud.com/", "control.akamai.com": "https://www.akamai.com/",
    "hpanel.hostinger.com": "https://www.hostinger.com/", "console.hetzner.cloud": "https://www.hetzner.com/cloud",
    "cloud.mongodb.com": "https://www.mongodb.com/products/platform/atlas-database",
    "cloud.elastic.co": "https://www.elastic.co/cloud",
}


def home_for(entry_url: str) -> str:
    q = urlparse(entry_url or "")
    if not q.hostname:
        return ""
    return SERVICE_HOME.get(q.hostname, f"{q.scheme or 'https'}://{q.hostname}/")

# 언어권이 분명한 사이트는 해당 locale 로 접속한다 (나머지는 en-US). 판정이 아니라 접속 조건이다.
TLD_LOCALE = {"ru": "ru-RU", "jp": "ja-JP", "kr": "ko-KR", "cn": "zh-CN", "tw": "zh-TW", "pl": "pl-PL",
              "br": "pt-BR", "se": "sv-SE", "de": "de-DE", "fr": "fr-FR"}
SITE_LOCALE = {
    # 한국
    "naver.com": "ko-KR", "fmkorea.com": "ko-KR", "dcinside.com": "ko-KR", "ruliweb.com": "ko-KR",
    "arca.live": "ko-KR", "theqoo.net": "ko-KR",
    # 중국어권
    "weibo.com": "zh-CN", "zhihu.com": "zh-CN", "douban.com": "zh-CN", "douyin.com": "zh-CN",
    "xiaohongshu.com": "zh-CN", "baidu.com": "zh-CN", "qq.com": "zh-CN", "aliyun.com": "zh-CN",
    "dingtalk.com": "zh-CN", "ptt.cc": "zh-TW",
    # 러시아어권
    "vk.com": "ru-RU", "ok.ru": "ru-RU", "mail.ru": "ru-RU", "yandex.ru": "ru-RU",
    # 일본
    "ameblo.jp": "ja-JP", "note.com": "ja-JP", "pixiv.net": "ja-JP",
}
LOCALE_TZ = {"ko-KR": "Asia/Seoul", "ja-JP": "Asia/Tokyo", "zh-CN": "Asia/Shanghai", "zh-TW": "Asia/Taipei",
             "ru-RU": "Europe/Moscow", "pl-PL": "Europe/Warsaw", "pt-BR": "America/Sao_Paulo",
             "sv-SE": "Europe/Stockholm", "de-DE": "Europe/Berlin", "fr-FR": "Europe/Paris",
             "en-US": "America/New_York"}

HEADER_ALIASES = {
    "카테고리": "category", "카테고리 순위": "no", "사이트(도메인)": "site", "서비스": "service",
    "Tranco 순위": "rank", "세부 유형": "type", "분류 확신도": "confidence", "진입 URL": "entry_url",
    "비고": "notes", "검증 결과": "verified", "검증 메모": "verify_note", "담당자": "owner",
}


def locale_for(site: str, entry_url: str) -> str:
    if site in SITE_LOCALE:
        return SITE_LOCALE[site]
    host = urlparse(entry_url or "").hostname or site
    tld = host.rsplit(".", 1)[-1].lower()
    return TLD_LOCALE.get(tld, "en-US")


def find_candidate_sheet(wb):
    for ws in wb.worksheets:
        head = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        if "카테고리" in head and "진입 URL" in head:
            return ws, head
    raise SystemExit("[corpus_import] '카테고리'·'진입 URL' 머리글이 있는 시트를 찾지 못함")


def read_rows(xlsx: Path) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx, data_only=True, read_only=True)
    ws, head = find_candidate_sheet(wb)
    keys = [HEADER_ALIASES.get(str(h).strip(), None) if h else None for h in head]
    rows = []
    for vals in ws.iter_rows(min_row=2, values_only=True):
        d = {k: v for k, v in zip(keys, vals) if k}
        if not d.get("category") or not d.get("site"):
            continue
        rows.append({k: ("" if v is None else str(v).strip()) for k, v in d.items()})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="코퍼스 v1 후보 엑셀 → 카테고리별 수집 설정 CSV")
    ap.add_argument("--xlsx", required=True, type=Path)
    ap.add_argument("--categories", required=True, help="쉼표 구분 (예: social,productivity,cloud)")
    ap.add_argument("--out-dir", default=Path("config"), type=Path)
    ap.add_argument("--prefix", default="corpus_v1")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    rows = read_rows(args.xlsx)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    source = {"xlsx": args.xlsx.name, "sha256": hashlib.sha256(args.xlsx.read_bytes()).hexdigest(),
              "imported": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "rank_source": "Tranco V38GN", "categories": {}}
    for cat in cats:
        sel = [r for r in rows if r["category"] == cat]
        excluded = [r["site"] for r in sel if r.get("verified") == "제외"]
        sel = [r for r in sel if r.get("verified") != "제외"]
        if not sel:
            print(f"[corpus_import] {cat}: 행 없음 (카테고리 이름 확인)")
            continue
        out = args.out_dir / f"{args.prefix}_{cat}.csv"
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in sel:
                loc = locale_for(r["site"], r.get("entry_url", ""))
                w.writerow({"no": r.get("no", ""), "site": r["site"], "service": r.get("service", ""),
                            "type": r.get("type", ""), "category": cat, "rank": r.get("rank", ""),
                            "rank_source": "tranco V38GN", "confidence": r.get("confidence", ""),
                            "verified": r.get("verified", "") or "미검증", "locale": loc,
                            "timezone": LOCALE_TZ.get(loc, "America/New_York"),
                            "entry_url": r.get("entry_url", ""), "home_url": home_for(r.get("entry_url", "")),
                            "login_url": "", "signup_url": "", "explore_url": "", "pricing_url": "",
                            "owner": r.get("owner", ""), "notes": r.get("notes", "")})
        source["categories"][cat] = {"file": out.name, "rows": len(sel), "excluded": excluded}
        print(f"[corpus_import] {cat}: {len(sel)} sites -> {out}" + (f" (제외 {len(excluded)})" if excluded else ""))
    (args.out_dir / f"{args.prefix}_source.json").write_text(json.dumps(source, ensure_ascii=False, indent=1),
                                                             encoding="utf-8")


if __name__ == "__main__":
    main()
