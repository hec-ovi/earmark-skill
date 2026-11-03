// Small pure helpers: time, identity colors, storage, clipboard.

export const cx = (...a) => a.filter(Boolean).join(' ');

const MIN = 60e3, HOUR = 60 * MIN, DAY = 24 * HOUR;

export function parseTime(iso) {
  return iso ? new Date(iso).getTime() : NaN;
}

/** Compact age: "now", "4m", "3h", "2d", "Nov 1". */
export function age(iso, now = Date.now()) {
  const t = parseTime(iso);
  if (!Number.isFinite(t)) return '';
  const d = Math.max(0, now - t);
  if (d < MIN) return 'now';
  if (d < HOUR) return `${Math.floor(d / MIN)}m`;
  if (d < DAY) return `${Math.floor(d / HOUR)}h`;
  if (d < 7 * DAY) return `${Math.floor(d / DAY)}d`;
  return shortDate(new Date(t));
}

/** Sentence age: "just now", "4 min ago", "3 h ago", "yesterday", "Nov 1". */
export function ago(iso, now = Date.now()) {
  const t = parseTime(iso);
  if (!Number.isFinite(t)) return '';
  const d = now - t;
  if (d < 0) return untilText(iso, now);
  if (d < MIN) return 'just now';
  if (d < HOUR) return `${Math.floor(d / MIN)} min ago`;
  if (d < DAY) return `${Math.floor(d / HOUR)} h ago`;
  if (d < 2 * DAY) return 'yesterday';
  if (d < 7 * DAY) return `${Math.floor(d / DAY)} days ago`;
  return shortDate(new Date(t));
}

/** "in 1 h 35 min" for future times. */
export function untilText(iso, now = Date.now()) {
  const d = parseTime(iso) - now;
  if (!Number.isFinite(d)) return '';
  if (d <= 0) return 'expired';
  const h = Math.floor(d / HOUR), m = Math.round((d % HOUR) / MIN);
  return h ? `in ${h} h ${m} min` : `in ${Math.max(1, m)} min`;
}

export function shortDate(date) {
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, sameYear ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}

export function absTime(iso) {
  const t = parseTime(iso);
  if (!Number.isFinite(t)) return '';
  return new Date(t).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function clockTime(iso) {
  const t = parseTime(iso);
  return Number.isFinite(t) ? new Date(t).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '';
}

export function todayISO(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

const CLOSED = new Set(['done', 'canceled']);
export const isClosed = (status) => CLOSED.has(status);

/** Due date presentation: label, overdue, soon (within 2 days). */
export function dueInfo(due, status) {
  if (!due) return null;
  const today = todayISO(0);
  const tomorrow = todayISO(1);
  const [y, m, d] = due.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  const open = !isClosed(status);
  let label = shortDate(date);
  if (due === today) label = 'Today';
  else if (due === tomorrow) label = 'Tomorrow';
  else if (due === todayISO(-1)) label = 'Yesterday';
  return {
    label,
    overdue: open && due < today,
    soon: open && due >= today && due <= todayISO(2),
    title: `Due ${date.toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' })}`,
  };
}

/** Deterministic hue 0..359 for a handle (FNV-1a). */
export function hue(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h) % 360;
}

export function initials(handle) {
  if (!handle) return '?';
  const parts = handle.split(/[-_.]/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return handle.slice(0, 2).toUpperCase();
}

export function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

export const store = {
  get(key, fallback = null) {
    try {
      const v = window.localStorage.getItem('earmark.' + key);
      return v === null ? fallback : JSON.parse(v);
    } catch (e) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem('earmark.' + key, JSON.stringify(value));
    } catch (e) { /* private mode or blocked storage */ }
  },
};

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.className = 'sr-only';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    ta.remove();
    return ok;
  }
}

export function plural(n, one, many = one + 's') {
  return `${n} ${n === 1 ? one : many}`;
}

export const LABEL_RE = /^[a-z0-9][a-z0-9._:-]{0,31}$/;
export const SLUG_RE = /^[a-z0-9][a-z0-9-]*$/;

export function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}
