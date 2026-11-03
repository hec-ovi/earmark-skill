// Issue detail: slide-over panel (#/issue/GAME-42) or full page (#/issue/GAME-42/full).

import { html, useState, useEffect, useRef } from './lib.js';
import {
  state, set, closeIssue, issueHref, navigate, moveStatus, patchIssue, assignIssue, mutate, applyIssue,
  linkedCache, loadDetail, refreshNow, toast, handleError, setIssueLayout,
} from './state.js';
import { api, get, qs, enc } from './api.js';
import {
  Icon, IconButton, StatusIcon, PriorityIcon, Avatar, LabelChip, Menu, openPopover, closePopover, Spinner, Empty,
  RoleBadge, PresenceDot, agentOf, topicIconName, confirmDialog, Age, TypeIcon, mdClick,
} from './components.js';
import { statusMenu, priorityMenu } from './list.js';
import { openTopicPicker } from './topics.js';
import { Conversation } from './conversation.js';
import { renderMarkdown } from './markdown.js';
import { STATUS, PRIORITY, TYPE, TYPE_KEYS, LINKS, ROLE } from './meta.js';
import { cx, copyText, dueInfo, todayISO, absTime, untilText, ago, LABEL_RE, plural } from './util.js';
import { openNewIssue } from './newissue.js';
import { AttachmentsSection, useFileDrop, DropOverlay, uploadFiles } from './attachments.js';

function IssueHead({ d, full }) {
  const issue = d.issue;
  return html`<div class=${cx('panel-head', full && 'page-head')}>
    <${IconButton} icon="arrow-left" label="Back to the list" cls=${full ? null : 'panel-back'} onClick=${closeIssue} />
    ${issue ? html`<${StatusIcon} status=${issue.status} size=${16} />` : null}
    <button type="button" class="key-copy mono" title="Copy key" onClick=${async () => { if (await copyText(d.key)) toast({ kind: 'success', title: `Copied ${d.key}` }); }}>
      ${d.key}<${Icon} name="copy" size=${12} /></button>
    ${issue && issue.parent ? html`<span class="panel-parent">in <a class="mono" href=${issueHref(issue.parent)}>${issue.parent}</a></span>` : null}
    <span class="top-spacer"></span>
    <${IconButton} icon=${full ? 'minimize-2' : 'maximize-2'} cls="layout-toggle" kbd="f"
      label=${full ? 'Open as a side panel' : 'Open as a full page'} onClick=${() => setIssueLayout(!full)} />
    ${issue ? html`<${IconButton} icon="ellipsis" label="More actions" onClick=${(e) => issueMenu(e.currentTarget, issue)} />` : null}
    ${full ? null : html`<${IconButton} icon="x" label="Close (Esc)" cls="panel-x" onClick=${closeIssue} />`}
  </div>`;
}

function IssueState({ d }) {
  if (d.loading && !d.issue) return html`<div class="list-loading"><${Spinner} /> Loading ${d.key}…</div>`;
  if (d.error && !d.issue) {
    return html`<${Empty} icon="circle-alert" title=${d.error.code === 'not_found' ? `${d.key} does not exist` : 'Could not load this issue'}
      action=${html`<button type="button" class="btn" onClick=${closeIssue}>Back to the list</button>`}>${d.error.message}</${Empty}>`;
  }
  return null;
}

function useFocusOnOpen(d) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current && !ref.current.contains(document.activeElement)) ref.current.focus({ preventScroll: true });
  }, [d && d.key]);
  return ref;
}

/** Slide-over panel (default on wide screens; full screen below 700 px). */
export function IssuePanel() {
  const d = state.detail;
  const ref = useFocusOnOpen(d);
  const [dropping, drop] = useFileDrop((files) => d && d.issue && uploadFiles(d.key, files));
  if (!d) return null;
  const issue = d.issue;
  return html`<aside class=${cx('panel', dropping && 'is-dropping')} ref=${ref} tabindex="-1" aria-label=${'Issue ' + d.key} ...${issue ? drop : {}}>
    ${dropping ? html`<${DropOverlay} issueKey=${d.key} />` : null}
    <${IssueHead} d=${d} />
    <div class="panel-scroll">
      <${IssueState} d=${d} />
      ${issue ? html`<div class="issue" key=${issue.key}>
        <${Title} issue=${issue} />
        <${HolderBanner} issue=${issue} />
        <${Properties} issue=${issue} />
        <${Description} issue=${issue} />
        <${AttachmentsSection} issue=${issue} />
        <${SubIssues} issue=${issue} />
        <${Links} issue=${issue} />
        <${Conversation} issue=${issue} d=${d} />
      </div>` : null}
    </div>
  </aside>`;
}

