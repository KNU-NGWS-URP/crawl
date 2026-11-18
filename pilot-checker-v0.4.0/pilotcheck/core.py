from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .context import Document
from . import VERSION

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
PATHS = {'버튼 포커스 후 Enter/Space': 'focused-control', 'Enter 제출': 'enter-submit',
         '단축키 핸들러': 'hotkey', '키 미확인 핸들러': 'unknown', 'accesskey': 'accesskey'}
RISK = re.compile(r'buy\s*now|add\s*to\s*(cart|bag)|checkout|place.*order|subscribe|unsubscribe|'
                  r'delete|remove|send|publish|transfer|purchase|do not sell|'
                  r'구매하기|바로구매|결제|주문하기|삭제|전송|구독|송금', re.I)
EMAIL = re.compile(r'email|e-mail|이메일', re.I)
AUTH = re.compile(r'sign\s*in|log\s*in|password|로그인|비밀번호|登录|continue with', re.I)
ACTION_URL = re.compile(r'/(?:logout|delete|remove|unsubscribe|subscribe|checkout|pay|purchase|'
                        r'redeem|transfer|confirm|addtocart|add-to-cart|signup|optout)(?:[/?.-]|$)|'
                        r'/intent/(?:tweet|post)|[?&](?:action|do)=(?:delete|remove|pay|subscribe)', re.I)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded(base: Path, relative: str) -> Path:
    path = (base / relative.replace('\\', '/')).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f'근거 경로가 데이터 폴더 밖을 가리킴: {relative}')
    return path


def read_xlsx(path: Path, name: str) -> list[dict]:
    """Read only. Preserve stored values, never recalculate or modify the source XLSX."""
    with zipfile.ZipFile(path) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(t.text or '' for t in si.findall('.//m:t', NS))
                       for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS)]
        sheets = ET.fromstring(z.read('xl/workbook.xml')).find('m:sheets', NS)
        match = next((s for s in sheets if s.get('name') == name), None)
        if match is None:
            raise ValueError(f'시트 없음: {name}')
        rid = match.get('{%s}id' % NS['r'])
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        target = next(r.get('Target') for r in rels if r.get('Id') == rid)
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        parsed = []
        for row in ET.fromstring(z.read(target)).findall('m:sheetData/m:row', NS):
            values = {}
            for cell in row.findall('m:c', NS):
                col = re.sub(r'\d', '', cell.get('r'))
                val = cell.find('m:v', NS)
                v = val.text if val is not None else None
                if cell.get('t') == 's' and v is not None:
                    v = strings[int(v)]
                elif cell.get('t') == 'inlineStr':
                    v = ''.join(t.text or '' for t in cell.findall('.//m:t', NS))
                values[col] = v
            parsed.append((int(row.get('r')), values))
        if not parsed:
            return []
        headers = parsed[0][1]
        return [dict(_excel_row=i, **{h: values.get(c) for c, h in headers.items() if h})
                for i, values in parsed[1:]]


def resolve(data: Path, ref: str, cache: dict) -> tuple[dict, dict]:
    file, sep, ptr = ref.partition('#')
    path = bounded(data, file)
    if not sep or not path.is_file():
        raise ValueError('원시 파일 또는 포인터 없음')
    if str(path) not in cache:
        cache[str(path)] = json.loads(path.read_text(encoding='utf-8-sig'))
    raw = cache[str(path)]
    obj = raw
    # Match the ENTIRE pointer; reject partial or unsupported expressions.
    parts = ptr.split('.')
    for part in parts:
        m = re.fullmatch(r'(\w+)(?:\[(\d+|\w+=[^\]]+)\])?', part)
        if not m:
            raise ValueError(f'지원하지 않는 포인터: {ptr}')
        key, index = m.groups()
        obj = obj[key]
        if index is not None:
            if '=' in index:
                field, value = index.split('=', 1)
                hits = [x for x in obj if str(x.get(field)) == value]
                if len(hits) != 1:
                    raise ValueError('seq 포인터가 유일하지 않음')
                obj = hits[0]
            else:
                obj = obj[int(index)]
    if not isinstance(obj, dict):
        raise ValueError('근거가 요소 객체가 아님')
    return obj, raw


