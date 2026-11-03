// Team (#/team): who is on the board, what each agent holds, and the profiles they join with.

import { html, useState, useEffect } from './lib.js';
import { state, set, mutate, loadProfiles, loadTeam, toast, issueHref } from './state.js';
import {
  Icon, Avatar, RoleBadge, CliBadge, PresenceDot, StatusIcon, PriorityIcon, Empty, Spinner, confirmDialog, Age, useAutosize,
} from './components.js';
import { openTopicPicker, IconGrid } from './topics.js';
import { ROLE, ROLE_KEYS, PRESENCE } from './meta.js';
import { cx, ago, absTime, SLUG_RE, plural } from './util.js';

export function TeamPage() {
  const agents = state.team;
  const order = { active: 0, idle: 1, offline: 2 };
  const sorted = [...agents].sort((a, b) => (a.kind === 'human' ? -1 : b.kind === 'human' ? 1 : 0) || order[a.presence] - order[b.presence] || a.handle.localeCompare(b.handle));
  const counts = { active: 0, idle: 0, offline: 0 };
  agents.forEach((a) => { if (a.kind !== 'human') counts[a.presence] = (counts[a.presence] || 0) + 1; });
  return html`<div class="team">
    <section class="team-section">
      <div class="team-head">
        <h2 class="h2">Agents</h2>
        <div class="team-summary">
          ${['active', 'idle', 'offline'].map((p) => html`<span class="team-sum" key=${p} title=${PRESENCE[p].hint}><${PresenceDot} presence=${p} />${counts[p] || 0} ${PRESENCE[p].label.toLowerCase()}</span>`)}
        </div>
      </div>
      ${agents.length ? html`<div class="agents">${sorted.map((a) => html`<${AgentCard} key=${a.handle} a=${a} />`)}</div>`
        : html`<${Empty} icon="users" title="No agents have joined yet">An agent joins with <code>earmark join worker --cli claude-code</code>.</${Empty}>`}
    </section>
    <${Profiles} />
  </div>`;
}

function AgentCard({ a }) {
  const human = a.kind === 'human';
  const profile = state.profiles.find((p) => p.slug === a.profile);
  const groups = [
    ['Working on', 'hammer', a.working || []],
    ['Reviewing', 'scan-eye', a.reviewing || []],
    ['Assigned', 'inbox', (a.assigned || []).filter((x) => !(a.working || []).some((w) => w.key === x.key))],
  ].filter(([, , list]) => list.length);
  return html`<article class=${cx('agent', 'pr-card-' + a.presence)}>
    <header class="agent-head">
      <${Avatar} handle=${a.handle} size=${36} />
      <div class="agent-id">
        <div class="agent-handle">${a.handle}${human ? html`<span class="role role-owner">Owner</span>` : null}</div>
        <div class="agent-sub">
          <${PresenceDot} presence=${a.presence} withLabel />
          <span class="muted" title=${'Last seen ' + absTime(a.last_seen)}>· seen ${ago(a.last_seen)}</span>
        </div>
      </div>
      <${CliBadge} cli=${a.cli} />
    </header>
    ${!human ? html`<div class="agent-profile">
      <${RoleBadge} role=${a.role} />
      ${a.profile ? html`<span class="agent-prof" title="Profile">${profile && profile.icon ? html`<${Icon} name=${profile.icon} size=${13} />` : null}${profile ? profile.name : a.profile}<span class="mono muted">${a.profile}</span></span>` : html`<span class="muted small">no profile</span>`}
    </div>` : null}
    ${groups.length ? groups.map(([label, icon, list]) => html`<div class="agent-group" key=${label}>
      <div class="agent-group-label"><${Icon} name=${icon} size=${12} />${label}<span class="muted">${list.length}</span></div>
      ${list.slice(0, 4).map((it) => html`<a class="agent-issue" href=${issueHref(it.key)} key=${it.key}>
        <${StatusIcon} status=${it.status} size=${14} /><span class="mono muted small">${it.key}</span>
        <span class="agent-issue-title">${it.title}</span>
        ${it.stale ? html`<span class="flag flag-stale is-icon" title="Stale lease"><${Icon} name="hourglass" size=${11} /></span>` : null}
        <${PriorityIcon} priority=${it.priority} size=${13} />
      </a>`)}
      ${list.length > 4 ? html`<div class="muted small agent-more">+${list.length - 4} more</div>` : null}
    </div>`) : html`<div class="agent-idle">${human ? 'Steering the board from this page.' : a.presence === 'offline' ? 'Offline. Its work went back to the queue.' : 'Nothing claimed. Next: earmark next'}</div>`}
  </article>`;
}