/** Full-page issue (#/issue/GAME-42/full): reading column plus a sticky properties column. */
export function IssuePage() {
  const d = state.detail;
  const ref = useFocusOnOpen(d);
  const [dropping, drop] = useFileDrop((files) => d && d.issue && uploadFiles(d.key, files));
  if (!d) return null;
  const issue = d.issue;
  return html`<div class=${cx('issue-page', dropping && 'is-dropping')} ref=${ref} tabindex="-1" aria-label=${'Issue ' + d.key} ...${issue ? drop : {}}>
    ${dropping ? html`<${DropOverlay} issueKey=${d.key} />` : null}
    <${IssueHead} d=${d} full />
    <${IssueState} d=${d} />
    ${issue ? html`<div class="issue-page-grid" key=${issue.key}>
      <div class="issue-page-main">
        <${Title} issue=${issue} />
        <${HolderBanner} issue=${issue} />
        <div class="page-props-inline"><${Properties} issue=${issue} column /></div>
        <${Description} issue=${issue} />
        <${AttachmentsSection} issue=${issue} />
        <${SubIssues} issue=${issue} />
        <${Links} issue=${issue} />
        <${Conversation} issue=${issue} d=${d} />
      </div>
      <aside class="issue-page-side" aria-label="Properties">
        <${Properties} issue=${issue} column />
      </aside>
    </div>` : null}
  </div>`;
}

function issueMenu(anchor, issue) {
  const items = [
    { value: 'copy-show', label: 'Copy “earmark show” command', icon: 'terminal' },
    { value: 'copy-link', label: 'Copy link', icon: 'link' },
    { value: 'sub', label: 'Add sub-issue', icon: 'list-tree' },
    { sep: true },
    { value: 'delete', label: 'Delete issue…', icon: 'trash-2', iconCls: 'tone-red' },
  ];
  openPopover(anchor, html`<${Menu} items=${items} onSelect=${async (v) => {
    if (v === 'copy-show' && await copyText(`earmark show ${issue.key}`)) toast({ kind: 'success', title: `Copied earmark show ${issue.key}` });
    if (v === 'copy-link' && await copyText(location.origin + '/' + issueHref(issue.key))) toast({ kind: 'success', title: 'Copied link' });
    if (v === 'sub') openNewIssue({ parent: issue.key, topic: issue.topic });
    if (v === 'delete') {
      confirmDialog({
        title: `Delete ${issue.key}?`,
        body: 'The issue, its comments and history are removed for good. Canceling keeps the record; prefer it unless this was created by mistake.',
        confirm: 'Delete', danger: true,
        onConfirm: async () => {
          if (await mutate('DELETE', `/issues/${enc(issue.key)}`)) {
            toast({ kind: 'success', title: `Deleted ${issue.key}` });
            closeIssue();
            refreshNow();
          }
        },
      });
    }
  }} />`, { width: 240, align: 'right' });
}

// ---------------------------------------------------------------- title

function Title({ issue }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(issue.title);
  useEffect(() => { if (!editing) setValue(issue.title); }, [issue.title, editing]);
  const save = async () => {
    const t = value.trim();
    setEditing(false);
    if (!t || t === issue.title) { setValue(issue.title); return; }
    await patchIssue(issue, { title: t });
  };
  if (editing) {
    return html`<textarea class="title-input" rows="1" value=${value} maxlength="200" aria-label="Title" ref=${(el) => el && autoGrow(el)}
      onInput=${(e) => { setValue(e.target.value); autoGrow(e.target); }}
      onBlur=${save}
      onKeyDown=${(e) => {
        if (e.key === 'Enter') { e.preventDefault(); save(); }
        if (e.key === 'Escape') { e.stopPropagation(); setValue(issue.title); setEditing(false); }
      }} data-autofocus />`;
  }
  return html`<h2 class="issue-title" tabindex="0" title="Click to edit the title"
    onClick=${() => setEditing(true)} onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); setEditing(true); } }}>${issue.title}</h2>`;
}

