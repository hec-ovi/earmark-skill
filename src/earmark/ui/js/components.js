// Shared building blocks: icons, status/priority glyphs, avatars, chips, popovers, menus, toasts.

import { html, useState, useEffect, useRef, useLayoutEffect, useMemo } from './lib.js';
import { STATUS, PRIORITY, TYPE, KIND, ROLE, PRESENCE, CLI_NAMES } from './meta.js';
import { state, set, dismissToast, topicHref, toast } from './state.js';
import { cx, hue, initials, age, absTime, dueInfo, ago } from './util.js';

// ---------------------------------------------------------------- icons

export const ICONS = new Set();

/** Inject the sprite once so <use href="#i-name"> resolves in-document (and names are known). */
export async function loadSprite() {
  try {
    const res = await fetch('/assets/icons.svg');
    const text = await res.text();
    const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
    const svg = doc.documentElement;
    if (svg.nodeName !== 'svg') return;
    svg.setAttribute('class', 'sprite');
    svg.querySelectorAll('symbol').forEach((s) => ICONS.add(s.id.slice(2)));
    document.body.prepend(document.importNode(svg, true));
  } catch (e) {
    /* icons are decoration; labels carry the meaning */
  }
}

export function Icon({ name, size = 16, cls, label, title }) {
  const n = ICONS.has(name) || !ICONS.size ? name : 'hash';
  return html`<svg class=${cx('i', 'i-' + n, cls)} width=${size} height=${size} viewBox="0 0 24 24"
    aria-hidden=${label ? null : 'true'} role=${label ? 'img' : null} aria-label=${label || null} focusable="false">
    ${title ? html`<title>${title}</title>` : null}<use href=${'#i-' + n} /></svg>`;
}

export function StatusIcon({ status, size = 16 }) {
  const m = STATUS[status] || STATUS.todo;
  return html`<span class=${cx('st', 'st-' + status)} title=${m.label}>
    <${Icon} name=${m.icon} size=${size} label=${m.label} /></span>`;
}

export function StatusPill({ status }) {
  const m = STATUS[status] || STATUS.todo;
  return html`<span class=${cx('st-pill', 'st-' + status)}><${Icon} name=${m.icon} size=${14} />${m.label}</span>`;
}

export function PriorityIcon({ priority, size = 16 }) {
  const m = PRIORITY[priority] || PRIORITY.none;
  return html`<span class=${cx('pri', 'pri-' + priority)} title=${'Priority: ' + m.label}>
    <${Icon} name=${m.icon} size=${size} label=${'Priority ' + m.label} /></span>`;
}

export function TypeIcon({ type, size = 14 }) {
  const m = TYPE[type] || TYPE.task;
  return html`<span class=${cx('ty', 'ty-' + type)} title=${m.label}><${Icon} name=${m.icon} size=${size} /></span>`;
}

export function KindBadge({ kind, compact }) {
  const m = KIND[kind] || KIND.note;
  return html`<span class=${cx('kind', 'kind-' + kind, compact && 'is-compact')} title=${m.label}>
    <${Icon} name=${m.icon} size=${12} />${compact ? null : html`<span>${m.label}</span>`}</span>`;
}

export function RoleBadge({ role }) {
  if (!role) return null;
  const m = ROLE[role] || { label: role, icon: 'user' };
  return html`<span class=${cx('role', 'role-' + role)} title=${'Role: ' + m.label}><${Icon} name=${m.icon} size=${12} />${m.label}</span>`;
}

export function CliBadge({ cli }) {
  if (!cli) return null;
  return html`<span class="cli" title=${'CLI: ' + (CLI_NAMES[cli] || cli)}><${Icon} name=${cli === 'human' ? 'user-round' : 'terminal'} size=${11} />${CLI_NAMES[cli] || cli}</span>`;
}

// ---------------------------------------------------------------- people

export function agentOf(handle) {
  return handle ? state.teamIndex.get(handle) : null;
}

export function Avatar({ handle, size = 22, presence = true, cls }) {
  if (!handle) {
    return html`<span class=${cx('av', 'av-empty', cls)} style=${{ '--sz': size + 'px' }} title="Unassigned">
      <${Icon} name="user-round" size=${Math.round(size * 0.6)} /></span>`;
  }
  const a = agentOf(handle);
  const human = a ? a.kind === 'human' : false;
  const p = a ? a.presence : null;
  const tip = a ? `${handle} · ${PRESENCE[p] ? PRESENCE[p].label : 'unknown'}${a.role ? ' · ' + a.role : ''}` : handle;
  return html`<span class=${cx('av', human ? 'av-human' : 'av-agent', cls)} title=${tip}
      style=${{ '--h': String(hue(handle)), '--sz': size + 'px' }}>
    <span class="av-txt">${initials(handle)}</span>
    ${presence && p ? html`<span class=${cx('av-dot', 'pr-' + p)}></span>` : null}
  </span>`;
}

