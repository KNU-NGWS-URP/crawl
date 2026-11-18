# pilot-checker v0.4.0

저장된 수집 자료를 읽어 동작을 분류하고, 필요한 대표 사례의 키 입력 관찰을 돕는 별도 도구입니다.
2026-11-17 고정한 실행 코드와 동일합니다. 2026-11-18 전달본은 문서만 현재 요청에 맞춰 정리했습니다.

## 현재 사용하는 도구

| 도구 | 하는 일 |
|---|---|
| 동료가 초기에 전달한 km_static | 현재 채택한 1차 정적 수집기. 키 동작 후보와 근거 수집 |
| pilot-checker v0.4.0 | 이미 저장된 자료를 읽어 분류하고 대표 사례의 별도 키 검사를 지원 |

수집기와 분류·검사기는 별도 도구입니다. 자동 분류 제안은 수동 라벨링의 초안이며 최종 판정을 대신하지 않습니다.
수집은 동료가 초기에 전달한 km_static으로 수행하고, 그 저장 결과에 v0.4.0을 적용합니다.
v0.4.0은 동료 초기 km_static의 data_run1 형식으로 검증했습니다. 이 폴더는 고정 버전을 보관하며 기존 수집기는 변경하지 않습니다.

## 오프라인 사용

Python 3.10 이상이면 분류 분석을 실행할 수 있습니다. Python 외부 패키지는 필요 없습니다.
압축을 풀고 이 README가 있는 폴더에서 실행합니다.

```powershell
python -m pilotcheck --data 'C:/path/to/data_run1' --out 'C:/path/to/new-analysis' --no-html
```

기존 승인 후보 엑셀과 연결하려면 `--baseline 'C:/path/to/baseline.xlsx'`를 추가합니다.
조건별 관찰 대장을 연결하려면 해당 형식의 `--observations`를 사용합니다.
원본 관찰 요약 JSON은 `--review-ledger` 형식이 아닙니다.
입력 상세는 [INPUT_FORMAT.md](INPUT_FORMAT.md)를 확인하세요. 출력은 반드시 원자료와 분리한 새 폴더로 지정합니다.

`settings.example.json`을 `settings.local.json`으로 복사하고 경로를 정하면 `run.ps1 -Mode Analyze`도 사용할 수 있습니다.
설정 경로는 각 PC에서 바꿔야 합니다. 개인 설정·원본 엑셀·수집 결과·관찰 대장은 이 전달본에 넣지 않았습니다.

## 검사와 검증 범위

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
```

Node 20 이상과 Playwright 1.62.1은 로컬 브라우저 테스트·별도 관찰 기능에만 필요합니다.
Node 의존성은 `npm install`로 설치합니다. 테스트는 `node --test tests/probe.test.mjs`입니다.
기본 브라우저 설치가 없고 Edge를 쓰는 경우 `PILOT_BROWSER_CHANNEL=msedge`를 설정합니다.
Codex 번들 모듈을 쓰는 경우 `PILOT_PLAYWRIGHT_ROOT`에 해당 node_modules 경로를 설정합니다.
이 전달 작업에서는 로컬 테스트만 수행했습니다. 실사이트 Probe를 실행한 것이 아닙니다.

분류 제안·키 전달·UI 변화는 최종 결제·전송 성공이나 위험 셀 확정을 뜻하지 않습니다.
독립 분류 정확도와 실제 자연스러움 점수는 측정하지 않았습니다.
[RULES.md](RULES.md)는 판정 기준, [NATURALNESS.md](NATURALNESS.md)는 간단한 정의입니다.
자연스러움 사용자 실험·은밀성 평가는 현재 계획에 넣지 않습니다.
합성 예시와 naturalness.py는 기존 고정 코드 보존용이며 실측 자료가 아닙니다.

원본 고정 코드와의 일치는 RELEASE.json, 전달 파일 무결성은 MANIFEST.sha256.json에 기록합니다.
원시 키 매핑·개인정보·인증 자료는 별도로 공유하지 않았습니다.