// ---------------------------------------------------------------- profiles

function Profiles() {
  const [sel, setSelRaw] = useState(null);
  const [rev, setRev] = useState(0);
  const setSel = (v) => { setSelRaw(v); setRev((r) => r + 1); };
  const profiles = state.profiles;
  useEffect(() => { if (!state.profilesLoaded) loadProfiles().catch(() => null); }, []);
  const current = sel === '__new' ? null : profiles.find((p) => p.slug === sel) || null;
  return html`<section class="team-section">
    <div class="team-head">
      <h2 class="h2">Profiles</h2>
      <span class="muted small">Agents join with a profile: it sets their role, prompt and topic scope.</span>
      <span class="top-spacer"></span>
      <button type="button" class="btn btn-sm" onClick=${() => setSel('__new')}><${Icon} name="plus" size=${14} />New profile</button>
    </div>
    ${!state.profilesLoaded ? html`<div class="list-loading"><${Spinner} /> Loading profiles…</div>` : html`<div class="profiles">
      <div class="profile-list" role="listbox" aria-label="Profiles">
        ${profiles.map((p) => html`<button type="button" role="option" aria-selected=${String(sel === p.slug)} key=${p.slug}
          class=${cx('profile-row', sel === p.slug && 'is-on')} onClick=${() => setSel(p.slug)}>
          <span class="profile-ico"><${Icon} name=${p.icon || (ROLE[p.role] || {}).icon || 'user'} size=${16} /></span>
          <span class="profile-txt"><span class="profile-name">${p.name}</span><span class="mono muted small">${p.slug}</span></span>
          <${RoleBadge} role=${p.role} />
          <span class="profile-agents" title=${(p.agents || []).length ? 'Used by ' + p.agents.join(', ') : 'No agent uses it yet'}>${(p.agents || []).length ? plural(p.agents.length, 'agent') : ''}</span>
        </button>`)}
        ${!profiles.length ? html`<div class="section-empty">No profiles. Create one so agents can join.</div>` : null}
      </div>
      <div class="profile-editor">
        ${sel ? html`<${ProfileEditor} key=${sel + ':' + rev + ':' + (current ? current.version : '')} p=${current} onDone=${(slug) => setSel(slug)} />`
          : html`<${Empty} icon="clipboard-list" title="Select a profile">Edit its prompt, role and topic scope, or create a new one.</${Empty}>`}
      </div>
    </div>`}
  </section>`;
}