function autoGrow(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
  if (!el.dataset.focused) {
    el.dataset.focused = '1';
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }
}

// ---------------------------------------------------------------- holder / lease

function HolderBanner({ issue }) {
  const holder = issue.status === 'in_progress' ? issue.assignee : issue.status === 'in_review' ? issue.reviewer : null;
  if (issue.status === 'in_review' && !issue.reviewer) {
    return html`<div class="banner banner-review"><${Icon} name="eye" size=${15} />
      <span><strong>Waiting for a reviewer.</strong> A reviewer agent picks it up with <code>earmark next --review</code>.</span></div>`;
  }
  if (!holder) return null;
  const a = agentOf(holder);
  const verb = issue.status === 'in_progress' ? 'is working on this' : 'is reviewing this';
  if (issue.stale) {
    return html`<div class="banner banner-stale"><${Icon} name="hourglass" size=${15} />
      <span><strong>${holder}’s claim is stale.</strong> The lease ended ${ago(issue.lease_until)}; any agent can take it over with <code>earmark claim ${issue.key}</code>.</span></div>`;
  }
  return html`<div class="banner banner-live">
    <${Avatar} handle=${holder} size=${20} />
    <span><strong>${holder}</strong> ${verb}${a ? html` · <${PresenceDot} presence=${a.presence} withLabel />` : null}</span>
    ${issue.lease_until ? html`<span class="banner-lease" title=${'Lease until ' + absTime(issue.lease_until)}><${Icon} name="clock" size=${13} />lease ${untilText(issue.lease_until)}</span>` : null}
  </div>`;
}

// ---------------------------------------------------------------- properties

function Prop({ label, icon, children, wide }) {
  return html`<div class=${cx('prop', wide && 'is-wide')}>
    <span class="prop-label"><${Icon} name=${icon} size=${14} />${label}</span>
    <div class="prop-val">${children}</div>
  </div>`;
}

function PropButton({ onClick, children, muted, label }) {
  return html`<button type="button" class=${cx('prop-btn', muted && 'is-muted')} aria-label=${label} onClick=${onClick}>${children}</button>`;
}

