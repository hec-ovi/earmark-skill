// Topic tree (sidebar), topic picker (popover) and the add / edit / move dialogs.

import { html, useState } from './lib.js';
import { state, set, topicHref, mutate, loadTopics, refreshNow, toast } from './state.js';
import { Icon, IconButton, Menu, openPopover, Dialog, confirmDialog, topicIconName } from './components.js';
import { ICON_CHOICES } from './meta.js';
import { cx, store, SLUG_RE } from './util.js';

function toggle(path, open) {
  const expanded = new Set(state.expanded);
  if (open) expanded.add(path);
  else expanded.delete(path);
  set({ expanded });
  store.set('topics.expanded', [...expanded]);
}

export function TopicTree() {
  const scope = state.base && state.base.scope;
  const active = scope && scope.type === 'topic' && state.route.page !== 'team' ? scope.path : null;
  if (!state.topics.length) {
    return html`<div class="tree-empty">No topics yet.${' '}
      <button type="button" class="link-btn" onClick=${() => openTopicDialog({ mode: 'add', parent: '' })}>Add the first one</button>${' '}
      or run <code>earmark topic add ui-ux</code></div>`;
  }
  return html`<div class="tree" role="tree" aria-label="Topics">
    ${state.topics.map((n) => html`<${TopicNode} key=${n.id} node=${n} active=${active} />`)}
  </div>`;
}

function TopicNode({ node, active }) {
  const kids = node.children || [];
  const isActive = active === node.path;
  const inActive = active && active.startsWith(node.path + '/');
  const open = kids.length > 0 && (state.expanded.has(node.path) || inActive);
  const open_count = node.counts ? node.counts.open : 0;
  return html`<div role="none">
    <div class=${cx('tree-row', isActive && 'is-active', node.archived && 'is-archived')} style=${{ '--depth': String(node.depth || 0) }}
      role="treeitem" aria-expanded=${kids.length ? String(open) : null} aria-selected=${String(isActive)}>
      ${kids.length
        ? html`<button type="button" class="tree-caret" aria-label=${(open ? 'Collapse ' : 'Expand ') + node.name}
            onClick=${() => toggle(node.path, !open)}><${Icon} name=${open ? 'chevron-down' : 'chevron-right'} size=${14} /></button>`
        : html`<span class="tree-caret is-leaf"></span>`}
      <a class="tree-link" href=${topicHref(node.path)} title=${node.name + '\n' + node.path + (node.description ? '\n' + node.description : '')}>
        <${Icon} name=${topicIconName(node)} size=${15} cls="tree-ico" />
        <span class="tree-name">${node.name}</span>
      </a>
      <span class="tree-count" title=${open_count + ' open'}>${open_count || ''}</span>
      <span class="tree-actions">
        <${IconButton} icon="plus" size=${14} label=${'Add a subtopic under ' + node.name} cls="is-xs"
          onClick=${() => openTopicDialog({ mode: 'add', parent: node.path })} />
        <${IconButton} icon="ellipsis" size=${14} label=${'More actions for ' + node.name} cls="is-xs"
          onClick=${(e) => openTopicMenu(e.currentTarget, node)} />
      </span>
    </div>
    ${open ? html`<div role="group">${kids.map((k) => html`<${TopicNode} key=${k.id} node=${k} active=${active} />`)}</div>` : null}
  </div>`;
}

function openTopicMenu(anchor, node) {
  const empty = !(node.children && node.children.length) && !(node.counts && node.counts.total);
  const items = [
    { value: 'add', label: 'Add subtopic', icon: 'folder-plus' },
    { value: 'edit', label: 'Rename and icon…', icon: 'pencil' },
    { value: 'move', label: 'Move…', icon: 'move' },
    { sep: true },
    { value: 'archive', label: node.archived ? 'Unarchive' : 'Archive', icon: node.archived ? 'archive-restore' : 'archive' },
    { value: 'delete', label: 'Delete', icon: 'trash-2', iconCls: 'tone-red', disabled: !empty, title: empty ? null : 'Only empty topics can be deleted; archive it instead' },
  ];
  openPopover(anchor, html`<${Menu} items=${items} onSelect=${(v) => topicAction(v, node)} />`, { width: 200 });
}

