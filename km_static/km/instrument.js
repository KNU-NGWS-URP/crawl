// km/instrument.js
// Tier 1 계측 init 스크립트. 페이지 스크립트보다 먼저 모든 프레임에서 실행된다.
// 목적: 키보드/제출 관련 이벤트 리스너의 "등록 사실"만 기록한다.
//  - 리스너를 호출하거나 이벤트를 합성(dispatch)하지 않는다.
//  - 원래 addEventListener 동작은 그대로 유지한다 (인자·this 그대로 전달).
// 정의서 원칙 8: 핸들러 등록 ≠ 액션 실행 가능. 여기 기록은 static-handler 근거일 뿐이다.
(() => {
  'use strict';
  if (window.__km_installed__) return;
  try {
    Object.defineProperty(window, '__km_installed__', { value: true, enumerable: false });
  } catch (e) { return; }

  const KEY_TYPES = new Set(['keydown', 'keyup', 'keypress']);
  const FORM_TYPES = new Set(['submit']);
  const SRC_LIMIT = 4000;
  const MAX_RECORDS = 5000;

  const records = [];
  const targetIds = new WeakMap();
  let nextTargetId = 1;
  const targetRefs = new Map(); // id -> WeakRef

  const nativeToString = Function.prototype.toString;
  const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

  function tid(t) {
    let id = targetIds.get(t);
    if (!id) {
      id = nextTargetId++;
      targetIds.set(t, id);
      try { targetRefs.set(id, new WeakRef(t)); } catch (e) { /* WeakRef 미지원 */ }
    }
    return id;
  }

  function isSubmitCapable(t) {
    try {
      if (!(t instanceof Element)) return false;
      const tag = t.tagName;
      if (tag === 'FORM') return true;
      if (tag === 'BUTTON') return (t.type || 'submit') === 'submit';
      if (tag === 'INPUT') return t.type === 'submit' || t.type === 'image';
    } catch (e) {}
    return false;
  }

  function listenerSource(listener) {
    try {
      let fn = listener;
      let kind = 'function';
      if (listener && typeof listener === 'object' && typeof listener.handleEvent === 'function') {
        fn = listener.handleEvent; kind = 'handleEvent';
      }
      if (typeof fn !== 'function') return { kind: 'non-callable', src: null, len: 0 };
      const s = nativeToString.call(fn);
      if (/\{\s*\[native code\]\s*\}$/.test(s)) kind = kind + ':native-or-bound';
      return { kind, src: s.length > SRC_LIMIT ? s.slice(0, SRC_LIMIT) : s, len: s.length };
    } catch (e) {
      return { kind: 'error', src: null, len: 0 };
    }
  }

  function callsite() {
    const old = Error.stackTraceLimit;
    try {
      Error.stackTraceLimit = 25;
      const st = (new Error()).stack || '';
      const lines = st.split('\n').slice(1).map(s => s.trim());
      // 계측 프레임(이 파일) 제외
      const ext = lines.filter(l => !l.includes('__km_') && !l.includes('km_instrument'));
      return ext.slice(0, 8);
    } catch (e) {
      return [];
    } finally {
      Error.stackTraceLimit = old;
    }
  }

  function record(target, type, listener, options, via) {
    if (records.length >= MAX_RECORDS) return;
    const isKey = KEY_TYPES.has(type);
    const isForm = FORM_TYPES.has(type);
    const isSubmitClick = type === 'click' && isSubmitCapable(target);
    if (!isKey && !isForm && !isSubmitClick) return;
    let capture = false;
    if (typeof options === 'boolean') capture = options;
    else if (options && typeof options === 'object') capture = !!options.capture;
    const ls = listenerSource(listener);
    records.push({
      seq: records.length,
      t_ms: Math.round(now()),
      type,
      via,                       // addEventListener | on-property
      target_id: tid(target),
      capture,
      listener_kind: ls.kind,
      listener_len: ls.len,
      listener_src: ls.src,
      stack: callsite(),
      removed: false,
    });
  }

  // 1) EventTarget.prototype.addEventListener / removeEventListener 래핑
  const ET = window.EventTarget && window.EventTarget.prototype;
  if (ET) {
    const origAdd = ET.addEventListener;
    const origRemove = ET.removeEventListener;
    const wrappedAdd = function addEventListener(type, listener, options) {
      try { record(this, String(type), listener, options, 'addEventListener'); } catch (e) {}
      return origAdd.apply(this, arguments);
    };
    const wrappedRemove = function removeEventListener(type, listener, options) {
      try {
        const id = targetIds.get(this);
        if (id) {
          for (let i = records.length - 1; i >= 0; i--) {
            const r = records[i];
            if (r.target_id === id && r.type === String(type) && !r.removed) { r.removed = true; break; }
          }
        }
      } catch (e) {}
      return origRemove.apply(this, arguments);
    };
    // 일부 사이트는 네이티브 여부를 검사하므로 toString 결과를 원본과 동일하게 유지 (호환성 목적)
    const fakeStr = new Map([[wrappedAdd, nativeToString.call(origAdd)], [wrappedRemove, nativeToString.call(origRemove)]]);
    const patchedToString = function toString() {
      if (fakeStr.has(this)) return fakeStr.get(this);
      return nativeToString.apply(this, arguments);
    };
    fakeStr.set(patchedToString, nativeToString.call(nativeToString));
    try {
      Object.defineProperty(ET, 'addEventListener', { value: wrappedAdd, writable: true, configurable: true, enumerable: false });
      Object.defineProperty(ET, 'removeEventListener', { value: wrappedRemove, writable: true, configurable: true, enumerable: false });
      Object.defineProperty(Function.prototype, 'toString', { value: patchedToString, writable: true, configurable: true, enumerable: false });
    } catch (e) {}
  }

  // 2) on-property 핸들러 (el.onkeydown = fn) 래핑
  const ON_PROPS = ['onkeydown', 'onkeyup', 'onkeypress', 'onsubmit'];
  const protos = [window.HTMLElement && HTMLElement.prototype, window.Document && Document.prototype,
                  window.SVGElement && SVGElement.prototype, window];
  for (const proto of protos) {
    if (!proto) continue;
    for (const prop of ON_PROPS) {
      try {
        const d = Object.getOwnPropertyDescriptor(proto, prop);
        if (!d || !d.set || !d.configurable) continue;
        const origSet = d.set;
        Object.defineProperty(proto, prop, {
          configurable: true, enumerable: d.enumerable, get: d.get,
          set: function (v) {
            try { if (typeof v === 'function') record(this, prop.slice(2), v, false, 'on-property'); } catch (e) {}
            return origSet.call(this, v);
          },
        });
      } catch (e) {}
    }
  }

  // 3) 대상(target) 기술자: 덤프 시점에 계산 (등록 시점엔 DOM에 없을 수 있음)
  function cssPath(el) {
    try {
      const parts = [];
      let cur = el;
      let depth = 0;
      while (cur && cur.nodeType === 1 && depth < 12) {
        let p = cur.tagName.toLowerCase();
        if (cur.id) { p += '#' + CSS.escape(cur.id); parts.unshift(p); break; }
        const parent = cur.parentElement;
        if (parent) {
          const sib = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
          if (sib.length > 1) p += `:nth-of-type(${sib.indexOf(cur) + 1})`;
        }
        parts.unshift(p);
        cur = parent;
        depth++;
      }
      return parts.join(' > ');
    } catch (e) { return null; }
  }

  function describe(t) {
    try {
      if (t === window) return { kind: 'window' };
      if (t === document) return { kind: 'document' };
      if (t && t.nodeType === 1) {
        const txt = (t.innerText || t.textContent || '').trim().replace(/\s+/g, ' ');
        return {
          kind: 'element',
          tag: t.tagName.toLowerCase(),
          id: t.id || null,
          name: t.getAttribute('name'),
          type: t.getAttribute('type'),
          role: t.getAttribute('role'),
          aria_label: t.getAttribute('aria-label'),
          title: t.getAttribute('title'),
          placeholder: t.getAttribute('placeholder'),
          text: txt.length > 80 ? txt.slice(0, 80) : txt,
          connected: t.isConnected,
          in_shadow: (t.getRootNode && t.getRootNode() instanceof ShadowRoot) || false,
          is_body_or_html: t === document.body || t === document.documentElement,
          css_path: cssPath(t),
        };
      }
      if (t && t.nodeType === 9) return { kind: 'other-document' };
      if (t && t.nodeType === 11) return { kind: 'shadow-root' };
      return { kind: (t && t.constructor && t.constructor.name) || 'unknown' };
    } catch (e) { return { kind: 'error' }; }
  }

  Object.defineProperty(window, '__km_dump__', {
    enumerable: false,
    value: function () {
      const targets = {};
      for (const [id, ref] of targetRefs) {
        const t = ref.deref ? ref.deref() : undefined;
        targets[id] = t ? describe(t) : { kind: 'collected' };
      }
      return {
        url: location.href,
        is_top: window.top === window,
        t_ms: Math.round(now()),
        truncated: records.length >= MAX_RECORDS,
        records: records.slice(),
        targets,
      };
    },
  });
})();
