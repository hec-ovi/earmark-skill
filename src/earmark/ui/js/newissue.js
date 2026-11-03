// New issue dialog (c). Preselects the current topic; Enter in the title creates.

import { html, useState, useRef } from './lib.js';
import { state, set, mutate, toast, refreshNow, issueHref, navigate } from './state.js';
import {
  Dialog, Icon, Menu, openPopover, StatusIcon, PriorityIcon, Avatar, LabelChip, topicIconName, Kbd,
} from './components.js';
import { openTopicPicker } from './topics.js';
import { STATUS, PRIORITY, PRIORITY_KEYS, TYPE, TYPE_KEYS, ROLE } from './meta.js';
import { cx, store, LABEL_RE, todayISO } from './util.js';

export function openNewIssue(preset = {}) {
  const scope = state.base && state.base.scope;
  const topic = preset.topic !== undefined ? preset.topic : scope && scope.type === 'topic' ? scope.path : null;
  set({ dialog: { type: 'new-issue', preset: Object.assign({ topic }, preset) }, popover: null });
}

export function NewIssueDialog({ d }) {
  const p = d.preset || {};
  const close = () => set({ dialog: null });
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [topic, setTopic] = useState(p.topic || null);
  const [priority, setPriority] = useState(p.priority || 'medium');
  const [type, setType] = useState(p.type || 'task');
  const [status, setStatus] = useState(p.status === 'backlog' ? 'backlog' : 'todo');
  const [labels, setLabels] = useState([]);
  const [labelDraft, setLabelDraft] = useState('');
  const [due, setDue] = useState('');
  const [assignee, setAssignee] = useState(null);
  const [more, setMore] = useState(store.get('new.more', false));
  const [busy, setBusy] = useState(false);
  const titleRef = useRef(null);
  const node = topic ? state.topicIndex.get(topic) : null;

  const addLabel = () => {
    const v = labelDraft.trim().toLowerCase();
    if (v && LABEL_RE.test(v) && !labels.includes(v)) setLabels([...labels, v]);
    setLabelDraft('');
  };

  const create = async () => {
    if (!title.trim() || busy) return;
    setBusy(true);
    const pending = labelDraft.trim().toLowerCase();
    const allLabels = pending && LABEL_RE.test(pending) && !labels.includes(pending) ? [...labels, pending] : labels;
    const res = await mutate('POST', '/issues', {
      title: title.trim(), body: body.trim() || undefined, topic: topic || undefined, priority, type, status,
      labels: allLabels.length ? allLabels : undefined, due: due || undefined, parent: p.parent || undefined,
      assignee: assignee || undefined,
    });
    setBusy(false);
    if (!res) return;
    refreshNow();
    if (more) {
      toast({ kind: 'success', title: `Created ${res.key}`, body: res.title });
      setTitle('');
      setBody('');
      setLabelDraft('');
      if (titleRef.current) titleRef.current.focus();
    } else {
      close();
      toast({ kind: 'success', title: `Created ${res.key}`, body: res.title });
      navigate(issueHref(res.key));
    }
  };

  const pickPriority = (e) => openPopover(e.currentTarget, html`<${Menu}
    items=${PRIORITY_KEYS.map((k) => ({ value: k, label: PRIORITY[k].label, checked: priority === k, iconNode: html`<${PriorityIcon} priority=${k} size=${15} />` }))}
    onSelect=${setPriority} />`, { width: 180 });
  const pickType = (e) => openPopover(e.currentTarget, html`<${Menu}
    items=${TYPE_KEYS.map((k) => ({ value: k, label: TYPE[k].label, icon: TYPE[k].icon, checked: type === k }))}
    onSelect=${setType} />`, { width: 170 });
  const pickStatus = (e) => openPopover(e.currentTarget, html`<${Menu}
    items=${['todo', 'backlog'].map((k) => ({ value: k, label: STATUS[k].label, hint: STATUS[k].hint, checked: status === k, iconNode: html`<${StatusIcon} status=${k} size=${15} />` }))}
    onSelect=${setStatus} />`, { width: 250 });
  const pickAssignee = (e) => openPopover(e.currentTarget, html`<${Menu} search placeholder="Assign to…"
    items=${[{ value: '__none', label: 'Unassigned', icon: 'user-x', checked: !assignee }, { sep: true },
      ...state.team.map((a) => ({ value: a.handle, label: a.handle, checked: assignee === a.handle, iconNode: html`<${Avatar} handle=${a.handle} size=${18} />`, hint: a.role ? (ROLE[a.role] || {}).label : null }))]}
    onSelect=${(v) => setAssignee(v === '__none' ? null : v)} />`, { width: 250 });
  const pickDue = (e) => openPopover(e.currentTarget, html`<div class="due-picker">
      <input class="input" type="date" value=${due} aria-label="Due date" data-autofocus onInput=${(ev) => setDue(ev.target.value)} />
      <div class="due-quick">${[['Today', 0], ['Tomorrow', 1], ['In a week', 7], ['In 2 weeks', 14]].map(([l, n]) =>
        html`<button type="button" class="btn btn-sm" onClick=${() => { setDue(todayISO(n)); set({ popover: null }); }}>${l}</button>`)}</div>
      ${due ? html`<div class="due-actions"><button type="button" class="btn btn-sm" onClick=${() => { setDue(''); set({ popover: null }); }}>Clear</button><span></span></div>` : null}
    </div>`, { width: 240 });

  const footer = html`
    <label class="switch" title="Keep this dialog open after creating">
      <input type="checkbox" checked=${more} onChange=${(e) => { setMore(e.target.checked); store.set('new.more', e.target.checked); }} />
      <span class="switch-track"><span class="switch-thumb"></span></span><span>Create more</span>
    </label>
    <span class="top-spacer"></span>
    <button type="button" class="btn" onClick=${close}>Cancel</button>
    <button type="button" class="btn btn-primary" disabled=${!title.trim() || busy} onClick=${create} title="Create (Enter in the title, or Ctrl+Enter)">
      Create issue</button>`;

  return html`<${Dialog} title=${p.parent ? `New sub-issue of ${p.parent}` : 'New issue'} icon="plus" onClose=${close} wide footer=${footer}>
    <div class="new-issue" onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); create(); } }}>
      <input ref=${titleRef} class="new-title" type="text" data-autofocus placeholder="Issue title" aria-label="Title" maxlength="200" value=${title}
        onInput=${(e) => setTitle(e.target.value)}
        onKeyDown=${(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); create(); } }} />
      <textarea class="new-body" rows="5" placeholder="Add a description… Markdown works. Agents read this with earmark show."
        aria-label="Description" value=${body} onInput=${(e) => setBody(e.target.value)}></textarea>
      <div class="new-props">
        <button type="button" class="pill" onClick=${pickStatus}><${StatusIcon} status=${status} size=${14} />${STATUS[status].label}</button>
        <button type="button" class="pill" onClick=${pickPriority}><${PriorityIcon} priority=${priority} size=${14} />${PRIORITY[priority].label}</button>
        <button type="button" class="pill" onClick=${pickType}><${Icon} name=${TYPE[type].icon} size=${14} />${TYPE[type].label}</button>
        <button type="button" class=${cx('pill', !topic && 'is-muted')} title=${topic || 'No topic'} onClick=${(e) => openTopicPicker(e.currentTarget, { value: topic, allowNone: true, onPick: setTopic })}>
          <${Icon} name=${node ? topicIconName(node) : 'folder'} size=${14} /><span class="pill-txt">${node ? node.name : topic || 'Topic'}</span></button>
        <button type="button" class=${cx('pill', !assignee && 'is-muted')} onClick=${pickAssignee}>
          ${assignee ? html`<${Avatar} handle=${assignee} size=${16} presence=${false} />` : html`<${Icon} name="user-round" size=${14} />`}${assignee || 'Assignee'}</button>
        <button type="button" class=${cx('pill', !due && 'is-muted')} onClick=${pickDue}><${Icon} name="calendar" size=${14} />${due || 'Due date'}</button>
      </div>
      <div class="new-labels">
        <${Icon} name="tag" size=${14} cls="muted" />
        ${labels.map((l) => html`<${LabelChip} key=${l} label=${l} onRemove=${() => setLabels(labels.filter((x) => x !== l))} />`)}
        <input class=${cx('label-input', labelDraft && !LABEL_RE.test(labelDraft.trim().toLowerCase()) && 'is-invalid')} type="text"
          placeholder=${labels.length ? 'Add label' : 'Labels: type and press Enter'} aria-label="Add label" value=${labelDraft} maxlength="32"
          list="em-known-labels"
          onInput=${(e) => setLabelDraft(e.target.value)}
          onKeyDown=${(e) => {
            if (e.key === 'Enter' || e.key === ',') { if (labelDraft.trim()) { e.preventDefault(); e.stopPropagation(); addLabel(); } }
            if (e.key === 'Backspace' && !labelDraft && labels.length) setLabels(labels.slice(0, -1));
          }} />
        <datalist id="em-known-labels">${[...state.knownLabels].sort().map((l) => html`<option value=${l} />`)}</datalist>
      </div>
      ${p.parent ? html`<div class="new-note"><${Icon} name="list-tree" size=${13} />Sub-issue of <span class="mono">${p.parent}</span></div>` : null}
      <div class="new-note muted"><${Icon} name="terminal" size=${13} />Same as <code>earmark new "${title.trim() || 'Title'}"${topic ? ` -t ${topic}` : ''}${priority !== 'medium' ? ` -p ${priority}` : ''}${type !== 'task' ? ` --type ${type}` : ''}${status === 'backlog' ? ' --status backlog' : ''}</code></div>
    </div>
  </${Dialog}>`;
}