export function PresenceDot({ presence, withLabel }) {
  const m = PRESENCE[presence] || PRESENCE.offline;
  return html`<span class=${cx('presence', 'pr-' + (presence || 'offline'))} title=${m.hint}>
    <span class="dot"></span>${withLabel ? html`<span>${m.label}</span>` : null}</span>`;
}

// ---------------------------------------------------------------- chips

export function LabelChip({ label, onRemove }) {
  return html`<span class="chip label-chip" style=${{ '--h': String(hue(label)) }}>
    <span class="label-dot"></span><span class="chip-txt">${label}</span>
    ${onRemove ? html`<button class="chip-x" type="button" aria-label=${'Remove label ' + label} onClick=${onRemove}><${Icon} name="x" size=${12} /></button>` : null}
  </span>`;
}

export function topicIconName(node) {
  if (node && node.icon && ICONS.has(node.icon)) return node.icon;
  return node && node.children && node.children.length ? 'folder' : 'hash';
}

export function topicName(path) {
  const node = state.topicIndex.get(path);
  return node ? node.name : path ? path.split('/').pop() : '';
}

export function TopicChip({ path, link = true }) {
  if (!path) return null;
  const node = state.topicIndex.get(path);
  const name = node ? node.name : path.split('/').pop();
  const body = html`<${Icon} name=${topicIconName(node || { children: [] })} size=${12} /><span class="chip-txt">${name}</span>`;
  return link
    ? html`<a class="chip topic-chip" href=${topicHref(path)} title=${path} onClick=${(e) => e.stopPropagation()}>${body}</a>`
    : html`<span class="chip topic-chip" title=${path}>${body}</span>`;
}

export function DueChip({ due, status, compact }) {
  const d = dueInfo(due, status);
  if (!d) return null;
  return html`<span class=${cx('due', d.overdue && 'is-overdue', d.soon && 'is-soon')} title=${d.title + (d.overdue ? ' · overdue' : '')}>
    <${Icon} name="calendar" size=${12} />${compact ? null : html`<span>${d.label}</span>`}</span>`;
}

export function Age({ iso, long }) {
  return html`<time class="age" datetime=${iso} title=${absTime(iso)}>${long ? ago(iso) : age(iso)}</time>`;
}

export function Kbd({ children }) {
  return html`<kbd class="kbd">${children}</kbd>`;
}

export function Spinner({ size = 16 }) {
  return html`<span class="spin" role="status" aria-label="Loading"><${Icon} name="loader-circle" size=${size} /></span>`;
}

export function Empty({ icon = 'inbox', title, children, action }) {
  return html`<div class="empty">
    <div class="empty-ico"><${Icon} name=${icon} size=${22} /></div>
    <div class="empty-title">${title}</div>
    ${children ? html`<div class="empty-body">${children}</div>` : null}
    ${action || null}
  </div>`;
}

export function IconButton({ icon, label, onClick, cls, size = 16, active, disabled, kbd }) {
  return html`<button type="button" class=${cx('icon-btn', cls, active && 'is-active')} aria-label=${label}
    title=${kbd ? `${label} (${kbd})` : label} onClick=${onClick} disabled=${disabled} aria-pressed=${active === undefined ? null : String(!!active)}>
    <${Icon} name=${icon} size=${size} /></button>`;
}

// ---------------------------------------------------------------- popovers and menus

/** First match by selector priority (querySelector alone returns document order). */
export function firstFocusable(root, ...selectors) {
  for (const sel of selectors) {
    const el = root.querySelector(sel);
    if (el && !el.disabled) return el;
  }
  return null;
}

/** Open a popover anchored to an element. content: a vnode (rendered by PopoverHost). */
export function openPopover(anchor, content, opts = {}) {
  const r = anchor.getBoundingClientRect();
  set({ popover: { rect: { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width }, content, anchor, ...opts } });
}

