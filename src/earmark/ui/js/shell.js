// App shell: sidebar (views, team, topics) and the top bar (breadcrumb, search, filters, sort, layout).

import { html, useEffect, useRef, useState } from './lib.js';
import {
  state, set, viewHref, hrefFor, setFilters, setSort, activeFilterCount, emptyFilters, topicHref, navigate,
} from './state.js';
import {
  Icon, IconButton, Menu, openPopover, Avatar, Kbd, topicIconName, topicName, StatusIcon, PriorityIcon,
} from './components.js';
import { TopicTree, openTopicDialog } from './topics.js';
import { VIEWS, STATUS, STATUS_KEYS, PRIORITY, PRIORITY_KEYS, TYPE, TYPE_KEYS, ROLE } from './meta.js';
import { cx, store, debounce } from './util.js';
import { openNewIssue } from './newissue.js';

// ---------------------------------------------------------------- sidebar

export function Sidebar() {
  const b = state.board;
  const r = state.route;
  const scope = state.base.scope;
  const onIssues = r.page === 'issues' || r.page === 'issue';
  const counts = {
    all: b && b.counts ? b.counts.open : null,
    my: state.viewCounts.my,
    review: b && b.counts && b.counts.by_status ? b.counts.by_status.in_review || 0 : null,
    blocked: state.viewCounts.blocked,
    done: state.viewCounts.done,
  };
  const rank = { active: 0, idle: 1 };
  const online = state.team.filter((a) => a.kind !== 'human' && a.presence !== 'offline')
    .sort((a, b) => rank[a.presence] - rank[b.presence] || a.handle.localeCompare(b.handle));
  const active = online.filter((a) => a.presence === 'active').length;
  const shown = online.slice(0, 3);
  const teamTip = online.length
    ? `${online.length} online (${active} active): ${online.map((a) => a.handle).join(', ')}`
    : 'No agents online';
  return html`<aside class=${cx('side', state.sidebarOpen && 'is-open')} aria-label="Navigation">
    <div class="side-head">
      <a class="brand" href="#/" title="earmark board">
        <span class="brand-mark"><${Icon} name="brand" size=${16} /></span>
        <span class="brand-txt"><span class="brand-prefix">${b ? b.prefix : '…'}</span><span class="brand-sub">earmark board</span></span>
      </a>
      <${IconButton} icon="x" label="Close menu" cls="side-close" onClick=${() => set({ sidebarOpen: false })} />
    </div>
    <div class="side-scroll">
      <nav class="side-nav" aria-label="Views">
        ${Object.entries(VIEWS).map(([key, v]) => {
          const isActive = onIssues && scope.type === 'view' && scope.view === key;
          return html`<a key=${key} class=${cx('nav-item', isActive && 'is-active')} href=${viewHref(key)} aria-current=${isActive ? 'page' : null}>
            <${Icon} name=${v.icon} size=${16} cls=${'nav-ico view-' + key} />
            <span class="nav-label">${v.label}</span>
            ${counts[key] ? html`<span class="nav-count">${counts[key]}</span>` : null}
          </a>`;
        })}
        <a class=${cx('nav-item', r.page === 'team' && 'is-active')} href="#/team" aria-current=${r.page === 'team' ? 'page' : null}>
          <${Icon} name="users" size=${16} cls="nav-ico" />
          <span class="nav-label">Team</span>
          <span class="nav-stack" title=${teamTip}>
            ${shown.map((a) => html`<${Avatar} key=${a.handle} handle=${a.handle} size=${20} presence=${false} />`)}
            ${online.length > shown.length ? html`<span class="nav-more">+${online.length - shown.length}</span>` : null}
          </span>
        </a>
      </nav>
      <div class="side-section">
        <div class="side-title">
          <span>Topics</span>
          <${IconButton} icon="plus" size=${14} cls="is-xs" label="Add a topic" onClick=${() => openTopicDialog({ mode: 'add', parent: '' })} />
        </div>
        <${TopicTree} />
      </div>
    </div>
    <div class="side-foot">
      <button type="button" class="side-foot-btn" onClick=${() => set({ dialog: { type: 'help' } })}>
        <${Icon} name="keyboard" size=${15} /><span>Shortcuts</span><${Kbd}>?</${Kbd}>
      </button>
    </div>
  </aside>
  ${state.sidebarOpen ? html`<div class="side-scrim" onClick=${() => set({ sidebarOpen: false })}></div>` : null}`;
}

// ---------------------------------------------------------------- top bar

