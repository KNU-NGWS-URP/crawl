from __future__ import annotations

import html
import json
from pathlib import Path
from .core import write_csv


def export(dataset: dict, plan: dict, out: Path, *, include_html: bool = True):
    out.mkdir(parents=True, exist_ok=True)
    (out/'analysis.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding='utf-8')
    (out/'probe_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['id','site','action_name','url','output_sequence','rule_id','suggested_c','consequence_tier','confirmation_tier',
              'reachability','danger_status','queue','probe_summary','reason','question','stop_line','mapping','previous_review','observation_refs']
    write_csv(out/'classification.csv', dataset['records'], fields)
    # This is a triage queue, not a demand that the user manually visit every suggested item.
    write_csv(out/'review_queue.csv', [r for r in dataset['records'] if not r['probe_allowed']
              and (r.get('previous_review') or {}).get('status') != 'excluded'], fields)
    if not include_html:
        return
    esc = lambda x: html.escape(str(x if x is not None else ''))
    labels={'guarded_effect':'요청·저장 차단(효과 미확정)','local_ui_changed':'격리 UI 변화 관측',
            'element_not_found':'대상 요소 못 찾음','identity_changed':'요소 정보 변경',
            'element_ambiguous':'동일 요소 여러 개','error':'접속·검사 오류','blocked_page':'차단 화면',
            'site_skipped_after_block':'차단 후 생략','runtime_policy_skipped':'실행 정책상 보류',
            'not_actionable':'숨김·비활성','no_observed_change':'격리 환경에서 변화 미관측'}
    labels.update({'preflight_unavailable':'페이지 사전 점검 실패','navigation_error':'문서 접속 오류',
                   'document_not_ready':'본문 준비 실패','redirected_away':'원래 페이지에서 다른 화면으로 이동',
                   'page_error':'오류 페이지','page_circuit_open':'동일 페이지 실패 후 생략',
                   'element_precheck_skipped':'동일 요소 실패 확인 후 다른 키 생략'})
    def display(r,k):
        value=str(r.get(k) or '')
        if k=='probe_summary':
            for term,label in sorted(labels.items(),key=lambda p:-len(p[0])):value=value.replace(term,label)
        return value
    body = ''.join('<tr>' + ''.join(f'<td>{esc(display(r,k))}</td>' for k in
                     ['id','site','action_name','rule_id','suggested_c','queue','probe_summary','reason']) + '</tr>' for r in dataset['records'])
    s=dataset['summary']
    outcome_text=' · '.join(f"{labels.get(k,k)} {count}건" for k,count in s.get('inspection_row_outcomes',{}).items())
    comparison=s.get('comparison');comparison_html=''
    if comparison:
        names={'keyboard_rows':'키 입력 전달 대상','ui_rows':'팝업·메뉴·입력값 변화 관측','popup_attempt_rows':'창 열기 시도 관측','page_loads':'페이지 로딩 횟수','duration_seconds':'실행 시간(초)'}
        cells=''.join(f'<tr><td>{esc(title)}</td><td>{esc(comparison["before"][key])}</td><td>{esc(comparison["after"][key])}</td></tr>' for key,title in names.items())
        comparison_html=f'<h2>보완 전후 비교</h2><table><tr><th>항목</th><th>이전</th><th>보완 후</th></tr>{cells}</table><p>{esc(comparison["scope"])}</p>'
        (out/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
    report=f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>파일럿 자동 검사 결과</title><style>body{{font:15px/1.6 system-ui,sans-serif;color:#203548;margin:32px}}h1{{font-size:25px}}
.note{{background:#fff3da;padding:16px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #dce4eb;vertical-align:top}}th{{background:#17344d;color:white}}td:nth-child(3){{max-width:340px;overflow-wrap:anywhere}}input{{padding:10px;width:min(500px,90%)}}</style>
<h1>파일럿 자동 검사·분류</h1><p>기준 행 {s['rows']}건 · 기존 제외 이력 {s.get('previous_exclusions',0)}건 보존 · 실제 검사 계획 대상 {s.get('planned_rows',0)}건 · 키별 계획 {len(plan['jobs'])}개</p>
<p>관측 연결 {s.get('rows_with_observations',0)}건 · 실제 키 입력을 관측한 대상 {s.get('rows_with_key_input',0)}건</p>
<p>팝업·메뉴·입력 변화 근거를 관측한 대상 {s.get('rows_with_ui_observation',0)}건 · 사전 점검 페이지 {s.get('preflight_pages',0)}개</p>
<p>{esc(outcome_text)}</p>
{comparison_html}
<p class="note">C 제안은 최종 판정이 아닙니다. 기존 검토 이력은 보존하며 덮어쓰지 않습니다. 이 도구는 위험 셀을 자동 확정하지 않습니다.
일반 UI는 네트워크를 격리해 관측합니다. 차단된 요청이 있으면 실제 효과는 미확인입니다.</p>
<p>분류: classification.csv · 검사 계획: probe_plan.json · 보류 사유: review_queue.csv<br>실제 키 검사 결과는 probe_results.jsonl에 별도로 기록됩니다.</p>
<label>검색 <input id="filter" placeholder="사이트, 문구, 규칙 검색"></label>
<table><thead><tr><th>ID</th><th>사이트</th><th>동작</th><th>규칙</th><th>C 제안</th><th>다음 처리</th><th>자동 검사 관측</th><th>근거</th></tr></thead><tbody>{body}</tbody></table>
<script>document.querySelector('#filter').addEventListener('input',e=>{{for(const r of document.querySelectorAll('tbody tr'))r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase());}});</script></html>'''
    (out/'report.html').write_text(report,encoding='utf-8')