export function closePopover() {
  if (!state.popover) return;
  const a = state.popover.anchor;
  set({ popover: null });
  if (a && a.isConnected && !state.popover) {
    try { a.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
  }
}

export function PopoverHost() {
  const p = state.popover;
  const ref = useRef(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !p) return;
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = el.offsetWidth, hgt = el.offsetHeight;
    let left = p.align === 'right' ? p.rect.right - w : p.rect.left;
    left = Math.max(8, Math.min(left, vw - w - 8));
    let top = p.rect.bottom + 4;
    if (top + hgt > vh - 8 && p.rect.top - hgt - 4 > 8) top = p.rect.top - hgt - 4;
    top = Math.max(8, Math.min(top, vh - hgt - 8));
    el.style.left = left + 'px';
    el.style.top = top + 'px';
    el.style.visibility = 'visible';
    const focusable = firstFocusable(el, '[data-autofocus]', 'input', '[role="menuitem"], [role="menuitemcheckbox"]', 'button');
    if (focusable && !el.contains(document.activeElement)) focusable.focus({ preventScroll: true });
  });
  useEffect(() => {
    if (!p) return undefined;
    const down = (e) => {
      if (ref.current && !ref.current.contains(e.target) && !(p.anchor && p.anchor.contains(e.target))) set({ popover: null });
    };
    const resize = () => set({ popover: null });
    document.addEventListener('mousedown', down, true);
    window.addEventListener('resize', resize);
    return () => {
      document.removeEventListener('mousedown', down, true);
      window.removeEventListener('resize', resize);
    };
  }, [p]);
  if (!p) return null;
  return html`<div class=${cx('popover', p.cls)} ref=${ref} style=${{ minWidth: (p.width || 220) + 'px' }}
    onKeyDown=${(e) => { if (e.key === 'Escape') { e.stopPropagation(); closePopover(); } }}>${p.content}</div>`;
}

/**
 * Keyboard-friendly menu. items: [{value, label, icon, iconNode, hint, checked, disabled, sep, header}]
 * multi: stays open and toggles; search: shows a filter box.
 */
export function Menu({ items, onSelect, multi, search, placeholder = 'Filter…', footer, emptyText = 'No matches' }) {
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const listRef = useRef(null);
  const visible = useMemo(() => {
    if (!q) return items;
    const needle = q.toLowerCase();
    return items.filter((it) => !it.sep && !it.header && (String(it.label).toLowerCase().includes(needle) || String(it.search || '').toLowerCase().includes(needle)));
  }, [items, q]);
  const selectable = visible.filter((it) => !it.sep && !it.header && !it.disabled);
  useEffect(() => {
    const checked = q || multi ? -1 : selectable.findIndex((it) => it.checked);
    setActive(Math.max(0, checked));
  }, [q]);
  useEffect(() => {
    const el = listRef.current && listRef.current.querySelector('.is-active');
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [active]);
  const choose = (it) => {
    if (!it || it.disabled) return;
    onSelect(it.value, it);
    if (!multi) closePopover();
  };
  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, selectable.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(selectable[active]); }
  };
  let idx = -1;
  return html`<div class="menu" onKeyDown=${onKey}>
    ${search ? html`<div class="menu-search"><${Icon} name="search" size=${14} />
      <input data-autofocus type="text" value=${q} placeholder=${placeholder} aria-label=${placeholder}
        onInput=${(e) => setQ(e.target.value)} /></div>` : null}
    <div class="menu-list" role="menu" ref=${listRef} tabindex="-1">
      ${visible.map((it, i) => {
        if (it.sep) return html`<div class="menu-sep" key=${'s' + i}></div>`;
        if (it.header) return html`<div class="menu-header" key=${'h' + i}>${it.label}</div>`;
        if (!it.disabled) idx++;
        const myIdx = idx;
        const isActive = !it.disabled && myIdx === active;
        return html`<button type="button" key=${String(it.value) + i} role=${multi ? 'menuitemcheckbox' : 'menuitem'}
          aria-checked=${multi || it.checked !== undefined ? String(!!it.checked) : null}
          class=${cx('menu-item', isActive && 'is-active', it.checked && 'is-checked', it.disabled && 'is-disabled')}
          style=${it.depth ? { paddingLeft: 10 + it.depth * 14 + 'px' } : null}
          disabled=${it.disabled} title=${it.title || null}
          onMouseMove=${() => !it.disabled && myIdx !== active && setActive(myIdx)}
          onClick=${() => choose(it)}>
          ${multi ? html`<span class=${cx('check', it.checked && 'on')}><${Icon} name="check" size=${12} /></span>` : null}
          ${it.iconNode || (it.icon ? html`<${Icon} name=${it.icon} size=${15} cls=${it.iconCls} />` : null)}
          <span class="menu-label">${it.label}</span>
          ${it.hint ? html`<span class="menu-hint">${it.hint}</span>` : null}
          ${!multi && it.checked ? html`<${Icon} name="check" size=${14} cls="menu-tick" />` : null}
        </button>`;
      })}
      ${!selectable.length && !visible.some((v) => v.header) ? html`<div class="menu-empty">${emptyText}</div>` : null}
    </div>
    ${footer || null}
  </div>`;
}

