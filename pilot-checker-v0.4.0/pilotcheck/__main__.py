from __future__ import annotations

import argparse
import json
from pathlib import Path
from .core import load_dataset, build_plan, merge_observations, compare_runs
from .report import export
from .observations import attach_observations


def main():
    parser=argparse.ArgumentParser(description='원본을 수정하지 않는 파일럿 분류·키 검사 계획 생성기')
    parser.add_argument('--data',type=Path,required=True,help='동료 수집기의 data_run1 등 결과 폴더')
    parser.add_argument('--baseline',type=Path,help='234건 기준 요약 엑셀. 생략 시 수집 레코드 기반')
    parser.add_argument('--review-ledger',type=Path,help='이전 검토 JSON: 판정 이력만 보존, 정답이나 규칙 승인으로 사용하지 않음')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--observations',type=Path,help='조건별 관찰 대장. 규칙·확정 등급을 덮어쓰지 않음')
    parser.add_argument('--no-html',action='store_true',help='JSON·CSV만 저장')
    parser.add_argument('--audit-previous',action='store_true',help='이전 제외 이력도 검사 계획에 포함')
    parser.add_argument('--probe-results',type=Path,help='summary.json과 probe_plan.json이 있는 완료된 검사 실행 폴더')
    parser.add_argument('--compare-with',type=Path,help='같은 계획으로 실행한 이전 검사 폴더')
    args=parser.parse_args()
    # Never export into the input dataset or its parents, and never overwrite its baseline.
    target=args.out.resolve();source=args.data.resolve()
    if target==source or target.is_relative_to(source) or source.is_relative_to(target):
        parser.error('--out은 원자료와 분리된 폴더를 지정하세요')
    dataset=load_dataset(args.data,args.baseline,args.review_ledger)
    if args.observations:
        attach_observations(dataset, args.observations)
    plan=build_plan(dataset,args.audit_previous)
    dataset['summary']['planned_rows'] = len({member for job in plan['jobs'] for member in job['members']})
    if args.probe_results:
        merge_observations(dataset,plan,args.probe_results)
    if args.compare_with:
        if not args.probe_results:parser.error('--compare-with는 --probe-results와 함께 사용하세요')
        dataset['summary']['comparison']=compare_runs(args.compare_with,args.probe_results)
    export(dataset,plan,args.out,include_html=not args.no_html)
    print(json.dumps({**dataset['summary'],'probe_jobs':len(plan['jobs']),'output':str(args.out)},ensure_ascii=False))


if __name__=='__main__':main()
