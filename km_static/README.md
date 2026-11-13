# km_static — Tier 1 Static Analyzer 프로토타입 (커머스 파일럿)

정의서 V1과 연구 계획 v1 구현 순서 ② "소규모 코퍼스 + Static Analyzer 프로토타입"의 구현이다.
커머스 사이트 10~20개에서 accesskey, Enter 암묵적 제출 폼, 키 핸들러 수집이 되는지 확인하는 것이 목적이다.

## 윤리 가드 (코드로 강제)

- 키 이벤트를 합성하거나 주입하지 않는다. `page.keyboard`와 `dispatchEvent`를 쓰지 않는다.
- 요소를 클릭하지 않고 폼을 제출하지 않는다. 페이지 이동은 `page.goto`로만 한다. 스크롤은 JS `scrollTo`로만 한다.
- 로그인하지 않는다. 로그인 후 화면은 미수집으로 남는다(정의서 5장: 미수집이면 '위험 셀 없음'으로 판단하지 않음).
- 봇 차단이 의심되면 우회하지 않고 `blocked_suspected`로 기록한다.
- `data/`(사이트별 키–액션 매핑)는 내부 자료다. 공개물에 포함하지 않는다.

## 구조

```
km/
  instrument.js   init 스크립트: addEventListener / on-property 래핑 → 키·submit 리스너 "등록 사실" 기록
  collect_dom.js  DOM 읽기: accesskey, 폼 암묵적 제출 판정, focused-control 후보, data-hotkey,
                  인라인 핸들러, React/Vue3/jQuery 위임 핸들러, 인라인 스크립트
  collect.py      크롤러: 사이트별 컨텍스트 → 화면(home/search/product/cart/login) 수집, CDP 교차검증,
                  번들 스캔, 스냅샷, 커버리지 기록
  bundle_scan.py  단축키 라이브러리 시그니처(Mousetrap, hotkeys-js, tinykeys 등)
  keyparse.py     리스너 소스에서 키 비교 상수 추출 (e.key==='Enter', keyCode===13, switch-case)
  labels.py       Appendix E 키워드 기반 action_label 1차 추정 (우선순위·보류 규칙 포함)
  normalize.py    raw → Appendix D 스키마 레코드 (records.jsonl/csv)
  summarize.py    보고용 표 + 수동 점검 시트
  reclassify.py   판정 규칙 수정 후 기존 raw 의 화면 상태 재판정 (재접속 없음)
  labelsheet.py   ③ 수동 라벨: C/V 등급 입력 시트 생성(export) → 반영(apply) → 일치도(agree)
  tranco_rank.py  고정 Tranco 리스트로 rank 채우기
config/commerce_pilot.csv   파일럿 코퍼스 20개 (원본 순위 순)
tests/                      로컬 픽스처(정답지 포함) + 검증 스크립트
```

## 설치 (연구실 PC, Windows PowerShell)

```powershell
cd km_static
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium     # --channel chrome 을 쓸 경우 생략 가능
```

Python 3.10 이상이 필요하다.

## 실행 순서

```powershell
# 0) 수집기 자체 검증 (외부 접속 없음). 20/20 checks passed 가 나와야 함 (로컬 테스트 페이지만 검사. 실제 사이트는 접속하지 않음)
python -m tests.test_fixture

# 1) (선택) 순위 재산출: 코퍼스의 rank는 이미 원본 순위로 채워져 있음.
#    같은 Tranco 리스트로 다시 확인하려면 CSV를 내려받은 뒤 (기존 rank를 덮어씀)
python -m km.tranco_rank --corpus config/commerce_pilot.csv --tranco top-1m.csv --list-id XXXX --list-date 2026-11-XX

# 2) 시운전: 2개 사이트로 URL·차단 여부 확인
python -m km.collect --corpus config/commerce_pilot.csv --out data --channel chrome --sites amazon.com,ebay.com

# 3) 전체 실행 (사이트당 약 2~4분, headed 창이 뜸)
python -m km.collect --corpus config/commerce_pilot.csv --out data --channel chrome

# 4) 정규화 → 5) 요약
python -m km.normalize --data data --corpus config/commerce_pilot.csv
python -m km.summarize --data data --manual-sites 5
```

