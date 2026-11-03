// Board layout: one column per status; drag a card to another column to move it.

import { html, useState } from './lib.js';
import { state, set, issueHref, navigate, moveStatus, loadIssues } from './state.js';
import { Icon, StatusIcon, PriorityIcon, Avatar, TopicChip, DueChip, Empty, Spinner, LabelChip } from './components.js';
import { STATUS, BOARD_ORDER } from './meta.js';
import { cx, plural } from './util.js';
import { openNewIssue } from './newissue.js';

let dragKey = null;

export function Board() {
  const l = state.list;
  const [over, setOver] = useState(null);
  if (l.loading && !l.items.length) return html`<div class="list-loading"><${Spinner} /> Loading issues…</div>`;
  if (l.error && !l.items.length) {
    return html`<${Empty} icon="circle-alert" title="Could not load issues" action=${html`<button type="button" class="btn" onClick=${() => loadIssues()}>Try again</button>`}>${l.error.message}</${Empty}>`;
  }
  const cols = BOARD_ORDER.filter((s) => s !== 'backlog' || state.showBacklog);
  const by = new Map(cols.map((s) => [s, []]));
  for (const it of l.items) if (by.has(it.status)) by.get(it.status).push(it);
  const canceled = l.items.filter((i) => i.status === 'canceled').length;

  const drop = async (status) => {
    setOver(null);
    const key = dragKey;
    dragKey = null;
    const it = key && l.items.find((x) => x.key === key);
    if (!it || it.status === status) return;
    await moveStatus(key, status);
  };

  return html`<div class="board" role="list" aria-label="Board">
    ${cols.map((s) => {
      const items = by.get(s);
      return html`<section key=${s} role="listitem" aria-label=${STATUS[s].label}
        class=${cx('col', 'st-' + s, over === s && 'is-over')}
        onDragOver=${(e) => { if (dragKey) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; if (over !== s) setOver(s); } }}
        onDragLeave=${(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(null); }}
        onDrop=${(e) => { e.preventDefault(); drop(s); }}>
        <header class="col-head">
          <${StatusIcon} status=${s} size=${15} />
          <span class="col-label">${STATUS[s].label}</span>
          <span class="col-count">${items.length}</span>
          ${s === 'done' ? html`<span class="col-note" title=${'Closed in the last 14 days' + (canceled ? ` · ${plural(canceled, 'canceled issue')} not shown` : '')}>last 14 days</span>` : null}
          ${s === 'todo' || s === 'backlog' ? html`<button type="button" class="icon-btn is-xs col-add" aria-label=${'New issue in ' + STATUS[s].label}
            title=${'New issue in ' + STATUS[s].label} onClick=${() => openNewIssue({ status: s })}><${Icon} name="plus" size=${14} /></button>` : null}
        </header>
        <div class="col-body">
          ${items.map((it) => html`<${Card} key=${it.key} it=${it} />`)}
          ${items.length ? null : html`<div class="col-empty">${over === s ? 'Drop to move here' : 'No issues'}</div>`}
        </div>
      </section>`;
    })}
  </div>`;
}

function Card({ it }) {
  const open = state.route.page === 'issue' && state.route.key === it.key;
  const selected = state.selected === it.key;
  return html`<a class=${cx('card', open && 'is-open', selected && 'is-selected', state.flash.has(it.key) && 'is-flash')}
    href=${issueHref(it.key)} draggable="true" data-key=${it.key}
    onDragStart=${(e) => { dragKey = it.key; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', it.key); e.currentTarget.classList.add('is-dragging'); }}
    onDragEnd=${(e) => { e.currentTarget.classList.remove('is-dragging'); dragKey = null; }}
    onClick=${(e) => { e.preventDefault(); set({ selected: it.key }); navigate(issueHref(it.key)); }}>
    <div class="card-top">
      <${PriorityIcon} priority=${it.priority} size=${14} />
      <span class="mono card-key">${it.key}</span>
      <span class="card-flags">
        ${it.blocked ? html`<span class="flag flag-blocked" title="Blocked by an open issue"><${Icon} name="ban" size=${12} /><span>Blocked</span></span>` : null}
        ${it.stale ? html`<span class="flag flag-stale" title="Lease expired: the claim is stale and others may take it over"><${Icon} name="hourglass" size=${12} /><span>Stale</span></span>` : null}
      </span>
      <${Avatar} handle=${it.assignee} size=${20} />
    </div>
    <div class="card-title">${it.title}</div>
    <div class="card-foot">
      <${TopicChip} path=${it.topic} link=${false} />
      ${it.labels.slice(0, 1).map((l) => html`<${LabelChip} key=${l} label=${l} />`)}
      <span class="card-spacer"></span>
      <${DueChip} due=${it.due} status=${it.status} />
      ${it.children ? html`<span class="meta-ico" title=${plural(it.children, 'sub-issue')}><${Icon} name="list-tree" size=${12} />${it.children}</span>` : null}
      ${it.comment_count ? html`<span class="meta-ico" title=${plural(it.comment_count, 'comment')}><${Icon} name="message-square" size=${12} />${it.comment_count}</span>` : null}
    </div>
  </a>`;
}
