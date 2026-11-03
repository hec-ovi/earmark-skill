// List layout: issues grouped by status with collapsible headers, j/k selection, inline edits.

import { html, useEffect, useState } from './lib.js';
import { state, set, issueHref, navigate, moveStatus, patchIssue, activeFilterCount, setFilters, emptyFilters, loadIssues } from './state.js';
import {
  Icon, StatusIcon, PriorityIcon, Avatar, LabelChip, TopicChip, DueChip, Age, Menu, openPopover, Empty, Spinner,
} from './components.js';
import { STATUS, STATUS_KEYS, GROUP_ORDER, PRIORITY, PRIORITY_KEYS } from './meta.js';
import { cx, store, plural } from './util.js';
import { openNewIssue } from './newissue.js';

export function statusMenu(anchor, issue, onPick) {
  const items = STATUS_KEYS.map((s) => ({
    value: s, label: STATUS[s].label, checked: issue.status === s,
    iconNode: html`<${StatusIcon} status=${s} size=${15} />`,
    hint: issue.status === s ? null : null,
  }));
  openPopover(anchor, html`<${Menu} items=${items} onSelect=${(v) => v !== issue.status && (onPick ? onPick(v) : moveStatus(issue.key, v))} />`, { width: 200 });
}

export function priorityMenu(anchor, issue, onPick) {
  const items = PRIORITY_KEYS.map((p) => ({
    value: p, label: PRIORITY[p].label, checked: issue.priority === p,
    iconNode: html`<${PriorityIcon} priority=${p} size=${15} />`,
  }));
  openPopover(anchor, html`<${Menu} items=${items} onSelect=${(v) => v !== issue.priority && (onPick ? onPick(v) : patchIssue(issue, { priority: v }))} />`, { width: 190 });
}

const collapsedDefault = { done: true, canceled: true };

export function groupIssues(items) {
  const groups = new Map(GROUP_ORDER.map((s) => [s, []]));
  for (const it of items) (groups.get(it.status) || groups.set(it.status, []).get(it.status)).push(it);
  return [...groups.entries()].filter(([, list]) => list.length);
}

/** Keys in on-screen order, for j/k. */
export function visibleKeys() {
  if (state.base.layout === 'board') return state.list.items.map((i) => i.key);
  const collapsed = store.get('groups.collapsed', collapsedDefault) || {};
  return groupIssues(state.list.items).flatMap(([s, list]) => (collapsed[s] ? [] : list.map((i) => i.key)));
}