function Properties({ issue, column }) {
  const due = dueInfo(issue.due, issue.status);
  const topic = issue.topic ? state.topicIndex.get(issue.topic) : null;
  const assignee = agentOf(issue.assignee);
  return html`<div class=${cx('props', column && 'is-column')}>
    <${Prop} label="Status" icon="workflow">
      <${PropButton} label="Change status" onClick=${(e) => statusMenu(e.currentTarget, issue, (v) => moveStatus(issue.key, v))}>
        <${StatusIcon} status=${issue.status} /><span>${STATUS[issue.status].label}</span></${PropButton}>
    </${Prop}>
    <${Prop} label="Priority" icon="flag">
      <${PropButton} label="Change priority" onClick=${(e) => priorityMenu(e.currentTarget, issue)}>
        <${PriorityIcon} priority=${issue.priority} /><span>${PRIORITY[issue.priority].label}</span></${PropButton}>
    </${Prop}>
    <${Prop} label="Assignee" icon="user-round">
      <${PropButton} label="Change assignee" muted=${!issue.assignee} onClick=${(e) => assigneeMenu(e.currentTarget, issue)}>
        <${Avatar} handle=${issue.assignee} size=${20} /><span>${issue.assignee || 'Unassigned'}</span>
        ${assignee && assignee.role ? html`<span class="prop-sub">${ROLE[assignee.role] ? ROLE[assignee.role].label : assignee.role}</span>` : null}
      </${PropButton}>
    </${Prop}>
    <${Prop} label="Reviewer" icon="scan-eye">
      <span class=${cx('prop-static', !issue.reviewer && 'is-muted')} title="Set by the reviewer agent when it claims the review">
        ${issue.reviewer ? html`<${Avatar} handle=${issue.reviewer} size=${20} /><span>${issue.reviewer}</span>` : html`<span>${issue.status === 'in_review' ? 'Waiting for a reviewer' : 'None'}</span>`}
      </span>
    </${Prop}>
    <${Prop} label="Type" icon="shapes">
      <${PropButton} label="Change type" onClick=${(e) => openPopover(e.currentTarget, html`<${Menu}
          items=${TYPE_KEYS.map((t) => ({ value: t, label: TYPE[t].label, icon: TYPE[t].icon, checked: issue.type === t }))}
          onSelect=${(v) => v !== issue.type && patchIssue(issue, { type: v })} />`, { width: 170 })}>
        <${TypeIcon} type=${issue.type} size=${15} /><span>${TYPE[issue.type] ? TYPE[issue.type].label : issue.type}</span></${PropButton}>
    </${Prop}>
    <${Prop} label="Due" icon="calendar">
      <${PropButton} label="Change due date" muted=${!issue.due} onClick=${(e) => openPopover(e.currentTarget, html`<${DuePicker} issue=${issue} />`, { width: 240 })}>
        <span class=${cx(due && due.overdue && 'tone-red')}>${due ? due.label : 'No due date'}</span>
        ${due && due.overdue ? html`<span class="flag flag-overdue">Overdue</span>` : null}
      </${PropButton}>
    </${Prop}>
    <${Prop} label="Topic" icon="folder" wide>
      <${PropButton} label="Change topic" muted=${!issue.topic} onClick=${(e) => openTopicPicker(e.currentTarget, {
        value: issue.topic, allowNone: true, onPick: (p) => (p || null) !== (issue.topic || null) && patchIssue(issue, { topic: p }),
      })}>
        <${Icon} name=${topic ? topicIconName(topic) : 'layers'} size=${15} cls="muted" />
        <span class="prop-ellipsis" title=${issue.topic || ''}>${topic ? topic.name : issue.topic || 'No topic'}</span>
        ${topic && topic.depth ? html`<span class="prop-sub mono prop-ellipsis">${issue.topic.split('/').slice(0, -1).join('/')}</span>` : null}
      </${PropButton}>
    </${Prop}>
    <${Prop} label="Parent" icon="list-tree" wide>
      <${PropButton} label="Change parent" muted=${!issue.parent} onClick=${(e) => openIssueSearch(e.currentTarget, {
        exclude: issue.key, title: 'Set parent issue',
        extra: issue.parent ? [{ value: '__none', label: 'Remove parent', icon: 'x' }] : [],
        onPick: (key) => patchIssue(issue, { parent: key === '__none' ? null : key }),
      })}>
        ${issue.parent ? html`<${LinkedTitle} k=${issue.parent} />` : html`<span>None</span>`}
      </${PropButton}>
    </${Prop}>
    <${Prop} label="Labels" icon="tag" wide>
      <${Labels} issue=${issue} />
    </${Prop}>
    <div class="props-meta">
      <span title=${absTime(issue.created_at)}>Created by <strong>${issue.reporter}</strong> ${ago(issue.created_at)}</span>
      <span>·</span><span title=${absTime(issue.updated_at)}>updated ${ago(issue.updated_at)}</span>
      <span>·</span><span title="Version increases with every field or status change">v${issue.version}</span>
    </div>
  </div>`;
}

function LinkedTitle({ k }) {
  const s = linkedCache.get(k);
  return html`<span class="linked">${s ? html`<${StatusIcon} status=${s.status} size=${14} />` : null}
    <span class="mono muted nowrap">${k}</span>${s ? html`<span class="prop-ellipsis">${s.title}</span>` : null}</span>`;
}

function assigneeMenu(anchor, issue) {
  const owner = state.board ? state.board.owner : null;
  const items = [
    { value: '__none', label: 'Unassigned', icon: 'user-x', checked: !issue.assignee },
    { sep: true },
    ...state.team.map((a) => ({
      value: a.handle, label: a.handle + (a.handle === owner ? ' (you)' : ''), search: (a.profile || '') + ' ' + (a.role || ''),
      checked: issue.assignee === a.handle,
      iconNode: html`<${Avatar} handle=${a.handle} size=${20} />`,
      hint: a.role ? (ROLE[a.role] ? ROLE[a.role].label : a.role) : a.kind === 'human' ? 'owner' : null,
    })),
  ];
  openPopover(anchor, html`<${Menu} items=${items} search placeholder="Assign to…" onSelect=${(v) => {
    const next = v === '__none' ? null : v;
    if (next !== issue.assignee) assignIssue(issue.key, next);
  }} />`, { width: 260 });
}