주요 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--channel chrome` | (Playwright Chromium) | 설치된 Chrome 사용. 봇 차단 완화와 실제 환경 근접 |
| `--headless` | headed | 화면 없이 실행. 차단률이 올라갈 수 있음 |
| `--settle` | 5 | networkidle 이후 추가 대기(초). SPA 지연 등록 대응 |
| `--delay` | 4 | 화면 사이 대기(초) |
| `--save-scripts` | off | JS 본문 저장 (재현용, 용량 큼) |
| `--jitter` | 0.25 | 대기 시간 ±변동 비율 |
| `--site-gap` | 0 | 사이트 사이 추가 대기(초) |
| `--max-blocks-per-site` | 0(끔) | 한 사이트에서 차단 의심이 N회 나오면 남은 화면은 접속하지 않고 `skipped_after_block` |

1회차(2026-11-13) 설정: `--delay 30 --site-gap 60 --max-blocks-per-site 2` (약 2시간).

판정 규칙(`collect.classify_status`)을 고친 뒤에는 재접속 없이 기존 수집분에 소급 적용한다.

```powershell
python -m km.reclassify --data data_run1   # raw 상태 갱신 + coverage_*_reclass.csv 생성 → 이후 normalize, summarize
```

화면 상태 `interstitial`(국가 선택 등 중간 페이지), `redirected_home`(요청 화면 대신 홈으로 이동)도 미수집으로 집계한다.
summarize는 coverage 상태가 `ok`인 화면의 레코드만 센다.
| `--views` | 5개 전부 | 예: `--views home,search` |

같은 `--out`으로 다시 실행하면 해당 사이트/화면의 raw를 덮어쓴다. summarize는 가장 최근 커버리지를 쓴다.

## 산출물

```
data/raw/{site}/{view}.json        원시 수집 결과 (frames[].handlers / frames[].dom / cdp_listeners / scripts)
data/snapshots/{site}/{view}.html|png
data/coverage/coverage_{run}.csv   사이트×화면 상태: ok / blocked_suspected / login_redirect / timeout / error /
                                   not_configured / not_found / skipped_search_failed
data/coverage/run_{run}.json       브라우저·Playwright·OS 버전, 인자, 코퍼스 해시
data/records/records.jsonl|csv     Appendix D 레코드 (+ record_id, view, aux)
data/records/view_stats.csv        화면별 폼 수·Enter 후보·래핑/CDP 리스너 수
data/records/site_signals.csv      번들 라이브러리·프레임워크 신호 (액션 레코드 아님)
data/report/summary.md 외          보고용 표, manual_check.csv
```

### 수집 경로 → Appendix D 매핑

| 수집 대상 | reach_path | evidence_source | output_sequence |
|---|---|---|---|
| `[accesskey]` (open shadow DOM 포함) | accesskey | static-accesskey | Windows/Linux `Alt+K`, macOS `Control+Option+K` (Chromium 기준) |
| 암묵적 제출 가능 폼 (`default_button`, `no_button_single_field`) | enter-submit | static-form | Enter |
| 래핑·인라인·React/Vue/jQuery 키 리스너 | 키 상수 추출 시 hotkey, 아니면 unknown | static-handler | 추출된 키 (예: `Enter \| Escape`) |
| `data-hotkey`, `aria-keyshortcuts` 등 | hotkey | static-dom | 속성값 |
| 액션 키워드가 붙은 포커스 가능 컨트롤 | focused-control | static-dom | `Enter \| Space` (링크는 Enter) |

Tier 1의 고정값은 다음과 같다. 정적 근거만으로는 확정하지 않는다(원칙 5·8).

- `physical_trigger_key=null`
- `reachability=unknown`
- `consequence_tier=unknown`, `confirmation_tier=unknown`
- `danger_status=unresolved`, `is_danger_cell=null`
- `tier2_verified=false`

`action_label`은 키워드 기반 1차 추정이다. `aux.needs_manual_review=true`인 항목은 ③ 단계 수동 라벨링 대상이다.

`action_id`는 화면과 무관한 요소 서명으로 만든다. 그래서 공통 헤더 검색창처럼 여러 화면에 반복되는 요소는 같은 action_id로 묶이고, 액션 수 집계 때 중복 제거된다(정의서 5장).

## 코퍼스

`config/commerce_pilot.csv`는 원본 순위 순서로 정렬한 커머스 사이트 20개로 구성된다(번호 1~20). 전부 같은 표본으로 취급한다.

- 접근에 실패한 사이트도 결과에서 지우지 않는다. 미수집으로 분모에 포함해 보고한다(정의서 5장: 미수집 규모와 분모를 함께 보고).
- `rank`는 제공된 원본 순위다. 리스트 ID와 날짜(`rank_date`)는 비어 있으므로 기입한다.
- `locale`과 `timezone`은 사이트별로 지정한다(ru-RU, zh-CN, ja-JP, id-ID 등). 검색어도 현지어로 넣었다.
- 로그인 URL이 비어 있는 사이트(ozon.ru, shop.app, rakuten.co.jp)는 첫 실행에서 실제 URL을 확인해 채운다. 그 전까지는 `not_configured`로 기록된다.
- `login_redirect`는 검색·장바구니 등이 로그인 페이지로 이동한 경우다(Taobao, Shopee 등에서 예상). 우회하지 않으며, 레코드화하지 않고 커버리지에만 남긴다.

## ③ 수동 라벨링: 결과 등급(C)·확인 등급(V)

Tier 1 레코드는 정의서대로 C/V가 모두 `unknown`이다. 등급은 사람이 근거를 보고 입력한다.

```powershell
# 1) 후보 액션만 추린 엑셀 시트 생성 → data_run1/labels/label_sheet.xlsx
python -m km.labelsheet export --data data_run1

# 2) 엑셀에서 노란 열 입력 (드롭다운). 'guide' 시트에 입력 규칙이 있음

