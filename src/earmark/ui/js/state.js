// App state, routing, data loading, mutations and the live stream.
// One mutable state object; set() schedules a single re-render of the app.

import { api, get, qs, enc } from './api.js';
import { useState, useEffect } from './lib.js';
import { VIEWS, PRIORITY_KEYS } from './meta.js';
import { store, debounce, todayISO } from './util.js';

const listeners = new Set();
let scheduled = false;

export function set(patch) {
  Object.assign(state, typeof patch === 'function' ? patch(state) : patch);
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    listeners.forEach((fn) => fn());
  });
}

export function useStore() {
  const [, force] = useState(0);
  useEffect(() => {
    const fn = () => force((n) => n + 1);
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  return state;
}

// ---------------------------------------------------------------- routing

export function parseHash(hash) {
  const raw = (hash || '').replace(/^#\/?/, '');
  const [pathPart] = raw.split('?');
  const parts = pathPart.split('/').filter(Boolean).map((p) => {
    try { return decodeURIComponent(p); } catch (e) { return p; }
  });
  let layout = 'list';
  if (parts[0] === 'board') {
    layout = 'board';
    parts.shift();
  }
  if (parts[0] === 'issue' && parts[1]) return { page: 'issue', key: parts[1].toUpperCase(), full: parts[2] === 'full' };
  if (parts[0] === 'team') return { page: 'team' };
  if (parts[0] === 'topic' && parts.length > 1) return { page: 'issues', layout, scope: { type: 'topic', path: parts.slice(1).join('/') } };
  if (parts[0] === 'view' && VIEWS[parts[1]]) return { page: 'issues', layout, scope: { type: 'view', view: parts[1] } };
  return { page: 'issues', layout, scope: { type: 'view', view: 'all' } };
}

export function hrefFor(route) {
  if (route.page === 'issue') return `#/issue/${route.key}${route.full ? '/full' : ''}`;
  if (route.page === 'team') return '#/team';
  const pre = route.layout === 'board' ? '#/board' : '#';
  const s = route.scope || { type: 'view', view: 'all' };
  if (s.type === 'topic') return `${pre}/topic/${s.path.split('/').map(enc).join('/')}`;
  if (s.view && s.view !== 'all') return `${pre}/view/${s.view}`;
  return route.layout === 'board' ? '#/board' : '#/';
}

/** Issues open the way the user last chose: slide-over panel or full page. */
export const prefersFull = () => store.get('issue.full', false) === true;
export const issueHref = (key, full = prefersFull()) => `#/issue/${key}${full ? '/full' : ''}`;

export function setIssueLayout(full) {
  store.set('issue.full', !!full);
  if (state.route.page === 'issue') navigate(issueHref(state.route.key, !!full));
}
export const topicHref = (path, layout = currentLayout()) => hrefFor({ page: 'issues', layout, scope: { type: 'topic', path } });
export const viewHref = (view, layout = currentLayout()) => hrefFor({ page: 'issues', layout, scope: { type: 'view', view } });

export function currentLayout() {
  return (state.base && state.base.layout) || 'list';
}

export function navigate(href) {
  if (location.hash === href) onRoute();
  else location.hash = href;
}

export function closeIssue() {
  navigate(hrefFor(state.base));
}

function onRoute() {
  const route = parseHash(location.hash);
  if (route.page === 'issue' && !route.full && prefersFull()) {
    location.replace(issueHref(route.key, true));
    return;
  }
  const patch = { route, sidebarOpen: false, popover: null };
  if (route.page === 'issues') {
    const prevSig = state.base && JSON.stringify(state.base);
    patch.base = route;
    store.set('layout', route.layout);
    if (prevSig !== JSON.stringify(route)) patch.selected = null;
  }
  if (route.page === 'issue' && (!state.detail || state.detail.key !== route.key)) {
    patch.detail = { key: route.key, issue: null, comments: [], events: [], loading: true, error: null };
  }
  if (route.page !== 'issue') patch.detail = null;
  set(patch);
  if (route.page === 'issue') {
    if (patch.detail) loadDetail(route.key);
  }
  if (route.page === 'issues' || (route.page === 'issue' && !state.list.sig)) loadIssues();
  if (route.page === 'team') {
    loadTeam();
    loadProfiles();
  }
  updateTitle();
}

export function updateTitle() {
  const b = state.board;
  const r = state.route;
  let t = 'earmark';
  if (b) t = `${b.prefix} · earmark`;
  if (r.page === 'issue') t = `${r.key}${state.detail && state.detail.issue ? ' ' + state.detail.issue.title : ''} · earmark`;
  else if (r.page === 'team') t = `Team · ${t}`;
  else if (r.scope && r.scope.type === 'topic') {
    const node = state.topicIndex.get(r.scope.path);
    t = `${node ? node.name : r.scope.path} · ${t}`;
  } else if (r.scope && r.scope.view) t = `${VIEWS[r.scope.view].label} · ${t}`;
  document.title = t;
}

// ---------------------------------------------------------------- state

const savedFilters = () => {
  const f = store.get('filters', null);
  return Object.assign(emptyFilters(), f && typeof f === 'object' ? f : {});
};

export function emptyFilters() {
  return { status: [], priority: [], type: [], assignee: null, label: [], due: null, q: '' };
}

export const state = {
  ready: false,
  bootError: null,
  board: null,
  topics: [],
  topicIndex: new Map(),
  topicFlat: [],
  team: [],
  teamIndex: new Map(),
  profiles: [],
  profilesLoaded: false,
  route: { page: 'issues', layout: 'list', scope: { type: 'view', view: 'all' } },
  base: { page: 'issues', layout: store.get('layout', 'list'), scope: { type: 'view', view: 'all' } },
  filters: savedFilters(),
  sort: store.get('sort', { key: 'priority', desc: false }),
  list: { items: [], total: 0, loading: true, error: null, sig: '', more: false },
  viewCounts: {},
  flash: new Set(),
  live: 'connecting',
  toasts: [],
  uploads: [],
  lightbox: null,
  dialog: null,
  popover: null,
  selected: null,
  detail: null,
  showBacklog: store.get('board.backlog', false),
  showActivity: store.get('detail.activity', false),
  sidebarOpen: false,
  sidebarCollapsed: store.get('sidebar.collapsed', false),
  theme: store.get('theme', 'system'),
  knownLabels: new Set(),
  expanded: new Set(store.get('topics.expanded', [])),
  now: Date.now(),
};

export function setFilters(patch) {
  const filters = Object.assign({}, state.filters, patch);
  set({ filters, selected: null });
  store.set('filters', Object.assign({}, filters, { q: '' }));
  loadIssues();
}

export function setSort(sort) {
  set({ sort });
  store.set('sort', sort);
  loadIssues();
}

export function activeFilterCount(f = state.filters) {
  return f.status.length + f.priority.length + f.type.length + (f.assignee ? 1 : 0) + f.label.length + (f.due ? 1 : 0);
}

// ---------------------------------------------------------------- toasts

let toastSeq = 0;
export function toast(t) {
  const id = ++toastSeq;
  const item = Object.assign({ id, kind: 'info' }, t);
  set({ toasts: [...state.toasts, item].slice(-4) });
  const ms = t.timeout || (item.kind === 'error' || item.kind === 'warn' ? 9000 : 3500);
  setTimeout(() => dismissToast(id), ms);
  return id;
}

export function dismissToast(id) {
  set({ toasts: state.toasts.filter((t) => t.id !== id) });
}

export function handleError(e, context) {
  if (e && (e.status === 409 || e.code === 'conflict')) {
    const current = e.details && e.details.key && e.details.version ? e.details : null;
    // a stale version is "someone else changed it"; other conflicts (a live lease) say why
    toast({ kind: 'warn', icon: current ? 'refresh-cw' : null, title: current ? 'Changed by someone else — reloaded' : e.message, body: current ? e.message : null, hint: e.hint });
    if (current) applyIssue(current);
    refreshNow();
    return;
  }
  toast({ kind: 'error', title: (context ? context + ': ' : '') + ((e && e.message) || 'Something went wrong'), hint: e && e.hint });
}

/** Run a write; on failure show the envelope error as a toast and return null. */
export async function mutate(method, path, body, opts = {}) {
  try {
    const data = await api(method, path, body);
    if (opts.success) toast({ kind: 'success', title: opts.success });
    return data;
  } catch (e) {
    if (opts.onError && opts.onError(e) === true) return null;
    handleError(e, opts.context);
    return null;
  }
}

// ---------------------------------------------------------------- loaders

export async function loadBoard() {
  const board = await get('/board');
  set({ board });
  updateTitle();
}

export async function loadTopics() {
  const topics = await get('/topics');
  const index = new Map();
  const flat = [];
  const walk = (nodes, depth) => {
    for (const n of nodes) {
      n.depth = depth;
      index.set(n.path, n);
      flat.push(n);
      walk(n.children || [], depth + 1);
    }
  };
  walk(topics, 0);
  set({ topics, topicIndex: index, topicFlat: flat });
}

export async function loadTeam() {
  const data = await get('/team');
  const agents = data.agents || [];
  set({ team: agents, teamIndex: new Map(agents.map((a) => [a.handle, a])) });
}

export async function loadProfiles() {
  const profiles = await get('/profiles');
  set({ profiles, profilesLoaded: true });
}

const PRI = Object.fromEntries(PRIORITY_KEYS.map((k, i) => [k, i]));
function comparator(sort) {
  const by = {
    priority: (a, b) => PRI[a.priority] - PRI[b.priority] || a.rank - b.rank,
    rank: (a, b) => a.rank - b.rank,
    updated: (a, b) => (a.updated_at < b.updated_at ? -1 : a.updated_at > b.updated_at ? 1 : 0),
    created: (a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0),
    due: (a, b) => (a.due ? (b.due ? (a.due < b.due ? -1 : a.due > b.due ? 1 : 0) : -1) : b.due ? 1 : 0),
    key: (a, b) => a.id - b.id,
  }[sort.key] || ((a, b) => a.rank - b.rank);
  return sort.desc ? (a, b) => by(b, a) : by;
}

export const LIST_LIMIT = 300;

/** Query parameters for the current scope, filters and sort (mirrors `earmark ls`). */
export function issueQueries(route = state.base) {
  const f = state.filters;
  const scope = route.scope || { type: 'view', view: 'all' };
  const view = scope.type === 'view' ? scope.view : 'all';
  const p = { sort: state.sort.key, desc: state.sort.desc, limit: LIST_LIMIT };
  if (scope.type === 'topic') p.topic = [scope.path];
  let status = 'open';
  if (view === 'my') p.assignee = state.board ? state.board.owner : 'me';
  if (view === 'review') status = 'in_review';
  if (view === 'done') {
    status = 'done,canceled';
    p.updated_since = '7d';
    if (state.sort.key === 'priority' && !state.sort.desc) Object.assign(p, { sort: 'updated', desc: true });
  }
  if (f.status.length) status = f.status.join(',');
  if (f.priority.length) p.priority = f.priority.join(',');
  if (f.type.length) p.type = f.type.join(',');
  if (f.assignee) p.assignee = f.assignee;
  if (f.label.length) p.label = f.label;
  if (f.q) p.q = f.q;
  if (f.due === 'overdue') p.overdue = true;
  if (f.due === 'week') p.due_before = todayISO(7);
  if (f.due === 'month') p.due_before = todayISO(30);
  const queries = [];
  if (view === 'blocked' && !f.status.length) {
    queries.push(Object.assign({}, p, { status: 'blocked' }));
    queries.push(Object.assign({}, p, { status: 'open', blocked: true }));
  } else if (route.layout === 'board' && !f.status.length && view !== 'done' && view !== 'review') {
    queries.push(Object.assign({}, p, { status: 'open' }));
    queries.push(Object.assign({}, p, { status: 'done', updated_since: '14d', sort: 'updated', desc: true, limit: 50 }));
  } else {
    queries.push(Object.assign({}, p, { status }));
  }
  return queries;
}

let listSeq = 0;
export async function loadIssues({ quiet = false } = {}) {
  const seq = ++listSeq;
  const queries = issueQueries();
  const sig = JSON.stringify(queries);
  const sameQuery = sig === state.list.sig;
  if (!sameQuery && !quiet) set({ list: Object.assign({}, state.list, { loading: true, error: null }) });
  try {
    const pages = await Promise.all(queries.map((q) => get('/issues' + qs(q))));
    if (seq !== listSeq) return;
    const seen = new Map();
    let total = 0;
    let more = false;
    pages.forEach((pg, i) => {
      for (const it of pg.items) seen.set(it.key, it);
      // the board's "recently done" column is a capped extra, not part of the total
      if (!(state.base.layout === 'board' && i === 1 && queries.length === 2 && !queries[1].blocked)) total += pg.total;
      if (pg.total > pg.items.length + (pg.offset || 0)) more = true;
    });
    let items = [...seen.values()];
    if (queries.length > 1) {
      items.sort(comparator(queries[0].sort === state.sort.key ? state.sort : { key: queries[0].sort, desc: !!queries[0].desc }));
      if (queries[1] && queries[1].blocked) total = items.length;
    }
    const flash = new Set();
    if (sameQuery) {
      const prev = new Map(state.list.items.map((it) => [it.key, it.updated_at]));
      for (const it of items) {
        if (prev.has(it.key) && prev.get(it.key) !== it.updated_at) flash.add(it.key);
        if (!prev.has(it.key) && prev.size) flash.add(it.key);
      }
    }
    const known = new Set(state.knownLabels);
    items.forEach((it) => it.labels.forEach((l) => known.add(l)));
    set({ list: { items, total, loading: false, error: null, sig, more }, knownLabels: known });
    if (flash.size) flashKeys(flash);
  } catch (e) {
    if (seq !== listSeq) return;
    set({ list: Object.assign({}, state.list, { loading: false, error: e }) });
  }
}

let flashTimer = null;
function flashKeys(keys) {
  set({ flash: new Set([...state.flash, ...keys]) });
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => set({ flash: new Set() }), 1800);
}

export async function loadViewCounts() {
  const owner = state.board ? state.board.owner : 'me';
  const count = (p) => get('/issues' + qs(Object.assign({ limit: 1 }, p))).then((r) => r.total);
  try {
    const [my, done, blockedStatus, blockedDeps] = await Promise.all([
      count({ status: 'open', assignee: owner }),
      count({ status: 'done,canceled', updated_since: '7d' }),
      get('/issues' + qs({ status: 'blocked', limit: LIST_LIMIT })),
      get('/issues' + qs({ status: 'open', blocked: true, limit: LIST_LIMIT })),
    ]);
    const keys = new Set([...blockedStatus.items, ...blockedDeps.items].map((i) => i.key));
    set({ viewCounts: { my, done, blocked: keys.size } });
  } catch (e) {
    /* counts are decoration; the list shows real errors */
  }
}

let detailSeq = 0;
export async function loadDetail(key, { incremental = false } = {}) {
  const seq = ++detailSeq;
  const prev = state.detail && state.detail.key === key ? state.detail : null;
  try {
    const since = incremental && prev && prev.lastCommentId ? prev.lastCommentId : 0;
    const [issue, page, events] = await Promise.all([
      get(`/issues/${enc(key)}`),
      loadComments(key, since),
      get(`/issues/${enc(key)}/events`),
    ]);
    if (seq !== detailSeq || state.route.key !== key) return;
    const base = since && prev ? prev.comments : [];
    const byId = new Map(base.map((c) => [c.id, c]));
    page.forEach((c) => byId.set(c.id, c));
    const comments = [...byId.values()].sort((a, b) => a.id - b.id);
    const fresh = new Set(since ? page.map((c) => c.id) : []);
    set({
      detail: {
        key, issue, comments, events, loading: false, error: null,
        lastCommentId: comments.length ? comments[comments.length - 1].id : 0,
        fresh,
      },
    });
    issue.labels.forEach((l) => state.knownLabels.add(l));
    updateTitle();
    loadLinked(issue);
  } catch (e) {
    if (seq !== detailSeq) return;
    set({ detail: Object.assign({}, state.detail || { key }, { loading: false, error: e }) });
  }
}

async function loadComments(key, since) {
  const all = [];
  let cursor = since || 0;
  for (let page = 0; page < 20; page++) {
    const r = await get(`/issues/${enc(key)}/comments` + qs({ since: cursor || undefined, limit: 200 }));
    all.push(...r.items);
    if (!r.has_more || !r.items.length) break;
    cursor = r.last_id || r.items[r.items.length - 1].id;
  }
  return all;
}

/** Summaries for linked keys (links carry keys only). */
export const linkedCache = new Map();
async function loadLinked(issue) {
  const keys = new Set();
  Object.values(issue.links || {}).forEach((list) => list.forEach((k) => keys.add(k)));
  if (issue.parent) keys.add(issue.parent);
  const missing = [...keys];
  if (!missing.length) return;
  const results = await Promise.all(missing.map((k) => get(`/issues/${enc(k)}`).catch(() => null)));
  results.forEach((r, i) => linkedCache.set(missing[i], r));
  set({});
}

/** Apply a fresh Issue from a write response to the list and the open detail. */
export function applyIssue(issue) {
  if (!issue || !issue.key) return;
  const items = state.list.items.map((it) => (it.key === issue.key ? Object.assign({}, it, summaryOf(issue)) : it));
  const patch = { list: Object.assign({}, state.list, { items }) };
  if (state.detail && state.detail.key === issue.key) patch.detail = Object.assign({}, state.detail, { issue });
  set(patch);
}

function summaryOf(issue) {
  const s = Object.assign({}, issue);
  delete s.body;
  if (Array.isArray(s.children)) s.children = s.children.length;
  return s;
}

// ---------------------------------------------------------------- writes used in many places

export async function moveStatus(key, status) {
  const it = state.list.items.find((x) => x.key === key);
  const before = it && it.status;
  if (it) applyIssue(Object.assign({}, it, { status }));
  const res = await mutate('POST', `/issues/${enc(key)}/status`, { status }, {
    onError: (e) => {
      if (it) applyIssue(Object.assign({}, it, { status: before }));
      return false;
    },
  });
  if (res) applyIssue(res);
  return res;
}

export async function patchIssue(issue, fields) {
  const res = await mutate('PATCH', `/issues/${enc(issue.key)}`, Object.assign({ version: issue.version }, fields));
  if (res) applyIssue(res);
  return res;
}

export async function assignIssue(key, assignee) {
  const res = await mutate('POST', `/issues/${enc(key)}/assign`, { assignee });
  if (res) applyIssue(res);
  return res;
}

// ---------------------------------------------------------------- live updates

let pendingFull = false;
const refreshDebounced = debounce(() => refreshVisible(), 150);

export function refreshNow() {
  refreshDebounced.cancel();
  return refreshVisible();
}

export async function refreshVisible() {
  const r = state.route;
  const jobs = [loadBoard(), loadTopics(), loadTeam(), loadViewCounts()];
  if (r.page === 'issues' || r.page === 'issue') jobs.push(loadIssues({ quiet: true }));
  if (r.page === 'issue') jobs.push(loadDetail(r.key, { incremental: !pendingFull }));
  if (r.page === 'team') jobs.push(loadProfiles());
  pendingFull = false;
  await Promise.all(jobs.map((j) => j.catch(() => null)));
}

let source = null;
let retryTimer = null;
export function connectLive() {
  if (source) source.close();
  clearTimeout(retryTimer);
  let wasOffline = state.live !== 'live';
  // the server sends one `change` on connect; skip it right after boot, use it after a gap
  let skipFirst = !state.everLive;
  try {
    source = new EventSource('/api/stream');
  } catch (e) {
    set({ live: 'offline' });
    retryTimer = setTimeout(connectLive, 4000);
    return;
  }
  source.addEventListener('open', () => {
    set({ live: 'live', everLive: true });
    if (wasOffline && state.ready) {
      pendingFull = true;
      refreshDebounced();
    }
    wasOffline = false;
  });
  source.addEventListener('change', () => {
    if (skipFirst) { skipFirst = false; return; }
    refreshDebounced();
  });
  source.addEventListener('error', () => {
    wasOffline = true;
    if (source.readyState === 2) {
      set({ live: 'offline' });
      retryTimer = setTimeout(connectLive, 4000);
    } else {
      set({ live: 'connecting' });
    }
  });
}

// ---------------------------------------------------------------- boot

export async function boot() {
  window.addEventListener('hashchange', onRoute);
  state.route = parseHash(location.hash);
  if (state.route.page === 'issues') state.base = state.route;
  else state.base = Object.assign({}, state.base, { layout: store.get('layout', 'list') });
  try {
    await Promise.all([loadBoard(), loadTopics(), loadTeam()]);
  } catch (e) {
    set({ bootError: e });
    connectLive();
    return;
  }
  set({ ready: true });
  onRoute();
  loadViewCounts();
  connectLive();
  setInterval(() => set({ now: Date.now() }), 30e3);
}