function topicAction(action, node) {
  if (action === 'add') openTopicDialog({ mode: 'add', parent: node.path });
  if (action === 'edit') openTopicDialog({ mode: 'edit', node });
  if (action === 'move') openTopicDialog({ mode: 'move', node });
  if (action === 'archive') {
    if (node.archived) return archive(node, false);
    confirmDialog({
      title: `Archive ${node.name}?`,
      body: `It disappears from the sidebar and pickers. Its ${node.counts ? node.counts.total : 0} issues keep their topic. Unarchive it from the CLI: earmark topics --all.`,
      confirm: 'Archive',
      onConfirm: () => archive(node, true),
    });
  }
  if (action === 'delete') {
    confirmDialog({
      title: `Delete ${node.name}?`,
      body: `The empty topic ${node.path} is removed for good.`,
      confirm: 'Delete', danger: true,
      onConfirm: async () => {
        if (await mutate('DELETE', `/topics/${node.id}`)) {
          toast({ kind: 'success', title: `Deleted ${node.path}` });
          refreshNow();
        }
      },
    });
  }
}

async function archive(node, archived) {
  const ok = archived
    ? await mutate('DELETE', `/topics/${node.id}?archive=1`)
    : await mutate('PATCH', `/topics/${node.id}`, { archived: false });
  if (ok) {
    toast({ kind: 'success', title: `${archived ? 'Archived' : 'Unarchived'} ${node.path}` });
    refreshNow();
  }
}

export function openTopicDialog(opts) {
  set({ dialog: Object.assign({ type: 'topic' }, opts), popover: null });
}

const slugify = (s) => s.toLowerCase().normalize('NFKD').replace(/[^\w\s-]/g, '').trim().replace(/[\s_]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '').slice(0, 48);

export function TopicDialog({ d }) {
  const node = d.node;
  const close = () => set({ dialog: null });
  const [name, setName] = useState(node ? node.name : '');
  const [slug, setSlug] = useState(node ? node.slug : '');
  const [slugTouched, setSlugTouched] = useState(!!node);
  const [icon, setIcon] = useState(node ? node.icon : null);
  const [description, setDescription] = useState(node ? node.description || '' : '');
  const [parent, setParent] = useState(d.mode === 'move' ? node.path.split('/').slice(0, -1).join('/') : d.parent || '');
  const [busy, setBusy] = useState(false);
  const effSlug = slugTouched ? slug : slugify(name);
  const slugOk = SLUG_RE.test(effSlug) && effSlug.length <= 48;

  const submit = async (e) => {
    if (e) e.preventDefault();
    setBusy(true);
    let ok = null;
    if (d.mode === 'add') {
      if (!slugOk) { setBusy(false); return; }
      const path = (parent ? parent + '/' : '') + effSlug;
      ok = await mutate('POST', '/topics', { path, name: name.trim() || effSlug, icon: icon || undefined, description: description.trim() || undefined });
      if (ok) {
        toast({ kind: 'success', title: `Added topic ${path}` });
        if (parent) {
          const expanded = new Set(state.expanded);
          expanded.add(parent);
          set({ expanded });
          store.set('topics.expanded', [...expanded]);
        }
      }
    } else if (d.mode === 'edit') {
      ok = await mutate('PATCH', `/topics/${node.id}`, { name: name.trim() || node.slug, icon: icon || null, description: description.trim() });
    } else if (d.mode === 'move') {
      if (!slugOk) { setBusy(false); return; }
      const path = (parent ? parent + '/' : '') + effSlug;
      if (path === node.path) { close(); return; }
      ok = await mutate('PATCH', `/topics/${node.id}`, { path });
      if (ok) {
        toast({ kind: 'success', title: `Moved to ${path}` });
        const scope = state.base.scope;
        if (scope.type === 'topic' && (scope.path === node.path || scope.path.startsWith(node.path + '/'))) {
          location.hash = topicHref(path + scope.path.slice(node.path.length));
        }
      }
    }
    setBusy(false);
    if (ok) {
      close();
      loadTopics().catch(() => null);
      refreshNow();
    }
  };

  const title = d.mode === 'add' ? (parent ? 'New subtopic' : 'New topic') : d.mode === 'edit' ? `Edit ${node.name}` : `Move ${node.name}`;
  const finalPath = (parent ? parent + '/' : '') + (effSlug || '…');
  return html`<${Dialog} title=${title} icon=${d.mode === 'move' ? 'move' : 'folder-plus'} onClose=${close}
    footer=${html`<span class="foot-note mono" title="Resulting topic path">${d.mode === 'edit' ? node.path : finalPath}</span>
      <button type="button" class="btn" onClick=${close}>Cancel</button>
      <button type="submit" form="topic-form" class="btn btn-primary" disabled=${busy || (d.mode !== 'edit' && !slugOk)}>
        ${d.mode === 'add' ? 'Add topic' : d.mode === 'edit' ? 'Save' : 'Move'}</button>`}>
    <form id="topic-form" class="form" onSubmit=${submit}>
      ${d.mode !== 'edit' ? html`<div class="field">
        <span class="field-label">Parent</span>
        <button type="button" class="select-btn" onClick=${(e) => openTopicPicker(e.currentTarget, {
          value: parent, allowNone: true, noneLabel: 'Top level', exclude: node && node.path,
          onPick: (p) => setParent(p || ''),
        })}>
          <${Icon} name=${parent ? 'folder' : 'layers'} size=${15} />
          <span class=${cx('select-txt', !parent && 'muted')}>${parent || 'Top level'}</span>
          <${Icon} name="chevron-down" size=${14} cls="select-caret" />
        </button>
      </div>` : null}
      ${d.mode !== 'move' ? html`<label class="field">
        <span class="field-label">Name</span>
        <input class="input" data-autofocus type="text" value=${name} placeholder="Character menu" maxlength="80"
          onInput=${(e) => setName(e.target.value)} />
      </label>` : null}
      ${d.mode !== 'edit' ? html`<label class="field">
        <span class="field-label">Slug <span class="field-help">lowercase, digits and dashes; part of the path agents type</span></span>
        <input class=${cx('input mono', !slugOk && (slugTouched || name) && 'is-invalid')} type="text" value=${effSlug}
          placeholder="character-menu" maxlength="48" data-autofocus=${d.mode === 'move' ? true : null}
          onInput=${(e) => { setSlugTouched(true); setSlug(e.target.value.toLowerCase()); }} />
      </label>` : null}
      ${d.mode !== 'move' ? html`<div class="field">
        <span class="field-label">Icon</span>
        <${IconGrid} value=${icon} onPick=${setIcon} />
      </div>
      <label class="field">
        <span class="field-label">Description <span class="field-help">optional</span></span>
        <input class="input" type="text" value=${description} placeholder="What belongs here" maxlength="200"
          onInput=${(e) => setDescription(e.target.value)} />
      </label>` : null}
    </form>
  </${Dialog}>`;
}