function Crumbs() {
  const r = state.route;
  if (r.page === 'issue' && r.full) {
    const scope = state.base.scope;
    const label = scope.type === 'topic' ? topicName(scope.path) : (VIEWS[scope.view] || VIEWS.all).label;
    const icon = scope.type === 'topic' ? topicIconName(state.topicIndex.get(scope.path) || { children: [] }) : (VIEWS[scope.view] || VIEWS.all).icon;
    return html`<nav class="crumbs" aria-label="Location">
      <${Icon} name=${icon} size=${16} cls="crumb-ico" />
      <a class="crumb" href=${hrefFor(state.base)}>${label}</a>
      <${Icon} name="chevron-right" size=${14} cls="crumb-sep" />
      <h1 class="crumb-cur mono">${r.key}</h1>
    </nav>`;
  }
  if (r.page === 'team') {
    return html`<div class="crumbs"><${Icon} name="users" size=${16} cls="crumb-ico" /><h1 class="crumb-cur">Team</h1></div>`;
  }
  const scope = state.base.scope;
  if (scope.type === 'topic') {
    const parts = scope.path.split('/');
    const nodes = parts.map((_, i) => parts.slice(0, i + 1).join('/'));
    const top = state.topicIndex.get(nodes[0]);
    return html`<nav class="crumbs" aria-label="Topic path">
      <${Icon} name=${topicIconName(top || { children: [1] })} size=${16} cls="crumb-ico" />
      ${nodes.map((p, i) => {
        const n = state.topicIndex.get(p);
        const name = n ? n.name : parts[i];
        const last = i === nodes.length - 1;
        return html`${i ? html`<${Icon} name="chevron-right" size=${14} cls="crumb-sep" />` : null}
          ${last ? html`<h1 class="crumb-cur" title=${p}>${name}</h1>` : html`<a class="crumb" href=${topicHref(p)} title=${p}>${name}</a>`}`;
      })}
    </nav>`;
  }
  const v = VIEWS[scope.view] || VIEWS.all;
  return html`<div class="crumbs"><${Icon} name=${v.icon} size=${16} cls=${'crumb-ico view-' + scope.view} /><h1 class="crumb-cur">${v.label}</h1></div>`;
}

function Search() {
  const ref = useRef(null);
  const [value, setValue] = useState(state.filters.q || '');
  const push = useRef(debounce((q) => setFilters({ q }), 220)).current;
  useEffect(() => {
    const focus = () => { ref.current && ref.current.focus(); ref.current && ref.current.select(); };
    window.addEventListener('earmark:search', focus);
    return () => window.removeEventListener('earmark:search', focus);
  }, []);
  useEffect(() => { if (!state.filters.q && value) setValue(''); }, [state.filters.q]);
  return html`<label class="search" title="Search titles and descriptions (/)">
    <${Icon} name="search" size=${15} cls="search-ico" />
    <input ref=${ref} type="search" placeholder="Search" aria-label="Search issues" value=${value}
      onInput=${(e) => { setValue(e.target.value); push(e.target.value.trim()); }}
      onKeyDown=${(e) => {
        if (e.key === 'Escape') { e.stopPropagation(); setValue(''); push.cancel(); setFilters({ q: '' }); e.target.blur(); }
        if (e.key === 'Enter') { push.cancel(); setFilters({ q: e.target.value.trim() }); }
      }} />
    ${value ? null : html`<${Kbd}>/</${Kbd}>`}
  </label>`;
}

function LiveIndicator() {
  const l = state.live;
  const text = { live: 'Live', connecting: 'Reconnecting', offline: 'Offline' }[l];
  const tip = {
    live: 'Live: changes by agents appear as they happen',
    connecting: 'Connection lost, retrying…',
    offline: 'Server unreachable. Is `earmark ui` still running?',
  }[l];
  return html`<span class=${cx('live', 'live-' + l)} title=${tip} role="status"><span class="live-dot"></span><span class="live-txt">${text}</span></span>`;
}

const THEMES = ['system', 'light', 'dark'];
export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') root.setAttribute('data-theme', theme);
  else root.removeAttribute('data-theme');
}
function ThemeToggle() {
  const t = state.theme;
  const icon = { system: 'monitor', light: 'sun', dark: 'moon' }[t];
  const next = THEMES[(THEMES.indexOf(t) + 1) % THEMES.length];
  return html`<${IconButton} icon=${icon} label=${`Theme: ${t} (switch to ${next})`} onClick=${() => {
    set({ theme: next });
    store.set('theme', next);
    applyTheme(next);
  }} />`;
}