// ---------------------------------------------------------------- toasts

export function Toasts() {
  const icon = { error: 'circle-alert', warn: 'refresh-cw', success: 'circle-check', info: 'info' };
  return html`<div class="toasts" role="status" aria-live="polite">
    ${state.toasts.map((t) => html`<div class=${cx('toast', 'toast-' + t.kind)} key=${t.id}>
      <${Icon} name=${icon[t.kind] || 'info'} size=${16} cls="toast-ico" />
      <div class="toast-main">
        <div class="toast-title">${t.title}</div>
        ${t.body ? html`<div class="toast-body">${t.body}</div>` : null}
        ${t.hint ? html`<div class="toast-hint"><span>hint</span><code>${t.hint}</code></div>` : null}
      </div>
      <button type="button" class="toast-x" aria-label="Dismiss" onClick=${() => dismissToast(t.id)}><${Icon} name="x" size=${14} /></button>
    </div>`)}
  </div>`;
}

// ---------------------------------------------------------------- dialogs

export function Dialog({ title, icon, onClose, children, footer, wide, cls, labelledBy }) {
  const ref = useRef(null);
  useEffect(() => {
    const prev = document.activeElement;
    const el = ref.current;
    const first = el && firstFocusable(el, '[data-autofocus]', 'input, textarea, select', '.dialog-body button', 'button');
    if (first) first.focus();
    return () => {
      if (prev && prev.isConnected) {
        try { prev.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
      }
    };
  }, []);
  const onKey = (e) => {
    if (e.key === 'Escape' && !state.popover) {
      e.stopPropagation();
      onClose();
    }
    if (e.key === 'Tab' && ref.current) {
      const f = [...ref.current.querySelectorAll('button, input, textarea, select, a[href], [tabindex="0"]')].filter((x) => !x.disabled && x.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  return html`<div class="scrim" onMouseDown=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div class=${cx('dialog', wide && 'is-wide', cls)} role="dialog" aria-modal="true" aria-label=${labelledBy ? null : title} ref=${ref} onKeyDown=${onKey}>
      <div class="dialog-head">
        ${icon ? html`<${Icon} name=${icon} size=${16} cls="dialog-ico" />` : null}
        <h2 class="dialog-title">${title}</h2>
        <${IconButton} icon="x" label="Close" onClick=${onClose} />
      </div>
      <div class="dialog-body">${children}</div>
      ${footer ? html`<div class="dialog-foot">${footer}</div>` : null}
    </div>
  </div>`;
}

/** In-page confirmation (never window.confirm). */
export function confirmDialog({ title, body, confirm = 'Confirm', danger, onConfirm }) {
  set({ dialog: { type: 'confirm', title, body, confirm, danger, onConfirm } });
}

export function ConfirmDialog({ d }) {
  const close = () => set({ dialog: null });
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    await d.onConfirm();
    setBusy(false);
    close();
  };
  return html`<${Dialog} title=${d.title} icon=${d.danger ? 'triangle-alert' : 'info'} onClose=${close} cls="is-narrow"
    footer=${html`<button type="button" class="btn" onClick=${close}>Cancel</button>
      <button type="button" class=${cx('btn', d.danger ? 'btn-danger' : 'btn-primary')} data-autofocus disabled=${busy} onClick=${go}>${d.confirm}</button>`}>
    <p class="confirm-body">${d.body}</p>
  </${Dialog}>`;
}

// ---------------------------------------------------------------- markdown helpers

/** Delegated clicks inside rendered markdown: #1187 comment refs scroll to the comment. */
export function mdClick(e) {
  const ref = e.target.closest('.md-cref');
  if (!ref) return;
  e.preventDefault();
  const el = document.getElementById('c' + ref.dataset.cid);
  if (el) {
    el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    el.classList.remove('is-target');
    void el.offsetWidth;
    el.classList.add('is-target');
  } else {
    toast({ kind: 'info', title: `Comment #${ref.dataset.cid} is on another issue` });
  }
}

