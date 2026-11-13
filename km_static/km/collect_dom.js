// km/collect_dom.js
// 프레임 하나의 DOM을 "읽기만" 하여 키보드 도달 경로 후보를 수집한다.
// 클릭·포커스·키 이벤트 합성·폼 제출을 하지 않는다.
// page.evaluate / frame.evaluate 로 함수 표현식으로 실행된다: (opts) => {...}
(opts) => {
  'use strict';
  const SRC_LIMIT = (opts && opts.srcLimit) || 3000;
  const MAX_ELEMS = (opts && opts.maxElems) || 30000;
  const ACTION_KW = (opts && opts.actionKeywordRegex) ? new RegExp(opts.actionKeywordRegex, 'i') : null;

  // ---------- 공통 유틸 ----------
  function allElements() {
    // open shadow root까지 포함한 깊이 우선 순회 (closed shadow root는 접근 불가 → 한계로 기록)
    const out = [];
    const stack = [document.documentElement];
    let shadowRoots = 0;
    while (stack.length && out.length < MAX_ELEMS) {
      const el = stack.pop();
      if (!el) continue;
      out.push(el);
      if (el.shadowRoot) { shadowRoots++; for (let i = el.shadowRoot.children.length - 1; i >= 0; i--) stack.push(el.shadowRoot.children[i]); }
      for (let i = el.children.length - 1; i >= 0; i--) stack.push(el.children[i]);
    }
    return { elems: out, truncated: out.length >= MAX_ELEMS, shadowRoots };
  }

  function cssPath(el) {
    try {
      const parts = [];
      let cur = el, depth = 0;
      while (cur && cur.nodeType === 1 && depth < 12) {
        let p = cur.tagName.toLowerCase();
        if (cur.id) { p += '#' + CSS.escape(cur.id); parts.unshift(p); break; }
        const parent = cur.parentElement;
        if (parent) {
          const sib = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
          if (sib.length > 1) p += `:nth-of-type(${sib.indexOf(cur) + 1})`;
        }
        parts.unshift(p);
        cur = parent; depth++;
      }
      return parts.join(' > ');
    } catch (e) { return null; }
  }

  function isVisible(el) {
    try {
      if (!el.isConnected) return false;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    } catch (e) { return null; }
  }

  function text(el) {
    try {
      let t = '';
      if (el.tagName === 'INPUT') t = el.value || el.getAttribute('value') || el.getAttribute('alt') || '';
      else t = el.innerText || el.textContent || '';
      t = t.trim().replace(/\s+/g, ' ');
      return t.length > 120 ? t.slice(0, 120) : t;
    } catch (e) { return ''; }
  }

  function desc(el) {
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      name: el.getAttribute('name'),
      type: el.getAttribute('type'),
      role: el.getAttribute('role'),
      text: text(el),
      aria_label: el.getAttribute('aria-label'),
      title: el.getAttribute('title'),
      href: el.tagName === 'A' ? el.href : null,
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      visible: isVisible(el),
      tabindex: el.getAttribute('tabindex'),
      in_shadow: (el.getRootNode && el.getRootNode() instanceof ShadowRoot) || false,
      css_path: cssPath(el),
    };
  }

  function fnSrc(fn) {
    try {
      if (typeof fn !== 'function') return null;
      const s = Function.prototype.toString.call(fn);
      return s.length > SRC_LIMIT ? s.slice(0, SRC_LIMIT) : s;
    } catch (e) { return null; }
  }

  const { elems, truncated, shadowRoots } = allElements();

  // ---------- 1. accesskey ----------
  const accesskeys = [];
  for (const el of elems) {
    if (!el.hasAttribute || !el.hasAttribute('accesskey')) continue;
    const d = desc(el);
    d.accesskey = el.getAttribute('accesskey');
    d.accessKeyLabel = el.accessKeyLabel || null; // Chromium은 보통 undefined
    // 브라우저 기본 활성화 의미(추정): 버튼·링크는 click, 텍스트 입력은 focus
    const tag = el.tagName;
    const t = (el.type || '').toLowerCase();
    if (tag === 'A' || tag === 'BUTTON' || (tag === 'INPUT' && ['submit', 'button', 'reset', 'image', 'checkbox', 'radio'].includes(t)) || tag === 'SUMMARY')
      d.activation_semantics = 'click(estimated)';
    else if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT')
      d.activation_semantics = 'focus(estimated)';
    else if (tag === 'LABEL')
      d.activation_semantics = 'label-control(estimated)';
    else d.activation_semantics = 'unknown';
    const form = el.form || (el.closest && el.closest('form'));
    d.form_action = form ? form.action : null;
    accesskeys.push(d);
  }

  // ---------- 2. 폼과 Enter 암묵적 제출 (HTML Living Standard §4.10.21.2 implicit submission) ----------
  // 암묵적 제출을 막는 필드(blocks implicit submission): 아래 type의 input
  const BLOCKING = new Set(['text', 'search', 'url', 'tel', 'email', 'password', 'date', 'month', 'week', 'time', 'datetime-local', 'number']);
  const forms = [];
  const formEls = elems.filter(e => e.tagName === 'FORM');
  formEls.forEach((f, idx) => {
    const ctrls = Array.from(f.elements || []);
    const submitButtons = ctrls.filter(c =>
      (c.tagName === 'BUTTON' && (c.type || 'submit') === 'submit') ||
      (c.tagName === 'INPUT' && (c.type === 'submit' || c.type === 'image')));
    const defaultButton = submitButtons[0] || null; // 트리 순서상 첫 submit 버튼
    const blockingFields = ctrls.filter(c => c.tagName === 'INPUT' && BLOCKING.has((c.type || 'text').toLowerCase()));
    const visibleBlocking = blockingFields.filter(c => isVisible(c) && !c.disabled && !c.readOnly);
    const external = ctrls.filter(c => !f.contains(c)); // form= 속성으로 연결된 외부 컨트롤
    const typeless = ctrls.filter(c => c.tagName === 'BUTTON' && !c.hasAttribute('type'));

    let implicit;
    if (blockingFields.length === 0) implicit = 'no_text_field';
    else if (defaultButton && defaultButton.disabled) implicit = 'default_button_disabled';
    else if (defaultButton) implicit = 'default_button';
    else if (blockingFields.length <= 1) implicit = 'no_button_single_field';
    else implicit = 'blocked_multiple_fields';

    const autofocus = ctrls.filter(c => c.hasAttribute && c.hasAttribute('autofocus')).map(c => desc(c));
    forms.push({
      form_index: idx,
      id: f.id || null,
      name: f.getAttribute('name'),
      method: (f.method || 'get').toLowerCase(),
      action: f.action,
      target: f.getAttribute('target'),
      novalidate: f.noValidate,
      visible: isVisible(f),
      has_onsubmit_attr: f.hasAttribute('onsubmit'),
      onsubmit_attr: f.getAttribute('onsubmit'),
      n_controls: ctrls.length,
      n_blocking_fields: blockingFields.length,
      n_visible_blocking_fields: visibleBlocking.length,
      blocking_fields: blockingFields.slice(0, 10).map(desc),
      n_submit_buttons: submitButtons.length,
      default_button: defaultButton ? desc(defaultButton) : null,
      typeless_buttons: typeless.length,
      external_controls: external.length,
      autofocus,
      implicit_submission: implicit,
      css_path: cssPath(f),
    });
  });

  // 문서 전체 autofocus (폼 밖 포함)
  const autofocusAll = elems.filter(e => e.hasAttribute && e.hasAttribute('autofocus')).map(desc);
  const activeEl = document.activeElement && document.activeElement !== document.body ? desc(document.activeElement) : null;

  // ---------- 3. focused-control 후보 (액션 키워드가 붙은 포커스 가능 컨트롤) ----------
  const focusedControls = [];
  if (ACTION_KW) {
    for (const el of elems) {
      const tag = el.tagName;
      const role = el.getAttribute && el.getAttribute('role');
      const isCtrl = tag === 'BUTTON' || (tag === 'INPUT' && ['submit', 'button', 'image'].includes((el.type || '').toLowerCase())) ||
                     role === 'button' || (tag === 'A' && el.hasAttribute('href'));
      if (!isCtrl) continue;
      const label = [text(el), el.getAttribute('aria-label') || '', el.getAttribute('title') || ''].join(' ');
      if (!ACTION_KW.test(label)) continue;
      const d = desc(el);
      // role=button인 div 등은 tabindex가 없으면 Tab 포커스 불가 → 추정 기록
      d.tab_focusable_estimated = (tag === 'BUTTON' || tag === 'INPUT' || (tag === 'A')) ? (el.tabIndex >= 0) : (el.hasAttribute('tabindex') && el.tabIndex >= 0);
      d.within_form = !!(el.form || (el.closest && el.closest('form')));
      focusedControls.push(d);
      if (focusedControls.length > 500) break;
    }
  }

  // ---------- 4. data-hotkey 등 선언형 단축키 속성 ----------
  const declarative = [];
  const DECL_ATTRS = ['data-hotkey', 'data-shortcut', 'data-keyboard-shortcut', 'aria-keyshortcuts', 'data-key', 'data-keybinding'];
  for (const el of elems) {
    for (const a of DECL_ATTRS) {
      if (el.hasAttribute && el.hasAttribute(a)) {
        const d = desc(el); d.attr = a; d.value = el.getAttribute(a);
        declarative.push(d);
      }
    }
  }

  // ---------- 5. 인라인 핸들러 속성 (onkeydown="...") ----------
  const inlineHandlers = [];
  const INLINE = ['onkeydown', 'onkeyup', 'onkeypress', 'onsubmit'];
  for (const el of elems) {
    for (const a of INLINE) {
      if (el.hasAttribute && el.hasAttribute(a)) {
        const d = desc(el); d.attr = a;
        const v = el.getAttribute(a) || '';
        d.code = v.length > SRC_LIMIT ? v.slice(0, SRC_LIMIT) : v;
        inlineHandlers.push(d);
      }
    }
  }
  for (const a of INLINE) {
    if (document.body && document.body.getAttribute(a) === null && typeof window[a] === 'function') {
      inlineHandlers.push({ tag: 'window', attr: a, code: fnSrc(window[a]) });
    }
  }

  // ---------- 6. 프레임워크 위임 핸들러 (React props / Vue3 _vei / jQuery events) ----------
  // React·jQuery는 루트/문서에 단일 디스패처만 네이티브 등록하므로, 실제 핸들러는 여기서 보충한다.
  const framework = [];
  const REACT_KEYS = ['onKeyDown', 'onKeyUp', 'onKeyPress', 'onKeyDownCapture', 'onKeyUpCapture', 'onSubmit'];
  let reactDetected = false, vueDetected = false;
  for (const el of elems) {
    let propsKey = null;
    const own = Object.keys(el);
    for (let i = 0; i < own.length; i++) { if (own[i].startsWith('__reactProps$')) { propsKey = own[i]; break; } }
    if (propsKey) {
      reactDetected = true;
      const props = el[propsKey] || {};
      for (const rk of REACT_KEYS) {
        if (typeof props[rk] === 'function') {
          const d = desc(el); d.framework = 'react'; d.event = rk; d.src = fnSrc(props[rk]);
          framework.push(d);
        }
      }
    }
    const vei = el._vei;
    if (vei && typeof vei === 'object') {
      vueDetected = true;
      for (const vk of Object.keys(vei)) {
        if (!/^on(key(down|up|press)|submit)/i.test(vk)) continue;
        const inv = vei[vk];
        let val = inv && inv.value;
        const fns = Array.isArray(val) ? val : [val];
        for (const fn of fns) {
          const d = desc(el); d.framework = 'vue3'; d.event = vk; d.src = fnSrc(fn);
          framework.push(d);
        }
      }
    }
    if (framework.length > 2000) break;
  }

  let jqueryVersion = null;
  try {
    const jq = window.jQuery;
    if (jq && jq._data) {
      jqueryVersion = jq.fn && jq.fn.jquery;
      const targets = [window, document].concat(elems);
      for (const t of targets) {
        let ev;
        try { ev = jq._data(t, 'events'); } catch (e) { ev = null; }
        if (!ev) continue;
        for (const type of ['keydown', 'keyup', 'keypress', 'submit']) {
          const hs = ev[type];
          if (!hs) continue;
          for (const h of hs) {
            const d = (t === window) ? { tag: 'window' } : (t === document) ? { tag: 'document' } : desc(t);
            d.framework = 'jquery'; d.event = type; d.delegate_selector = h.selector || null;
            d.namespace = h.namespace || null; d.src = fnSrc(h.handler);
            framework.push(d);
          }
        }
      }
    }
  } catch (e) {}

  // ---------- 7. 인라인 스크립트 (번들 스캔 대상) ----------
  const inlineScripts = [];
  for (const s of elems) {
    if (s.tagName !== 'SCRIPT' || s.src) continue;
    const ty = (s.type || '').toLowerCase();
    if (ty && !/javascript|module|ecmascript/.test(ty)) continue;
    const c = s.textContent || '';
    if (c.length) inlineScripts.push(c.length > 2000000 ? c.slice(0, 2000000) : c);
  }

  return {
    url: location.href,
    title: document.title,
    is_top: window.top === window,
    ready_state: document.readyState,
    n_elements: elems.length,
    elements_truncated: truncated,
    open_shadow_roots: shadowRoots,
    accesskeys,
    forms,
    autofocus: autofocusAll,
    active_element: activeEl,
    focused_controls: focusedControls,
    declarative_shortcuts: declarative,
    inline_handlers: inlineHandlers,
    framework_handlers: framework,
    frameworks: { react: reactDetected, vue3: vueDetected, jquery: jqueryVersion },
    inline_scripts: inlineScripts,
    body_text_sample: (document.body ? (document.body.innerText || '').slice(0, 1500) : ''),
  };
}