export function TopBar() {
  const r = state.route;
  const showTools = r.page !== 'team' && !(r.page === 'issue' && r.full);
  return html`<header class="top">
    <div class="top-row">
      <${IconButton} icon="panel-left" label="Show navigation" cls="menu-btn" onClick=${() => set({ sidebarOpen: true })} />
      <${Crumbs} />
      <div class="top-spacer"></div>
      ${showTools ? html`<${Search} />` : null}
      <${LiveIndicator} />
      <${ThemeToggle} />
      <button type="button" class="btn btn-primary new-btn" title="New issue (c)" onClick=${() => openNewIssue()}>
        <${Icon} name="plus" size=${15} /><span class="new-txt">New issue</span>
      </button>
    </div>
    ${showTools ? html`<${Toolbar} />` : null}
  </header>`;
}

// ---------------------------------------------------------------- filters

const DUE = [
  { value: 'overdue', label: 'Overdue', icon: 'calendar' },
  { value: 'week', label: 'Due within 7 days', icon: 'calendar' },
  { value: 'month', label: 'Due within 30 days', icon: 'calendar' },
];

function filterSpec(key) {
  const f = state.filters;
  if (key === 'status') {
    return {
      label: 'Status', icon: 'workflow', multi: true, values: f.status,
      chipIcon: f.status.length === 1 ? html`<${StatusIcon} status=${f.status[0]} size=${13} />` : null,
      items: STATUS_KEYS.map((s) => ({ value: s, label: STATUS[s].label, iconNode: html`<${StatusIcon} status=${s} size=${15} />` })),
      summary: f.status.length === 1 ? STATUS[f.status[0]].label : f.status.length ? `${f.status.length} statuses` : null,
    };
  }
  if (key === 'priority') {
    return {
      label: 'Priority', icon: 'flag', multi: true, values: f.priority,
      chipIcon: f.priority.length === 1 ? html`<${PriorityIcon} priority=${f.priority[0]} size=${13} />` : null,
      items: PRIORITY_KEYS.map((p) => ({ value: p, label: PRIORITY[p].label, iconNode: html`<${PriorityIcon} priority=${p} size=${15} />` })),
      summary: f.priority.length === 1 ? PRIORITY[f.priority[0]].label : f.priority.length ? `${f.priority.length} priorities` : null,
    };
  }
  if (key === 'type') {
    return {
      label: 'Type', icon: 'shapes', multi: true, values: f.type,
      items: TYPE_KEYS.map((t) => ({ value: t, label: TYPE[t].label, icon: TYPE[t].icon })),
      summary: f.type.length === 1 ? TYPE[f.type[0]].label : f.type.length ? `${f.type.length} types` : null,
    };
  }
  if (key === 'assignee') {
    const owner = state.board ? state.board.owner : null;
    const items = [
      { value: 'none', label: 'Unassigned', icon: 'user-x' },
      ...(owner ? [{ value: owner, label: `${owner} (you)`, iconNode: html`<${Avatar} handle=${owner} size=${18} />` }] : []),
      { sep: true },
      ...state.team.filter((a) => a.handle !== owner).map((a) => ({
        value: a.handle, label: a.handle, search: a.profile || '',
        iconNode: html`<${Avatar} handle=${a.handle} size=${18} />`,
        hint: a.role ? ROLE[a.role] ? ROLE[a.role].label : a.role : null,
      })),
    ];
    return {
      label: 'Assignee', icon: 'user-round', multi: false, values: f.assignee ? [f.assignee] : [], items, search: true,
      summary: f.assignee === 'none' ? 'Unassigned' : f.assignee,
    };
  }
  if (key === 'label') {
    const labels = [...new Set([...state.knownLabels, ...f.label])].sort();
    return {
      label: 'Label', icon: 'tag', multi: true, values: f.label, search: true,
      items: labels.map((l) => ({ value: l, label: l, icon: 'tag' })),
      summary: f.label.length === 1 ? f.label[0] : f.label.length ? `${f.label.length} labels` : null,
      empty: 'No labels seen yet',
    };
  }
  return {
    label: 'Due', icon: 'calendar', multi: false, values: f.due ? [f.due] : [], items: DUE,
    summary: f.due ? DUE.find((d) => d.value === f.due).label : null,
  };
}

function FilterMenu({ fkey }) {
  const spec = filterSpec(fkey);
  const items = spec.items.map((it) => (it.sep ? it : Object.assign({}, it, { checked: spec.values.includes(it.value) })));
  return html`<${Menu} items=${items} multi=${spec.multi} search=${spec.search} emptyText=${spec.empty || 'No matches'}
    onSelect=${(v) => {
      if (spec.multi) {
        const cur = state.filters[fkey];
        setFilters({ [fkey]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] });
      } else {
        const cur = state.filters[fkey];
        setFilters({ [fkey]: cur === v ? null : v });
      }
    }} />`;
}