# 3) 입력값 반영 → data_run1/labels/records_labeled.jsonl|csv, labels_summary.md
python -m km.labelsheet apply --data data_run1 --sheet data_run1/labels/label_sheet.xlsx

# 라벨러 2명이 같은 시트를 따로 채운 경우 일치도 (Cohen's kappa)
python -m km.labelsheet agree --a 라벨러A.xlsx --b 라벨러B.xlsx

# 재수집 후 시트를 다시 만들 때 기존 입력 이어받기
python -m km.labelsheet export --data data_run1 --prev data_run1/labels/label_sheet.xlsx --out data_run1/labels/label_sheet_v2.xlsx
```

- 시트 행: 정상(ok) 화면의 enter-submit·accesskey 전부, `action_label`이 붙은 focused-control·hotkey,
  `needs_manual_review` 레코드. 사이트·action_id·도달 경로·출력 키가 같으면 화면이 달라도 한 행이다.
- P1~P4, S, R1~R3, E 는 `Y`/`N`/빈칸(근거 부족). 빈칸은 N 이 아니다. C 등급은 이 입력으로 자동 계산된다.
- V 등급은 직접 고른다. 확인 절차를 관측하지 못했으면 `unknown`.
- **자동 사전 라벨 (export 기본값, 끄려면 `--no-prelabel`)**: 정적 근거만으로 페이지·포커스 이동임이 분명한 행은
  P1~P4=N, S=N, E=Y(→ C1)로 미리 채우고 `reviewer=auto-prelabel`, 규칙 ID를 `notes`에 남긴다.
  - `R-form`: GET 폼, JS submit 핸들러 없음, action URL에 부수효과 단어 없음 (confidence medium)
  - `R-search`: GET 검색 폼, JS 핸들러 있음 (confidence low)
  - `R-link`: 실제 URL로 가는 `<a href>`, 인라인 onclick 없음, URL·문구에 부수효과 단어 없음 (medium)
  - `R-akey`: 입력칸으로 포커스만 옮기는 accesskey (medium)
  - 버튼·핫키·POST 폼, 부수효과 URL(login·cart·order·delete 등)이나 강한 동작 문구(Add to cart, Buy now 등)는
    자동 라벨하지 않는다. **C3와 V 등급은 자동으로 정하지 않는다** (실제 동작 확인은 Tier 2).
  - addEventListener 클릭 핸들러는 정적으로 보이지 않으므로, 자동 라벨 일부를 표본 검토하고 확인한 행은
    `reviewer`를 사람 이름으로 바꾼다. 이전 시트(`--prev`)의 사람 입력이 자동 라벨보다 우선한다.
- `danger_status`: excluded(도달 불가·C0~C2·V2 중 하나 확인) / confirmed(reachable·C3·V0~V1 + 최종 검토 Y) /
  candidate(세 조건 충족이나 검토 전, 또는 불충족 없이 C3 근거 있음) / unresolved(그 외).
- 엑셀의 C_auto·danger_auto 열은 입력 확인용 수식이고, 최종 값은 `apply`가 같은 규칙으로 다시 계산한다.
- 규칙 검사: `python -m tests.test_labelsheet`
- 시트와 산출물은 사이트별 키–액션 매핑이므로 `data_*/` 안에 두고 공개하지 않는다.

## 1회차 실행 후 확인할 것

1. coverage에서 `error`/`timeout`인 화면은 URL을 고친다. 이 코퍼스의 URL은 아직 실제 접속으로 확인하지 않았다.
2. `blocked_suspected` 화면은 우회하지 않는다. 차단으로 기록하고 분모에 포함해 보고한다.
3. 상품 화면이 `not_found`이면 `product_href_pattern`(정규식)을 고치거나 `product_url`을 직접 채운다.
4. `handler_crosscheck.csv`에서 CDP 수가 래핑 수보다 크면 래핑이 놓친 리스너가 있다는 뜻이다. 사례를 기록해 둔다.
5. `manual_check.csv`를 보고 DevTools(Elements → Event Listeners)와 대조해 Y/N을 기입한다.

## 알려진 한계 (보고서 타당성 위협 절에 기술)

- 등록 ≠ 실행: 리스너가 있어도 실제로 어떤 키가 무엇을 하는지는 Tier 2 전까지 모른다.
- 키 추출은 리스너 본문만 본다. 다른 함수로 위임하는 번들 코드(React 루트 디스패처, Mousetrap 등)는 키가 비어 있다. 이 부분은 `aux.dispatcher_guess`와 site_signals로 보완한다.
- closed shadow DOM, 래핑 이전에 등록된 리스너(극히 드묾), 사용자 상호작용 후에만 등록되는 핸들러는 수집되지 않는다.
- Enter 제출 판정은 HTML 표준의 암묵적 제출 규칙만 반영한다. JS submit 핸들러가 제출을 막는지는 `aux.js_submit_handler_present`로 표시만 한다.
- accesskey 조합키는 브라우저·OS·충돌 여부에 따라 달라진다.
- 비로그인 화면만 수집하므로 1-click 구매처럼 로그인 후에 나오는 액션은 범위 밖이다.