function ProfileEditor({ p, onDone }) {
  const isNew = !p;
  const [slug, setSlug] = useState('');
  const [name, setName] = useState(p ? p.name : '');
  const [role, setRole] = useState(p ? p.role : 'worker');
  const [icon, setIcon] = useState(p ? p.icon : null);
  const [topics, setTopics] = useState(p ? p.topics || [] : []);
  const [prompt, setPrompt] = useState(p ? p.prompt || '' : '');
  const [busy, setBusy] = useState(false);
  const promptRef = useAutosize(prompt, 6, 24);
  const dirty = isNew || name !== p.name || role !== p.role || icon !== p.icon || prompt !== (p.prompt || '') || JSON.stringify(topics) !== JSON.stringify(p.topics || []);
  const slugOk = SLUG_RE.test(slug) && slug.length <= 32;

  const save = async () => {
    setBusy(true);
    let res;
    if (isNew) {
      res = await mutate('POST', '/profiles', { slug, name: name.trim() || slug, role, icon: icon || undefined, prompt, topics });
    } else {
      res = await mutate('PATCH', `/profiles/${encodeURIComponent(p.slug)}`, { version: p.version, name: name.trim() || p.slug, role, icon, prompt, topics });
    }
    setBusy(false);
    if (res) {
      toast({ kind: 'success', title: isNew ? `Created profile ${res.slug}` : `Saved ${res.slug}` });
      await loadProfiles();
      loadTeam().catch(() => null);
      onDone(res.slug);
    }
  };
  const remove = () => confirmDialog({
    title: `Delete profile ${p.slug}?`,
    body: (p.agents || []).length ? `${plural(p.agents.length, 'agent')} (${p.agents.join(', ')}) use it and will keep working without a profile.` : 'No agents use it.',
    confirm: 'Delete', danger: true,
    onConfirm: async () => {
      if (await mutate('DELETE', `/profiles/${encodeURIComponent(p.slug)}`)) {
        toast({ kind: 'success', title: `Deleted ${p.slug}` });
        await loadProfiles();
        onDone(null);
      }
    },
  });
  return html`<form class="form profile-form" onSubmit=${(e) => { e.preventDefault(); if (dirty && (!isNew || slugOk)) save(); }}>
    <div class="form-row">
      ${isNew ? html`<label class="field">
        <span class="field-label">Slug <span class="field-help">what agents type: earmark join ${slug || 'SLUG'}</span></span>
        <input class=${cx('input mono', slug && !slugOk && 'is-invalid')} type="text" value=${slug} maxlength="32" placeholder="ui-lead" data-autofocus
          onInput=${(e) => setSlug(e.target.value.toLowerCase())} />
      </label>` : html`<div class="field"><span class="field-label">Slug</span><div class="static mono">${p.slug} <span class="muted small">· v${p.version}</span></div></div>`}
      <label class="field">
        <span class="field-label">Name</span>
        <input class="input" type="text" value=${name} maxlength="80" placeholder="UI lead" onInput=${(e) => setName(e.target.value)} />
      </label>
    </div>
    <div class="field">
      <span class="field-label">Role</span>
      <div class="seg" role="radiogroup" aria-label="Role">
        ${ROLE_KEYS.map((r) => html`<button type="button" role="radio" aria-checked=${String(role === r)} key=${r}
          class=${cx('seg-btn', 'role-seg-' + r, role === r && 'is-on')} onClick=${() => setRole(r)}>
          <${Icon} name=${ROLE[r].icon} size=${14} /><span>${ROLE[r].label}</span></button>`)}
      </div>
      <span class="field-help">${{ worker: 'Claims todo work with earmark next and hands off for review.', reviewer: 'Takes the review queue with earmark next --review; approves or rejects.', pm: 'Triages, assigns and may make any status move.' }[role]}</span>
    </div>
    <div class="field">
      <span class="field-label">Topic scope <span class="field-help">empty = the whole board; each topic covers its subtree</span></span>
      <div class="scope-edit">
        ${topics.map((t) => html`<span class="chip topic-chip" key=${t} title=${t}><${Icon} name="folder" size=${12} /><span class="chip-txt mono">${t}</span>
          <button type="button" class="chip-x" aria-label=${'Remove ' + t} onClick=${() => setTopics(topics.filter((x) => x !== t))}><${Icon} name="x" size=${12} /></button></span>`)}
        ${!topics.length ? html`<span class="muted small">Whole board</span>` : null}
        <button type="button" class="chip chip-add" onClick=${(e) => openTopicPicker(e.currentTarget, { multi: true, values: topics, onToggle: setTopics })}>
          <${Icon} name="plus" size=${12} />Choose topics</button>
      </div>
    </div>
    <div class="field">
      <span class="field-label">Icon</span>
      <${IconGrid} value=${icon} onPick=${setIcon} />
    </div>
    <label class="field">
      <span class="field-label">Prompt <span class="field-help">printed to the agent by earmark join</span></span>
      <textarea class="textarea mono prompt-input" rows="6" value=${prompt} ref=${promptRef} placeholder="You own the player-facing UI. Match the style guide…"
        onInput=${(e) => setPrompt(e.target.value)}></textarea>
    </label>
    ${!isNew ? html`<div class="field"><span class="field-label">Agents using it</span>
      <div class="scope-edit">${(p.agents || []).length ? p.agents.map((h) => html`<span class="chip" key=${h}><${Avatar} handle=${h} size=${16} presence=${false} /><span class="chip-txt">${h}</span></span>`) : html`<span class="muted small">None yet</span>`}</div>
    </div>` : null}
    <div class="form-actions">
      ${!isNew ? html`<button type="button" class="btn btn-ghost tone-red" onClick=${remove}><${Icon} name="trash-2" size=${14} />Delete</button>` : null}
      <span class="top-spacer"></span>
      <button type="button" class="btn" onClick=${() => onDone(isNew ? null : p.slug)} disabled=${!dirty && !isNew}>${isNew ? 'Cancel' : 'Revert'}</button>
      <button type="submit" class="btn btn-primary" disabled=${busy || !dirty || (isNew && !slugOk)}>${isNew ? 'Create profile' : 'Save changes'}</button>
    </div>
  </form>`;
}