function FilterChip({ fkey }) {
  const spec = filterSpec(fkey);
  const on = !!spec.summary;
  const open = (e) => openPopover(e.currentTarget, html`<${FilterMenu} fkey=${fkey} />`, { width: 230 });
  return html`<span class=${cx('fchip', on && 'is-on')}>
    <button type="button" class="fchip-main" aria-haspopup="menu" onClick=${open}
      title=${on ? `${spec.label}: ${spec.summary}` : `Filter by ${spec.label.toLowerCase()}`}>
      ${spec.chipIcon || html`<${Icon} name=${spec.icon} size=${13} />`}
      <span class="fchip-label">${spec.label}</span>
      ${on ? html`<span class="fchip-val">${spec.summary}</span>` : html`<${Icon} name="chevron-down" size=${12} cls="fchip-caret" />`}
    </button>
    ${on ? html`<button type="button" class="fchip-x" aria-label=${'Clear ' + spec.label + ' filter'}
      onClick=${() => setFilters({ [fkey]: Array.isArray(state.filters[fkey]) ? [] : null })}><${Icon} name="x" size=${12} /></button>` : null}
  </span>`;
}

const SORTS = [
  { value: 'priority', label: 'Priority', icon: 'flag' },
  { value: 'updated', label: 'Last updated', icon: 'history' },
  { value: 'created', label: 'Created', icon: 'plus' },
  { value: 'due', label: 'Due date', icon: 'calendar' },
  { value: 'rank', label: 'Manual order', icon: 'grip-vertical' },
  { value: 'key', label: 'Key', icon: 'hash' },
];

function SortButton() {
  const s = state.sort;
  const cur = SORTS.find((x) => x.value === s.key) || SORTS[0];
  const open = (e) => openPopover(e.currentTarget, html`<${Menu}
    items=${[
      ...SORTS.map((x) => Object.assign({}, x, { checked: x.value === s.key })),
      { sep: true },
      { value: '__dir', label: s.desc ? 'Descending' : 'Ascending', icon: 'arrow-up-down', hint: 'toggle' },
    ]}
    onSelect=${(v) => setSort(v === '__dir' ? Object.assign({}, s, { desc: !s.desc }) : { key: v, desc: v === 'updated' || v === 'created' })} />`,
  { width: 200, align: 'right' });
  return html`<button type="button" class="tool-btn" aria-haspopup="menu" onClick=${open} title="Sort order">
    <${Icon} name="arrow-down-wide-narrow" size=${14} /><span class="tool-txt">${cur.label}</span>
    <${Icon} name=${s.desc ? 'chevron-down' : 'chevron-up'} size=${12} cls="muted" />
  </button>`;
}

function LayoutToggle() {
  const base = state.base;
  const go = (layout) => navigate(hrefFor(Object.assign({}, base, { page: 'issues', layout })));
  return html`<div class="seg" role="group" aria-label="Layout">
    <button type="button" class=${cx('seg-btn', base.layout !== 'board' && 'is-on')} aria-pressed=${String(base.layout !== 'board')} onClick=${() => go('list')} title="List">
      <${Icon} name="layout-list" size=${15} /><span class="seg-txt">List</span></button>
    <button type="button" class=${cx('seg-btn', base.layout === 'board' && 'is-on')} aria-pressed=${String(base.layout === 'board')} onClick=${() => go('board')} title="Board">
      <${Icon} name="square-kanban" size=${15} /><span class="seg-txt">Board</span></button>
  </div>`;
}

function Toolbar() {
  const n = activeFilterCount();
  return html`<div class="toolbar">
    <div class="filters" role="group" aria-label="Filters">
      <${Icon} name="list-filter" size=${15} cls="filters-ico" />
      ${['status', 'priority', 'type', 'assignee', 'label', 'due'].map((k) => html`<${FilterChip} key=${k} fkey=${k} />`)}
      ${n > 1 ? html`<button type="button" class="link-btn small" onClick=${() => setFilters(Object.assign(emptyFilters(), { q: state.filters.q }))}>Clear all</button>` : null}
    </div>
    <div class="toolbar-right">
      ${state.base.layout === 'board' ? html`<button type="button" class=${cx('tool-btn', state.showBacklog && 'is-on')}
        aria-pressed=${String(state.showBacklog)} title="Show the backlog column"
        onClick=${() => { set({ showBacklog: !state.showBacklog }); store.set('board.backlog', !state.showBacklog); }}>
        <${Icon} name="circle-dashed" size=${14} /><span>Backlog</span></button>` : null}
      <${SortButton} />
      <${LayoutToggle} />
    </div>
  </div>`;
}
