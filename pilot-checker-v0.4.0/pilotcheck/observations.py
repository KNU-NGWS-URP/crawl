"""Attach scoped observations as provenance, never as classifier overrides."""
from __future__ import annotations

import json
from pathlib import Path
from .core import digest


def attach_observations(dataset: dict, path: Path) -> None:
    ledger = json.loads(path.read_text(encoding='utf-8-sig'))
    if ledger.get('schema') != 'pilotcheck-scoped-observations-v1':
        raise ValueError('조건별 관찰 대장 형식이 아닙니다')
    if not dataset.get('baseline_sha256') or ledger.get('baseline_sha256') != dataset['baseline_sha256']:
        raise ValueError('관찰 대장과 기준 엑셀 해시 불일치')
    records = {r['id']: r for r in dataset['records']}
    cases = ledger.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('관찰 사례가 없습니다')
    seen = set()
    links = {key: [] for key in records}
    for case in cases:
        cid = case.get('id')
        if not cid or cid in seen:
            raise ValueError('관찰 사례 ID 누락 또는 중복')
        seen.add(cid)
        if not case.get('row_ids') or not case.get('conditions') or not case.get('limit') or not case.get('repeat'):
            raise ValueError('관찰 적용 범위·조건·한계·반복 기록 누락')
        if case.get('association') not in ('scoped', 'ambiguous-rows', 'substitute-product'):
            raise ValueError('관찰 연결 종류 오류')
        for rid in case['row_ids']:
            if rid not in records:
                raise ValueError('관찰이 알 수 없는 원본 행을 참조함')
            links[rid].append(cid)
        for condition in case['conditions']:
            if not all(condition.get(k) for k in ('focus', 'input', 'result')):
                raise ValueError('포커스·입력·결과 조건 누락')
    # Validate everything before mutating the dataset. Associations do not assert
    # target identity, reachability, confirmation, or a final consequence grade.
    dataset['observation_ledger'] = ledger
    dataset['observation_ledger_sha256'] = digest(path)
    for rid, refs in links.items():
        records[rid]['observation_refs'] = refs
