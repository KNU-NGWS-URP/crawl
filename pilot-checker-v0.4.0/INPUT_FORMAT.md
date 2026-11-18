# 입력 형식

검증한 입력은 동료가 초기에 전달한 km_static의 data_run1 구조입니다. 이것이 현재 채택한 1차 수집기입니다.

- records/records.jsonl: site, action_id, reach_path, output_sequence 등의 레코드
- raw/: 원본 JSON. evidence_ref는 `raw/file.json#frames[0].dom...`처럼 점·대괄호 포인터를 사용
- labels/label_sheet.xlsx: 있으면 labels 시트를 연결; 없으면 레코드에서 후보를 구성
- snapshots/<site>/<view>.html: 저장 요소 문맥 확인용; 없으면 문맥 근거를 제한
- report/coverage_matrix.csv: 있으면 화면 수집 상태 연결

기존 기준 엑셀을 지정할 때는 ‘후보 동작’ 시트와 원래 열 이름을 사용합니다.
이 전달본에는 원자료나 기준 엑셀을 포함하지 않습니다.
v3.2.0은 폐기된 도구이므로 현재 수집과 이 입력 경로에서 제외합니다.
그 도구의 `#/frames/...` 포인터·관측 구조를 이 로더가 자동 변환하지 않습니다.
현재 고정본의 분류 결과를 사용하려면 입력 형식을 먼저 맞춰야 합니다.