def normalize_text(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def split_keys(value) -> list[str]:
    # Vertical bars in Tier 1 mean alternatives, never a macro sequence.
    allowed = {'Enter': 'Enter', 'Space': 'Space', 'Escape': 'Escape'}
    return list(dict.fromkeys(allowed[k.strip()] for k in str(value or '').split('|')
                              if k.strip() in allowed))


def classify(element: dict, row: dict, records: list[dict]) -> dict:
    """Suggestions never become confirmed findings merely because a rule matched."""
    text = normalize_text(row['action_name'])
    fields = element.get('blocking_fields') or []
    field_text = ' '.join(str(f.get(k) or '') for f in fields for k in ('id', 'name', 'type'))
    submit = element.get('default_button') or {}
    context = ' '.join([text, field_text, str(submit.get('text') or ''),
                        str(element.get('id') or ''), str(element.get('aria_label') or '')])
    label = str(element.get('text') or '') + ' ' + str(element.get('aria_label') or '')
    href = element.get('href') or ''
    stored = element.get('stored_context') or {}
    attrs = (stored.get('attrs') or {}) if stored.get('matched') else (stored.get('consensus_attrs') or {})
    context_supported = stored.get('matched') or bool(stored.get('consensus_attrs'))
    handler = row['reach_path'] in ('hotkey', 'unknown') or bool(element.get('handler_event'))
    base = {'suggested_c': 'unknown', 'consequence_tier': 'unknown',
            'confirmation_tier': 'unknown', 'reachability': 'unknown',
            'danger_status': 'unresolved', 'is_danger_cell': None,
            'rule_id': 'unresolved', 'reason': '실제 효과를 확정할 근거 부족',
            'probe_allowed': False, 'queue': 'effect_review'}
    search_fields = any(re.fullmatch(r'q|sitem|_nkw|searchTerm|field-keywords|header-search',
                                    str(f.get('name') or ''), re.I) for f in fields)
    # Some JS search forms have no action; their default action is the current
    # login/home page. A matched search landmark plus a search field is stronger
    # context than the URL's incidental "login" word. Submission is unverified.
    if (row['reach_path'] == 'enter-submit' and stored.get('matched')
            and attrs.get('role') == 'search' and search_fields
            and not any(EMAIL.search(str(f.get('name') or '')) or f.get('type') == 'password' for f in fields)):
        return {**base, 'rule_id': 'search-landmark-form', 'suggested_c': 'C1',
                'queue': 'sample_review', 'reason': '동일 폼의 저장 HTML role=search와 검색어 필드를 함께 확인. URL의 login 단어와 구분하며 실제 Enter 처리·도달성은 미검증'}
    # A key callback's target may be a link, but its effect is not the href effect.
    # Keep known account-menu probes available without labeling the callback C1.
    if handler and element.get('id') != 'account-menu-account-button':
        display_context = (element.get('id') in ('autocomplete-clear-button', 'autocomplete-search-button', 'site_search_survey_launch_button')
                           or (attrs.get('data-brix-component-name') == 'CheckboxLabel'
                               and any(a.get('tag') == 'fieldset' for a in stored.get('ancestors', []))))
        isolated = bool(stored.get('matched') and display_context and not element.get('within_form')
                        and element.get('type') != 'submit' and not RISK.search(context))
        return {**base, 'rule_id': 'delegated-handler', 'queue': 'ui_probe' if isolated else 'effect_review',
                'probe_allowed': isolated,
                'reason': '키 처리 함수의 호출은 기록됐으나 호출 대상의 최종 효과는 미확인. 대상 문구·href로 콜백 결과를 대신 판정하지 않음'}
    if element.get('tag') == 'a' and href.startswith(('http://', 'https://')):
        if ACTION_URL.search(href) or 'ca_donotsell' in href.lower():
            return {**base, 'rule_id': 'effectful-link', 'queue': 'controlled_tier2',
                    'reason': '실행·개인정보 선택 경로. 링크 진입의 상태 변경 여부를 별도 확인'}
        if attrs.get('onclick') or any((r.get('aux') or {}).get('onclick_attr') for r in records):
            return {**base, 'rule_id': 'scripted-link', 'reason': '인라인 클릭 처리 있음'}
        return {**base, 'rule_id': 'navigation-link', 'suggested_c': 'C1',
                'queue': 'sample_review',
                'reason': '명시적 href의 화면 이동 범위에 한정한 C1 제안. buy/share/clear 문구로 실행 효과를 추정하지 않으며 위임 처리·키 발동은 미검증'}
    if EMAIL.search(field_text) and (re.search(r'sign\s*up|subscribe|구독', context, re.I)
                                    or not AUTH.search(context)):
        return {**base, 'rule_id': 'email-subscription', 'queue': 'subscription',
                'reason': '이메일 입력 폼: GET·동일 페이지 action만으로 화면 이동이라 판정하지 않음'}
    if re.search(r'sign\s*up|구독', text, re.I) and row['reach_path'] == 'focused-control':
        return {**base, 'rule_id': 'signup-or-subscription', 'queue': 'subscription',
                'reason': '가입·구독 제출 가능성: 상태 변경·복원·확인 절차 검토 필요'}
    if RISK.search(context) or ACTION_URL.search(href) or 'ca_donotsell' in href.lower():
        if (context_supported and attrs.get('data-testid') == 'hri-disclaimer-modal-link'
                and element.get('role') == 'button' and not element.get('within_form')):
            return {**base, 'rule_id': 'terms-dialog', 'suggested_c': 'C1',
                    'probe_allowed': bool(stored.get('matched')), 'queue': 'ui_probe' if stored.get('matched') else 'sample_review',
                    'reason': '동일 요소의 저장 HTML에서 약관 모달 식별자 확인. 할인·checkout 문구와 실행 동작을 구분'}
        return {**base, 'rule_id': 'sensitive-action', 'queue': 'controlled_tier2',
                'reason': '결제·전송·삭제·설정 변경 가능성. 자동 활성화하지 않음'}
    if AUTH.search(context) or element.get('id') == 'account-menu-account-button':
        # No credentials, SSO, authorization, or login submissions are automated.
        menu = (element.get('id') == 'account-menu-account-button'
                and element.get('type') == 'button' and not element.get('within_form'))
        return {**base, 'rule_id': 'authentication', 'queue': 'auth_review',
                'suggested_c': 'C1' if menu and not handler else 'unknown', 'probe_allowed': menu,
                'reason': '로그인·권한 문맥. 계정 메뉴 열기만 격리 재현 가능; 인증 제출은 제외'}
    if row['reach_path'] == 'enter-submit':
        action = element.get('action') or ''
        search = re.search(r'(^|/)(search|s|sch|find)(/|$)', urlsplit(action).path, re.I)
        if element.get('method', '').lower() == 'get' and search and search_fields:
            return {**base, 'rule_id': 'search-form', 'suggested_c': 'C1',
                    'probe_allowed': True, 'queue': 'ui_probe',
                    'reason': '검색 경로와 검색어 필드 확인. Enter 발동은 별도 격리 검사'}
        return {**base, 'rule_id': 'unknown-form', 'reason': 'GET 폼도 지속 변경 가능; 자동 제외하지 않음'}
    if row['reach_path'] == 'accesskey' and element.get('tag') == 'input':
        if not (re.search(r'(^|/)(search|s|sch|find)(/|$)', urlsplit(element.get('form_action') or '').path, re.I)
                and re.fullmatch(r'q|sitem|searchTerm|field-keywords|header-search', str(element.get('name') or ''), re.I)):
            return {**base, 'rule_id': 'unverified-accesskey', 'queue': 'sample_review',
                    'reason': '입력칸 접근키만 확인됨. 검색 문맥과 활성화 효과가 불명확하여 보류'}
        return {**base, 'rule_id': 'input-accesskey', 'suggested_c': 'C1',
                'reason': '입력칸 포커스 후보. 브라우저별 조합키 검증은 별도', 'queue': 'sample_review'}
    if re.search(r'clear.*search|search.*clear|검색.*지우|검색.*삭제', context, re.I) or element.get('id') == 'autocomplete-clear-button':
        return {**base, 'rule_id': 'clear-search', 'suggested_c': 'C1', 'probe_allowed': True,
                'queue': 'ui_probe', 'reason': '검색 입력 지우기 UI를 격리 검사'}
    if element.get('id') == 'autocomplete-search-button':
        return {**base, 'rule_id': 'search-control', 'suggested_c': 'C1', 'probe_allowed': True,
                'queue': 'ui_probe', 'reason': '검색 컨트롤을 격리 검사'}
    if (context_supported and attrs.get('data-testid') == 'hri-disclaimer-modal-link'
            and element.get('role') == 'button' and not element.get('within_form')):
        return {**base, 'rule_id': 'terms-dialog', 'suggested_c': 'C1', 'probe_allowed': True,
                'queue': 'ui_probe', 'reason': '저장 HTML의 약관 모달 식별자로 설명 표시 동작을 구분'}
    if (stored.get('matched') and element.get('tag') == 'button' and not element.get('within_form')
            and element.get('type') != 'submit'
            and re.fullmatch(r'badge for .+', attrs.get('title') or '', re.I)):
        return {**base, 'rule_id': 'badge-description', 'suggested_c': 'C1',
                'probe_allowed': bool(stored.get('matched')), 'queue': 'ui_probe' if stored.get('matched') else 'sample_review',
                'reason': '동일 요소의 badge 설명 속성을 확인. 상호의 Buy를 구매 동작으로 해석하지 않음. 설명·표시 UI 범위의 제안'}
    if (element.get('tag') in ('button', 'a', 'div')
            and element.get('type') != 'submit' and not element.get('within_form')
            and element.get('css_path') and (element.get('text') or element.get('aria_label'))):
        return {**base, 'rule_id': 'isolated-control', 'probe_allowed': True,
                'queue': 'ui_probe', 'reason': '제출·인증·민감 동작으로 식별되지 않은 요소. 외부 요청을 막은 격리 관측만 수행'}
    return base


def load_dataset(data: Path, baseline: Path | None, ledger: Path | None = None) -> dict:
    data = data.resolve()
    all_records = [json.loads(line) for line in (data / 'records/records.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    labels_path = data / 'labels/label_sheet.xlsx'
    label_rows = read_xlsx(labels_path, 'labels') if labels_path.exists() else []
    index = defaultdict(list)
    for r in all_records:
        key = f"{r['site']}|{r['action_id']}|{r['reach_path']}|{r.get('output_sequence') or ''}"
        index[key].append(r)
    if not label_rows:
        for key, group in index.items():
            r = group[0]
            label_rows.append({'row_key': key, 'site': r['site'], 'views': r['view'],
                               'reach_path': r['reach_path'], 'output_sequence': r.get('output_sequence'),
                               'action_name': r['action_name'], 'snapshot': f"snapshots/{r['site']}/{r['view']}.png",
                               'evidence_ref_t1': r['evidence_ref']})
    if baseline:
        source = [r for r in read_xlsx(baseline, '후보 동작') if r.get('사이트') and r.get('서비스')]
        rows = [{'id': f'S3-{i:03d}', 'site': r['사이트'], 'action_name': r['동작(버튼·폼 문구)'],
                 'output_sequence': r.get('출력 키') or '', 'reach_path': PATHS[r['키보드 경로']],
                 'snapshot': r['스냅샷'], 'baseline_row': r['_excel_row'], 'baseline': r}
                for i, r in enumerate(source, 1)]
    else:
        rows = [{'id': f"R-{hashlib.sha256(str(r['row_key']).encode()).hexdigest()[:12]}",
                 **{k: r.get(k) for k in ('site', 'action_name', 'output_sequence', 'reach_path', 'snapshot')},
                 'row_key': r['row_key']} for r in label_rows]
    coverage = {}
    cp = data / 'report/coverage_matrix.csv'
    if cp.exists():
        for r in csv.DictReader(cp.open(encoding='utf-8-sig')):
            coverage.update({(r['site'], k): v for k, v in r.items() if k not in ('site', 'n_ok_views')})
    prior = {}
    if ledger:
        ld = json.loads(ledger.read_text(encoding='utf-8-sig'))
        if not isinstance(ld, dict) or not ('results' in ld or 'records' in ld):
            raise ValueError('review-ledger는 results/records 검토 이력 형식이어야 합니다. 관찰 요약은 별도 대장을 사용하세요.')
        ledger_hash = ld.get('sha256') or ld.get('metadata', {}).get('source_sha256')
        if not baseline or ledger_hash != digest(baseline):
            raise ValueError('검토 이력과 기준 엑셀 해시 불일치. 이력을 적용하지 않습니다.')
        prior_rows = ld.get('results', ld.get('records', []))
        if len({r['n'] for r in prior_rows}) != len(prior_rows):
            raise ValueError('검토 이력 행 ID 중복')
        prior = {r['n']: r for r in prior_rows}
    cache = {}; html_cache = {}; results = []
    for pos, row in enumerate(rows, 1):
        pool = [l for l in label_rows if l['site'] == row['site']
                and l['reach_path'] == row['reach_path']
                and (l.get('output_sequence') or '') == (row.get('output_sequence') or '')
                and l.get('snapshot') == row.get('snapshot')]
        exact = [l for l in pool if l['action_name'] == row['action_name']]
        matches = exact or [l for l in pool if str(l['action_name']).startswith(row['action_name'])]
        evidence = []; issues = []
        for l in matches:
            try:
                obj, raw = resolve(data, l['evidence_ref_t1'], cache)
                view = raw['meta'].get('view')
                status = coverage.get((row['site'], view), raw['meta'].get('status'))
                m = re.match(r'frames\[(\d+)\]', l['evidence_ref_t1'].partition('#')[2])
                frame = raw['frames'][int(m[1])] if m else {}
                if obj.get('target_id') is not None:
                    target = ((frame.get('handlers') or {}).get('targets') or {}).get(str(obj['target_id']))
                    if target and target.get('kind') == 'element':
                        obj = {**target, **obj, 'handler_event': obj.get('type'), 'type': target.get('type')}
                # Stored snapshot context is read-only and matched by exact selector/signature.
                snap = data / 'snapshots' / row['site'] / f'{view}.html'
                stored_context = {'matched': False, 'issue': 'snapshot unavailable'}
                if snap.is_file():
                    if str(snap) not in html_cache:
                        html_cache[str(snap)] = Document(snap.read_text(encoding='utf-8-sig'))
                    stored_context = html_cache[str(snap)].context(obj)
                    stored_context.update(source=str(snap), sha256=digest(snap))
                obj = {**obj, 'stored_context': stored_context}
                evidence.append({'ref': l['evidence_ref_t1'], 'raw_sha256': digest(bounded(data, l['evidence_ref_t1'].split('#')[0])),
                                 'row_key': l['row_key'], 'element': obj, 'view': view, 'status': status,
                                 'locale': raw['meta'].get('locale') or 'en-US',
                                 'url': raw['meta'].get('requested_url'), 'frame_url': (frame.get('dom') or {}).get('url'),
                                 'frame_is_top': bool(frame.get('is_top')), 'records': index.get(l['row_key'], [])})
            except (ValueError, KeyError, IndexError, OSError, json.JSONDecodeError) as exc:
                issues.append(str(exc))
        decisions = [classify(e['element'], row, e['records']) for e in evidence]
        if not evidence or issues or any(e['status'] != 'ok' for e in evidence):
            decision = classify({}, {'action_name': '', 'reach_path': 'unknown'}, [])
            decision.update(rule_id='evidence-gap', queue='evidence_review', reason='연결·수집 상태 확인 필요')
        elif len({(d['rule_id'], d['suggested_c'], d['probe_allowed']) for d in decisions}) != 1:
            decision = {**decisions[0], 'rule_id': 'ambiguous-elements', 'suggested_c': 'unknown',
                        'probe_allowed': False, 'reason': '동일 문구의 요소들이 다른 판정을 가짐', 'queue': 'evidence_review'}
        else:
            decision = decisions[0]
        # Previous results are provenance, NOT machine-readable gold labels or validated rule approval.
        old = prior.get(pos)
        if old and (old.get('site') != row['site'] or old.get('name') != row['action_name']):
            raise ValueError(f'검토 이력 행 식별 불일치: {row["id"]}')
        history = {'c': old['c'], 'status': old['status'], 'reason': old['reason']} if old else None
        questions = {
            'subscription': '이메일 구독인지, 정상 입력 후 등록·확인 이메일·해제 경로가 있는지 확인',
            'auth_review': '최초 로그인 화면 열기와 실제 인증·권한 부여를 구분해 확인',
            'controlled_tier2': '중대한 효과 전 추가 승인·인증과 중단 지점을 통제 환경에서 확인',
            'sample_review': '표본에서 명시적 목적과 실제 동작이 일치하는지 확인',
            'evidence_review': '연결된 원자료·화면 상태·동일 문구의 요소를 먼저 확인',
            'effect_review': '안내·화면 변화인지, 지속 저장·외부 전달인지 확인',
            'ui_probe': '자동 키 검사 결과부터 확인. 같은 UI를 먼저 수동 반복하지 않음',
        }
        results.append({**row, **decision, 'mapping': 'exact' if exact else 'prefix' if matches else 'missing',
                        'issues': issues, 'evidence': evidence, 'previous_review': history,
                        'url': evidence[0]['url'] if evidence else None,
                        'question': questions[decision['queue']],
                        'stop_line': '실제 결제·외부 전송·삭제·계정 동의 완료 전 중단'})
    return {'schema_version': 1, 'tool_version': VERSION, 'data_dir': str(data), 'baseline': str(baseline) if baseline else None,
            'baseline_sha256': digest(baseline) if baseline else None, 'records': results,
            'summary': {'rows': len(results), 'rules': dict(Counter(r['rule_id'] for r in results)),
                        'suggested_c': dict(Counter(r['suggested_c'] for r in results)),
                        'auto_probe_candidates': sum(r['probe_allowed'] for r in results),
                        'previous_exclusions': sum((r.get('previous_review') or {}).get('status') == 'excluded' for r in results),
                        'prior_working_c1': sum((r.get('previous_review') or {}).get('c') == 'C1' for r in results),
                        'final_confirmed': 0}}


def build_plan(dataset: dict, audit_previous: bool = False) -> dict:
    jobs = {}; skipped = []
    for row in dataset['records']:
        previous = row.get('previous_review') or {}
        if (not audit_previous and previous.get('status') in ('excluded', 'working_label')
                and previous.get('c') == 'C1' and row.get('suggested_c') == 'C1'):
            skipped.append({'id': row['id'], 'reason': '기존 검토 완료 이력 보존. 표본 재검사는 audit_previous로 별도 계획', 'queue': 'previous_review'})
            continue
        if not row['probe_allowed']:
            skipped.append({'id': row['id'], 'reason': row['reason'], 'queue': row['queue']})
            continue
        target_identities = set()
        for evidence in row['evidence']:
            element = evidence['element']
            pointer = evidence['ref'].partition('#')[2]
            frame = re.match(r'frames\[\d+\]', pointer)
            target_identities.add((evidence['url'], evidence.get('frame_url'),
                                   evidence['ref'].partition('#')[0], frame[0] if frame else '',
                                   element.get('css_path')))
        if len(target_identities) > 1:
            skipped.append({'id': row['id'], 'reason': '원본 행이 서로 다른 요소·프레임 후보에 연결됨. 대표 타깃을 임의 선택하지 않음',
                            'queue': 'target_identity_review'})
            continue
        for e in row['evidence']:
            el = e['element']; focus_el = el; selector = el.get('css_path')
            if row['reach_path'] == 'enter-submit':
                fields = [f for f in el.get('blocking_fields', []) if f.get('visible') and f.get('type') != 'password']
                selector = fields[0].get('css_path') if len(fields) == 1 else None
                focus_el = fields[0] if len(fields) == 1 else el
            if not selector or el.get('visible') is False or el.get('disabled'):
                skipped.append({'id': row['id'], 'reason': '요소 숨김·비활성 또는 단일 입력 포커스 불명확', 'queue': 'focus_review'})
                continue
            for key in split_keys(row.get('output_sequence')):
                item = {'url': e['url'], 'selector': selector, 'key': key, 'rule_id': row['rule_id'],
                        'locale': e.get('locale') or 'en-US',
                        'frame_url': e['frame_url'], 'frame_is_top': e['frame_is_top'],
                        'expected': {k: focus_el.get(k) for k in ('tag', 'id', 'name', 'type', 'text', 'aria_label', 'href')},
                        'source_ref': e['ref'], 'source_sha256': e['raw_sha256'], 'members': [row['id']]}
                # The key includes the signature; a changed element never reuses a previous test.
                identity = hashlib.sha256(json.dumps({k: v for k, v in item.items() if k != 'members'}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
                if identity in jobs:
                    jobs[identity]['members'] = sorted(set(jobs[identity]['members'] + item['members']))
                else:
                    jobs[identity] = {'job_id': identity, **item}
        if row['probe_allowed'] and not split_keys(row.get('output_sequence')):
            skipped.append({'id': row['id'], 'reason': '출력 키 미확인 또는 지원 범위(Enter/Space/Escape) 밖', 'queue': 'key_review'})
    return {'schema_version': 1, 'mode': 'isolated-ui', 'baseline_sha256': dataset['baseline_sha256'],
            'jobs': list(jobs.values()), 'skipped': skipped}


def merge_observations(dataset: dict, plan: dict, run_dir: Path):
    """Attach only matching, complete-run evidence. Never turn a blocked effect into success."""
    manifest = run_dir / 'probe_plan.json'
    summary = json.loads((run_dir / 'summary.json').read_text(encoding='utf-8'))
    if summary.get('completed') is False:
        raise ValueError('중단된 검사 실행입니다. 완료된 실행만 판정 자료에 연결합니다.')
    observed_plan = json.loads(manifest.read_text(encoding='utf-8'))
    if (summary.get('plan_sha256') != digest(manifest)
            or observed_plan.get('baseline_sha256') != dataset['baseline_sha256']):
        raise ValueError('검사 실행의 계획 해시 또는 기준 데이터가 다름')
    jobs = {j['job_id']: j for j in plan['jobs']}
    expected = {j['job_id']: j for j in observed_plan['jobs']}
    if any(key not in jobs or jobs[key] != job for key, job in expected.items()):
        raise ValueError('원자료·계획이 바뀌어 검사 결과를 적용할 수 없음')
    observations = [json.loads(line) for line in (run_dir/'probe_results.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    observations = [normalize_ui_observation(o,run_dir) for o in observations]
    grouped = defaultdict(list)
    for obs in observations:
        if obs.get('job_id') not in expected:
            raise ValueError('검사 결과의 job_id 또는 키 불일치')
        if obs.get('key') is None and not obs.get('key_dispatched') and obs.get('status') in {
                'site_paused_after_preflight_failure','site_skipped_after_block','preflight_unavailable',
                'page_circuit_open','element_precheck_skipped'}:
            obs = {**obs, 'key': expected[obs['job_id']]['key'],
                   'key_provenance': 'manifest planned key only; not dispatched'}
        if obs.get('key') != expected[obs['job_id']]['key']:
            raise ValueError('검사 결과의 job_id 또는 키 불일치')
        grouped[obs['job_id']].append(obs)
    for row in dataset['records']:
        row['observations'] = []
        for job_id, values in grouped.items():
            if row['id'] not in expected[job_id]['members']:
                continue
            consistent = len({v.get('attempt') for v in values}) >= 2 and len({v['status'] for v in values}) == 1
            row['observations'].append({'job_id': job_id, 'key': expected[job_id]['key'],
                                        'consistent': consistent, 'attempts': values})
        # UI observations reduce repeated data collection, not the evidence threshold for C/V.
        if row['observations']:
            row['queue'] = 'observation_review'
            statuses = {a['status'] for o in row['observations'] for a in o['attempts']}
            row['probe_outcome'] = next(iter(statuses)) if len(statuses) == 1 else 'mixed'
            row['probe_summary'] = ' / '.join(
                f"{o['key']}: {','.join(dict.fromkeys(a['status'] for a in o['attempts']))} ({len(o['attempts'])}회)"
                for o in row['observations'])
            row['question'] = '자동 관측과 전후 화면을 먼저 검토. 요소 변경·차단·요청 차단이면 해당 조건을 해소하기 전 최종 판정하지 않음'
    dataset['summary']['rows_with_observations'] = sum(bool(r.get('observations')) for r in dataset['records'])
    dataset['summary']['inspection_statuses'] = dict(Counter(o['status'] for o in observations))
    dataset['summary']['inspection_row_outcomes'] = dict(Counter(r['probe_outcome'] for r in dataset['records'] if r.get('probe_outcome')))
    dataset['summary']['rows_with_key_input'] = len({m for o in observations if o.get('key_dispatched') for m in o.get('members',[])})
    dataset['summary']['rows_with_ui_observation'] = len({m for o in observations if o.get('ui_observed') for m in o.get('members',[])})
    dataset['summary']['preflight_pages'] = len(summary.get('preflights',[]))
    dataset['summary']['preflight_statuses'] = dict(Counter(p['status'] for p in summary.get('preflights',[])))
    dataset['summary']['rows_with_popup_attempt'] = len({m for o in observations if any(g.get('type')=='popup' for g in o.get('guards',[])) for m in o.get('members',[])})


def input_value_changed(before: list, after: list) -> bool:
    values={item.get('id') or f'@{index}':item.get('value') for index,item in enumerate(before)}
    return any((item.get('id') or f'@{index}') in values and values[item.get('id') or f'@{index}']!=item.get('value') for index,item in enumerate(after))


def normalize_ui_observation(observation: dict, run_dir: Path) -> dict:
    """Recompute UI evidence from saved before/after states; preserve raw run logs."""
    result=dict(observation)
    for file in observation.get('artifacts',[]):
        p=Path(file).resolve()
        if not p.is_relative_to(run_dir.resolve()):
            raise ValueError('관측 파일이 실행 폴더 밖에 있음')
        if not p.name.endswith('-observation.json') or not p.is_file():continue
        detail=json.loads(p.read_text(encoding='utf-8'))
        before,after=detail.get('before'),detail.get('after')
        if before and after:
            evidence={'new_dialogs':[x for x in after.get('dialogs',[]) if x not in before.get('dialogs',[])],
                      'new_menus':[x for x in after.get('menus',[]) if x not in before.get('menus',[])],
                      'input_change':input_value_changed(before.get('inputs',[]),after.get('inputs',[]))}
            evidence['frame_closed']=detail.get('frameVisibleBefore') is True and detail.get('frameVisibleAfter') is False
            outer_before=detail.get('outerBefore') or {};outer_after=detail.get('outerAfter') or {}
            evidence['closed_dialogs']=[x for x in outer_before.get('dialogs',[]) if x not in outer_after.get('dialogs',[])] if detail.get('outerAfter') is not None else []
            old_types={x['key']:x['type'] for x in before.get('field_types',[])}
            evidence['field_type_changes']=[{'key':x['key'],'from':old_types[x['key']],'to':x['type']} for x in after.get('field_types',[]) if x['key'] in old_types and old_types[x['key']]!=x['type']]
            result.update(ui_evidence=evidence,ui_observed=bool(evidence['new_dialogs'] or evidence['new_menus'] or evidence['input_change'] or evidence['frame_closed'] or evidence['closed_dialogs'] or evidence['field_type_changes']),ui_measurement='common-input-values; visible-dialog/menu delta; frame visibility; field types')
    return result


def compare_runs(before_dir: Path,after_dir: Path) -> dict:
    def read_run(folder):
        summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        if summary.get('completed') is False:raise ValueError('중단된 실행은 비교하지 않음')
        manifest=json.loads((folder/'probe_plan.json').read_text(encoding='utf-8'))
        obs=[normalize_ui_observation(json.loads(x),folder) for x in (folder/'probe_results.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
        return summary,manifest,obs
    b,bp,bo=read_run(before_dir);a,ap,ao=read_run(after_dir)
    if bp.get('baseline_sha256')!=ap.get('baseline_sha256') or bp['jobs']!=ap['jobs']:
        raise ValueError('같은 원자료·계획인 실행끼리만 비교할 수 있음')
    def metrics(folder,summary,obs):
        duration=summary.get('duration_ms',max(0,((folder/'summary.json').stat().st_mtime-(folder/'probe_plan.json').stat().st_mtime)*1000))
        return {'rows':len({m for o in obs for m in o.get('members',[])}),
                'keyboard_rows':len({m for o in obs if o.get('key_dispatched') for m in o.get('members',[])}),
                'ui_rows':len({m for o in obs if o.get('ui_observed') for m in o.get('members',[])}),
                'popup_attempt_rows':len({m for o in obs if any(g.get('type')=='popup' for g in o.get('guards',[])) for m in o.get('members',[])}),
                'observations':len(obs),'page_loads':sum('test_scope' in o for o in obs)+len(summary.get('preflights',[])),
                'duration_seconds':round(duration/1000,1)}
    return {'before_run':str(before_dir),'after_run':str(after_dir),'before':metrics(before_dir,b,bo),'after':metrics(after_dir,a,ao),
            'scope':'동일 19건 계획의 실행 비교. 사이트 상태·시간차를 통제한 통계적 성능 평가는 아님.'}


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fields, extrasaction='ignore'); writer.writeheader()
        for row in rows:
            def safe(value):
                value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                # Prevent untrusted site labels from becoming spreadsheet formulas.
                return "'" + value if isinstance(value, str) and (value.startswith(('\t','\r')) or value.lstrip().startswith(('=', '+', '-', '@'))) else value
            writer.writerow({k: safe(row.get(k)) for k in fields})