export function IssueList() {
  const l = state.list;
  const [collapsed, setCollapsed] = useState(store.get('groups.collapsed', collapsedDefault) || {});
  const toggle = (s) => {
    const next = Object.assign({}, collapsed, { [s]: !collapsed[s] });
    setCollapsed(next);
    store.set('groups.collapsed', next);
  };
  useEffect(() => {
    if (!state.selected) return;
    const el = document.querySelector(`[data-key="${state.selected}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [state.selected]);

  if (l.loading && !l.items.length) return html`<div class="list-loading"><${Spinner} /> Loading issues…</div>`;
  if (l.error && !l.items.length) {
    return html`<${Empty} icon="circle-alert" title="Could not load issues" action=${html`<button type="button" class="btn" onClick=${() => loadIssues()}>Try again</button>`}>
      ${l.error.message}${l.error.hint ? html`<div><code>${l.error.hint}</code></div>` : null}</${Empty}>`;
  }
  if (!l.items.length) return html`<${EmptyList} />`;
  const groups = groupIssues(l.items);
  return html`<div class="list" role="list" aria-label="Issues">
    ${groups.map(([status, items]) => html`<section class="group" key=${status} aria-label=${STATUS[status].label}>
      <button type="button" class=${cx('group-head', 'st-' + status)} aria-expanded=${String(!collapsed[status])} onClick=${() => toggle(status)}>
        <${Icon} name=${collapsed[status] ? 'chevron-right' : 'chevron-down'} size=${14} cls="group-caret" />
        <${StatusIcon} status=${status} size=${16} />
        <span class="group-label">${STATUS[status].label}</span>
        <span class="group-count">${items.length}</span>
        <span class="group-hint">${STATUS[status].hint}</span>
      </button>
      ${collapsed[status] ? null : items.map((it) => html`<${Row} key=${it.key} it=${it} />`)}
    </section>`)}
    ${l.more ? html`<div class="list-more">Showing ${l.items.length} of ${l.total}. Narrow the filters, or use <code>earmark ls --offset ${l.items.length}</code>.</div>` : null}
  </div>`;
}

function EmptyList() {
  const filtered = activeFilterCount() > 0 || state.filters.q;
  const scope = state.base.scope;
  if (filtered) {
    return html`<${Empty} icon="search" title="No issues match these filters"
      action=${html`<button type="button" class="btn" onClick=${() => setFilters(emptyFilters())}>Clear filters</button>`}>
      Try a broader status, or clear the search.</${Empty}>`;
  }
  const total = state.board && state.board.counts ? state.board.counts.total : 0;
  if (!total) {
    return html`<${Empty} icon="inbox" title="No issues on this board yet"
      action=${html`<button type="button" class="btn btn-primary" onClick=${() => openNewIssue()}><${Icon} name="plus" size=${15} />New issue</button>`}>
      Create one here, or let an agent file it:
      <pre class="cmd">earmark new "Eye color resets on reload" -t ui-ux -p high</pre>
    </${Empty}>`;
  }
  const msg = {
    my: ['user', 'Nothing assigned to you', 'Issues assigned to the board owner show up here.'],
    review: ['eye', 'Review queue is empty', 'Agents hand work off with earmark review KEY -m "what changed".'],
    blocked: ['ban', 'Nothing is blocked', 'Issues with status Blocked or an open blocker appear here.'],
    done: ['circle-check', 'Nothing closed in the last 7 days', 'Finished work lands here after approval.'],
  }[scope.view];
  if (msg) return html`<${Empty} icon=${msg[0]} title=${msg[1]}>${msg[2]}</${Empty}>`;
  if (scope.type === 'topic') {
    return html`<${Empty} icon="folder-open" title="Nothing open in this topic"
      action=${html`<button type="button" class="btn" onClick=${() => openNewIssue({ topic: scope.path })}><${Icon} name="plus" size=${15} />New issue here</button>`}>
      Or from the CLI: <code>earmark new "Title" -t ${scope.path}</code></${Empty}>`;
  }
  return html`<${Empty} icon="circle-check" title="All clear">No open issues. Done work is under Recently done.</${Empty}>`;
}

function Row({ it }) {
  const selected = state.selected === it.key;
  const open = state.route.page === 'issue' && state.route.key === it.key;
  const flash = state.flash.has(it.key);
  const go = (e) => {
    if (e.target.closest('button, a')) return;
    set({ selected: it.key });
    navigate(issueHref(it.key));
  };
  const labels = it.labels.slice(0, 2);
  const extra = it.labels.length - labels.length;
  return html`<div role="listitem" class=${cx('row', selected && 'is-selected', open && 'is-open', flash && 'is-flash')} data-key=${it.key} onClick=${go}>
    <button type="button" class="row-pri" aria-label=${`Priority ${PRIORITY[it.priority].label}, change`} title=${'Priority: ' + PRIORITY[it.priority].label}
      onClick=${(e) => { e.stopPropagation(); priorityMenu(e.currentTarget, it); }}>
      <${PriorityIcon} priority=${it.priority} /></button>
    <span class="row-key mono">${it.key}</span>
    <button type="button" class="row-st" aria-label=${`Status ${STATUS[it.status].label}, change`} title=${'Status: ' + STATUS[it.status].label}
      onClick=${(e) => { e.stopPropagation(); statusMenu(e.currentTarget, it); }}>
      <${StatusIcon} status=${it.status} /></button>
    <a class="row-title" href=${issueHref(it.key)} onClick=${(e) => { e.preventDefault(); set({ selected: it.key }); navigate(issueHref(it.key)); }}>
      ${it.parent ? html`<span class="row-parent" title=${'Sub-issue of ' + it.parent}><${Icon} name="corner-down-right" size=${13} /></span>` : null}
      <span class="row-title-txt">${it.title}</span>
    </a>
    <span class="row-meta">
      ${it.blocked ? html`<span class="flag flag-blocked" title="Blocked by an open issue"><${Icon} name="ban" size=${12} /><span>Blocked</span></span>` : null}
      ${it.stale ? html`<span class="flag flag-stale" title="Lease expired: the claim is stale and others may take it over"><${Icon} name="hourglass" size=${12} /><span>Stale</span></span>` : null}
      ${it.children ? html`<span class="meta-ico" title=${plural(it.children, 'sub-issue')}><${Icon} name="list-tree" size=${13} />${it.children}</span>` : null}
      <span class="row-chips">
        ${labels.map((l) => html`<${LabelChip} key=${l} label=${l} />`)}
        ${extra > 0 ? html`<span class="chip chip-more" title=${it.labels.slice(2).join(', ')}>+${extra}</span>` : null}
        <${TopicChip} path=${it.topic} />
      </span>
      <span class="row-due"><${DueChip} due=${it.due} status=${it.status} /></span>
      ${it.comment_count ? html`<span class="meta-ico comments" title=${plural(it.comment_count, 'comment')}><${Icon} name="message-square" size=${13} />${it.comment_count}</span>` : html`<span class="meta-ico comments is-zero"></span>`}
    </span>
    <span class="row-av"><${Avatar} handle=${it.assignee} size=${22} /></span>
    <span class="row-age"><${Age} iso=${it.updated_at} /></span>
  </div>`;
}