function DuePicker({ issue }) {
  const [value, setValue] = useState(issue.due || '');
  const pick = (v) => {
    closePopover();
    if ((v || null) !== (issue.due || null)) patchIssue(issue, { due: v || null });
  };
  return html`<div class="due-picker">
    <input class="input" type="date" value=${value} aria-label="Due date" data-autofocus
      onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => { if (e.key === 'Enter') pick(value); }} />
    <div class="due-quick">
      ${[['Today', 0], ['Tomorrow', 1], ['In 3 days', 3], ['In a week', 7], ['In 2 weeks', 14]].map(([l, n]) =>
        html`<button type="button" class="btn btn-sm" onClick=${() => pick(todayISO(n))}>${l}</button>`)}
    </div>
    <div class="due-actions">
      ${issue.due ? html`<button type="button" class="btn btn-sm" onClick=${() => pick(null)}>Clear</button>` : html`<span></span>`}
      <button type="button" class="btn btn-sm btn-primary" disabled=${!value} onClick=${() => pick(value)}>Set date</button>
    </div>
  </div>`;
}

function Labels({ issue }) {
  const remove = (l) => patchIssue(issue, { labels: issue.labels.filter((x) => x !== l) });
  return html`<div class="labels-edit">
    ${issue.labels.map((l) => html`<${LabelChip} key=${l} label=${l} onRemove=${() => remove(l)} />`)}
    <button type="button" class="chip chip-add" onClick=${(e) => openPopover(e.currentTarget, html`<${LabelAdder} issue=${issue} />`, { width: 240 })}>
      <${Icon} name="plus" size=${12} />Add label</button>
  </div>`;
}

function LabelAdder({ issue }) {
  const [q, setQ] = useState('');
  const v = q.trim().toLowerCase();
  const valid = LABEL_RE.test(v);
  const known = [...state.knownLabels].filter((l) => !issue.labels.includes(l) && (!v || l.includes(v))).sort().slice(0, 8);
  const add = (l) => {
    closePopover();
    if (!issue.labels.includes(l)) patchIssue(issue, { labels: [...issue.labels, l] });
  };
  return html`<div class="menu">
    <div class="menu-search"><${Icon} name="tag" size=${14} />
      <input data-autofocus type="text" placeholder="Label name" value=${q} aria-label="Label name" maxlength="32"
        onInput=${(e) => setQ(e.target.value)}
        onKeyDown=${(e) => { if (e.key === 'Enter' && valid) { e.preventDefault(); add(v); } }} /></div>
    <div class="menu-list">
      ${v && valid && !known.includes(v) && !issue.labels.includes(v) ? html`<button type="button" class="menu-item is-active" onClick=${() => add(v)}>
        <${Icon} name="plus" size=${14} /><span class="menu-label">Create “${v}”</span></button>` : null}
      ${v && !valid ? html`<div class="menu-empty">Lowercase letters, digits and . _ : - only</div>` : null}
      ${known.map((l) => html`<button type="button" class="menu-item" key=${l} onClick=${() => add(l)}>
        <${LabelChip} label=${l} /></button>`)}
      ${!v && !known.length ? html`<div class="menu-empty">Type a new label</div>` : null}
    </div>
  </div>`;
}

// ---------------------------------------------------------------- issue search (parent, links)

export function openIssueSearch(anchor, opts) {
  openPopover(anchor, html`<${IssueSearch} opts=${opts} />`, { width: 340, cls: 'is-tall' });
}