export function IconGrid({ value, onPick }) {
  return html`<div class="icon-grid" role="radiogroup" aria-label="Icon">
    <button type="button" role="radio" aria-checked=${String(!value)} class=${cx('icon-cell', !value && 'is-on')} title="Default icon" onClick=${() => onPick(null)}>
      <span class="muted small">auto</span></button>
    ${ICON_CHOICES.map((n) => html`<button type="button" role="radio" aria-checked=${String(value === n)} key=${n}
      class=${cx('icon-cell', value === n && 'is-on')} title=${n} aria-label=${n} onClick=${() => onPick(n)}>
      <${Icon} name=${n} size=${16} /></button>`)}
  </div>`;
}

/**
 * Searchable topic picker in a popover.
 * opts: {value, onPick(path|null), allowNone, noneLabel, multi, values, onToggle, exclude}
 */
export function openTopicPicker(anchor, opts) {
  const flat = state.topicFlat.filter((n) => !opts.exclude || (n.path !== opts.exclude && !n.path.startsWith(opts.exclude + '/')));
  const items = [];
  if (opts.allowNone) items.push({ value: '', label: opts.noneLabel || 'No topic', icon: 'layers', checked: !opts.multi && !opts.value });
  flat.forEach((n) => items.push({
    value: n.path, label: n.name, search: n.path, depth: n.depth,
    icon: topicIconName(n), title: n.path,
    checked: opts.multi ? (opts.values || []).includes(n.path) : opts.value === n.path,
  }));
  const content = html`<${TopicPickerMenu} items=${items} opts=${opts} />`;
  openPopover(anchor, content, { width: 300, cls: 'is-tall' });
}

function TopicPickerMenu({ items, opts }) {
  const [values, setValues] = useState(opts.values || []);
  const view = opts.multi ? items.map((it) => Object.assign({}, it, { checked: values.includes(it.value) })) : items;
  return html`<${Menu} items=${view} search placeholder="Find a topic…" multi=${!!opts.multi}
    emptyText="No topic matches. Add one from the sidebar."
    onSelect=${(v) => {
      if (opts.multi) {
        const next = values.includes(v) ? values.filter((x) => x !== v) : [...values, v];
        setValues(next);
        opts.onToggle(next);
      } else {
        opts.onPick(v || null);
      }
    }} />`;
}