function IssueSearch({ opts }) {
  const [q, setQ] = useState('');
  const [items, setItems] = useState(null);
  const [active, setActive] = useState(0);
  useEffect(() => {
    let alive = true;
    const t = setTimeout(async () => {
      try {
        const term = q.trim();
        const results = [];
        const m = term.match(/^(?:[A-Za-z][A-Za-z0-9]*-)?(\d+)$/);
        if (m && state.board) {
          const key = `${state.board.prefix}-${m[1]}`;
          const one = await get(`/issues/${enc(key)}`).catch(() => null);
          if (one) results.push(one);
        }
        const r = await get('/issues' + qs({ q: term && !m ? term : undefined, status: 'all', sort: 'updated', desc: true, limit: 12 }));
        r.items.forEach((it) => { if (!results.some((x) => x.key === it.key)) results.push(it); });
        if (alive) { setItems(results.filter((x) => x.key !== opts.exclude && !(opts.skip || []).includes(x.key))); setActive(0); }
      } catch (e) {
        if (alive) setItems([]);
      }
    }, q ? 160 : 0);
    return () => { alive = false; clearTimeout(t); };
  }, [q]);
  const all = [...(opts.extra || []).map((x) => ({ extra: true, ...x })), ...((items || []).map((it) => ({ value: it.key, it })))];
  const pick = (x) => { if (!x) return; closePopover(); opts.onPick(x.value); };
  return html`<div class="menu" onKeyDown=${(e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, all.length - 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    if (e.key === 'Enter') { e.preventDefault(); pick(all[active]); }
  }}>
    ${opts.title ? html`<div class="menu-header">${opts.title}</div>` : null}
    <div class="menu-search"><${Icon} name="search" size=${14} />
      <input data-autofocus type="text" placeholder="Search by title or key" value=${q} aria-label="Search issues" onInput=${(e) => setQ(e.target.value)} /></div>
    <div class="menu-list">
      ${items === null ? html`<div class="menu-empty"><${Spinner} size=${14} /></div>` : null}
      ${all.map((x, i) => x.extra
        ? html`<button type="button" key=${x.value} class=${cx('menu-item', i === active && 'is-active')} onMouseMove=${() => setActive(i)} onClick=${() => pick(x)}>
            <${Icon} name=${x.icon} size=${14} /><span class="menu-label">${x.label}</span></button>`
        : html`<button type="button" key=${x.value} class=${cx('menu-item', i === active && 'is-active')} onMouseMove=${() => setActive(i)} onClick=${() => pick(x)}>
            <${StatusIcon} status=${x.it.status} size=${14} /><span class="mono muted small">${x.it.key}</span><span class="menu-label">${x.it.title}</span></button>`)}
      ${items && !all.length ? html`<div class="menu-empty">No issues found</div>` : null}
    </div>
  </div>`;
}

// ---------------------------------------------------------------- description

function Description({ issue }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(issue.body || '');
  const [busy, setBusy] = useState(false);
  const prefix = state.board ? state.board.prefix : null;
  useEffect(() => { if (!editing) setDraft(issue.body || ''); }, [issue.body, editing]);
  const save = async () => {
    setBusy(true);
    try {
      const res = await api('PATCH', `/issues/${enc(issue.key)}`, { version: issue.version, body: draft });
      applyIssue(res);
      setEditing(false);
    } catch (e) {
      if (e.status === 409 || e.code === 'conflict') {
        toast({ kind: 'warn', icon: 'refresh-cw', title: 'Changed by someone else — reloaded', body: 'Your draft is kept. Review the new version, then save again.', hint: e.hint });
        if (e.details && e.details.key) applyIssue(e.details);
        else loadDetail(issue.key);
      } else handleError(e);
    }
    setBusy(false);
  };
  if (editing) {
    return html`<section class="section">
      <div class="section-head"><h3 class="section-title">Description</h3></div>
      <textarea class="textarea md-input" rows="10" value=${draft} aria-label="Description (Markdown)" data-autofocus
        ref=${(el) => { if (el && !el.dataset.f) { el.dataset.f = '1'; el.focus(); } }}
        placeholder="What is the problem, what does done look like? Markdown works."
        onInput=${(e) => setDraft(e.target.value)}
        onKeyDown=${(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
          if (e.key === 'Escape') { e.stopPropagation(); setEditing(false); }
        }}></textarea>
      <div class="edit-actions">
        <span class="muted small">Markdown · <kbd class="kbd">Ctrl</kbd>+<kbd class="kbd">Enter</kbd> to save</span>
        <button type="button" class="btn" onClick=${() => setEditing(false)}>Cancel</button>
        <button type="button" class="btn btn-primary" disabled=${busy} onClick=${save}>Save</button>
      </div>
    </section>`;
  }
  return html`<section class="section">
    <div class="section-head">
      <h3 class="section-title">Description</h3>
      <button type="button" class="btn btn-ghost btn-sm" onClick=${() => setEditing(true)}><${Icon} name="pencil" size=${13} />Edit</button>
    </div>
    ${issue.body
      ? html`<div class="md" onClick=${mdClick} dangerouslySetInnerHTML=${{ __html: renderMarkdown(issue.body, { prefix }) }}></div>`
      : html`<button type="button" class="placeholder-btn" onClick=${() => setEditing(true)}>Add a description…</button>`}
  </section>`;
}

// ---------------------------------------------------------------- sub-issues and links

function SubIssues({ issue }) {
  const kids = Array.isArray(issue.children) ? issue.children : [];
  const done = kids.filter((k) => k.status === 'done' || k.status === 'canceled').length;
  return html`<section class="section">
    <div class="section-head">
      <h3 class="section-title">Sub-issues ${kids.length ? html`<span class="section-count">${done}/${kids.length}</span>` : null}</h3>
      <button type="button" class="btn btn-ghost btn-sm" onClick=${() => openNewIssue({ parent: issue.key, topic: issue.topic })}><${Icon} name="plus" size=${13} />Add</button>
    </div>
    ${kids.length ? html`${kids.length > 1 ? html`<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax=${kids.length} aria-valuenow=${done}
        aria-label=${`${done} of ${kids.length} sub-issues closed`}><span style=${{ width: (100 * done / kids.length) + '%' }}></span></div>` : null}
      <div class="mini-list">${kids.map((k) => html`<${MiniRow} key=${k.key} it=${k} />`)}</div>`
      : html`<div class="section-empty">Split large work into sub-issues so several agents can claim parts.</div>`}
  </section>`;
}

export function MiniRow({ it, onRemove, kindLabel }) {
  return html`<div class="mini-row">
    <${StatusIcon} status=${it.status} size=${15} />
    <a class="mini-link" href=${issueHref(it.key)}>
      <span class="mono muted mini-key">${it.key}</span><span class="mini-title">${it.title}</span>
    </a>
    ${kindLabel ? html`<span class="mini-kind">${kindLabel}</span>` : null}
    <${PriorityIcon} priority=${it.priority} size=${14} />
    <${Avatar} handle=${it.assignee} size=${18} />
    ${onRemove ? html`<button type="button" class="icon-btn is-xs" aria-label=${'Remove link to ' + it.key} title="Remove link" onClick=${onRemove}><${Icon} name="x" size=${13} /></button>` : null}
  </div>`;
}

function Links({ issue }) {
  const links = issue.links || {};
  const rows = [];
  LINKS.forEach((L) => (links[L.key] || []).forEach((k) => rows.push({ L, k })));
  const remove = async (L, k) => {
    const [src, target] = L.reverse ? [k, issue.key] : [issue.key, k];
    const res = await mutate('DELETE', `/issues/${enc(src)}/links`, { kind: L.kind, target });
    if (res) { if (!L.reverse) applyIssue(res); loadDetail(issue.key); }
  };
  const add = (e) => {
    const anchor = e.currentTarget;
    openPopover(anchor, html`<${Menu} items=${LINKS.filter((L) => L.key !== 'duplicated_by').map((L) => ({ value: L.key, label: L.label + '…', icon: L.icon }))}
      onSelect=${(v) => {
        const L = LINKS.find((x) => x.key === v);
        setTimeout(() => openIssueSearch(anchor, {
          exclude: issue.key, title: `${issue.key} ${L.label.toLowerCase()}…`, skip: links[L.key] || [],
          onPick: async (k) => {
            const [src, target] = L.reverse ? [k, issue.key] : [issue.key, k];
            const res = await mutate('POST', `/issues/${enc(src)}/links`, { kind: L.kind, target });
            if (res) loadDetail(issue.key);
          },
        }), 0);
      }} />`, { width: 200 });
  };
  return html`<section class="section">
    <div class="section-head">
      <h3 class="section-title">Links ${rows.length ? html`<span class="section-count">${rows.length}</span>` : null}</h3>
      <button type="button" class="btn btn-ghost btn-sm" onClick=${add}><${Icon} name="plus" size=${13} />Add</button>
    </div>
    ${rows.length ? html`<div class="mini-list">
      ${rows.map(({ L, k }) => {
        const s = linkedCache.get(k) || { key: k, title: '', status: 'todo', priority: 'none', assignee: null };
        return html`<${MiniRow} key=${L.key + k} it=${s} kindLabel=${L.label} onRemove=${() => remove(L, k)} />`;
      })}
    </div>` : html`<div class="section-empty">No dependencies. Use “Blocked by” so agents skip work that cannot start yet.</div>`}
  </section>`;
}
